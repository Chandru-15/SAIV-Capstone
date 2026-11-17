const { test } = require('node:test');
const assert = require('node:assert/strict');
const axios = require('axios');
const { parseApiError, ClientDataError } = require('../lib/api-error.ts');
const { parseUtcTimestamp, formatDateTime, deriveFacePolicy, sessionEligibility } = require('../lib/student-policy.ts');
const { mergeCheckins } = require('../lib/attendance.ts');
const now = Date.parse('2026-10-25T02:00:00Z');
const session = { id: 'session-1', course_id: 'course-1', name: 'Lecture', status: 'active',
  checkin_opens_at: '2026-10-25T01:00:00', checkin_closes_at: '2026-10-25T03:00:00Z',
  scheduled_start: '2030-01-01T00:00:00Z', scheduled_end: '2030-01-01T01:00:00Z',
  require_face_match: false, require_liveness_check: false };
const course = { id: 'course-1', code: 'SC3099', name: 'Capstone', require_face_recognition: false };
const http = (status, data, extra = {}) => new axios.AxiosError('Do not display this transport message', undefined,
  { headers: {}, ...extra }, undefined, { status, data, headers: {}, config: {}, statusText: '' });

for (const status of [400, 401, 403, 404, 409, 422, 429, 500, 503]) {
  test(`HTTP ${status} has safe, useful fallback guidance`, () => {
    const result = parseApiError(http(status, '<html>internal stack</html>'));
    assert.equal(result.kind, 'http'); assert.equal(result.status, status);
    assert.ok(result.message.length > 15); assert.doesNotMatch(result.message, /internal stack|transport message/);
  });
}

test('domain and FastAPI error formats preserve safe messages without exposing arbitrary details', () => {
  assert.equal(parseApiError(http(400, { code: 'business_rule_violation', message: 'You have already checked in to this session',
    details: { private: 'never-display-this' } })).message, 'You have already checked in to this session');
  assert.equal(parseApiError(http(403, { detail: 'You are not enrolled in this course' })).message, 'You are not enrolled in this course');
  assert.equal(parseApiError(http(422, { message: 'Image quality too low (0.45 < 0.5)' })).message, 'Image quality too low (0.45 < 0.5)');
  assert.equal(parseApiError(http(422, { detail: [{ msg: 'Please supply a valid email address', input: 'private-input', ctx: { error: 'private' } },
    { msg: 'Password must contain at least eight characters' }] })).message,
    'Please supply a valid email address Password must contain at least eight characters');
});

test('validation and domain messages never reflect credentials, biometric values, traces or secrets', () => {
  const config = { data: JSON.stringify({ password: 'private-password', image: 'private-image-data',
    challenge_frames: ['private-frame-data'], refresh_token: 'private-refresh', qr_code_secret: 'private-qr-code' }),
    headers: { Authorization: 'Bearer private-access' } };
  for (const sensitive of ['private-password', 'private-image-data', 'private-frame-data', 'private-refresh', 'private-qr-code',
    'private-access', 'Traceback stack trace', 'token=leaked', 'data:image/jpeg;base64,AAAA', '<script>bad</script>']) {
    for (const body of [{ message: `Invalid input: ${sensitive}` }, { detail: [{ msg: `Invalid input: ${sensitive}`, input: sensitive }] }]) {
      const result = parseApiError(http(422, body, config));
      assert.ok(!result.message.includes(sensitive));
    }
  }
  assert.doesNotMatch(parseApiError(new Error('private-password')).message, /private-password/);
});

test('timeouts, cancellation, connection failures and malformed errors are distinguishable', () => {
  assert.equal(parseApiError(new axios.AxiosError('private', 'ECONNABORTED')).kind, 'timeout');
  assert.equal(parseApiError(new axios.AxiosError('private', 'ETIMEDOUT')).kind, 'timeout');
  assert.deepEqual(parseApiError(new axios.CanceledError('private')), { kind: 'cancelled', message: '' });
  assert.equal(parseApiError(new DOMException('private', 'AbortError')).kind, 'cancelled');
  assert.equal(parseApiError(new axios.AxiosError('private', 'ERR_NETWORK')).kind, 'network');
  for (const error of [undefined, null, 'private-data', { message: 'private-data' }]) {
    assert.equal(parseApiError(error).kind, 'unexpected');
    assert.doesNotMatch(parseApiError(error).message, /private-data/);
  }
  assert.equal(parseApiError(new ClientDataError('Course policy unavailable. Please refresh.')).message,
    'Course policy unavailable. Please refresh.');
});

test('429 retains a numeric Retry-After without rendering a raw header', () => {
  const error = http(429, { detail: 'Too many requests' });
  error.response.headers['retry-after'] = '30';
  assert.equal(parseApiError(error).retryAfterSeconds, 30);
});

for (const courseRequired of [false, true]) for (const sessionRequired of [false, true]) {
  test(`face policy combines course=${courseRequired}, session=${sessionRequired}`, () => {
    assert.deepEqual(deriveFacePolicy({ ...session, require_face_match: sessionRequired },
      { ...course, require_face_recognition: courseRequired }), {
      faceMatchRequired: courseRequired || sessionRequired, imageRequired: courseRequired || sessionRequired,
    });
  });
}

test('liveness still requires passive image capture independently of face-match policy', () => {
  assert.deepEqual(deriveFacePolicy({ ...session, require_liveness_check: true }, course),
    { faceMatchRequired: false, imageRequired: true });
});

test('missing course, wrong course and malformed policy fail closed', () => {
  for (const value of [undefined, { ...course, id: 'unrelated' }, { ...course, require_face_recognition: undefined }]) {
    assert.equal(deriveFacePolicy(session, value), null);
  }
  assert.equal(sessionEligibility(session, null, new Map(), now).eligible, false);
  assert.equal(sessionEligibility(session, [], new Map(), now).eligible, false);
  assert.equal(sessionEligibility(session, [{ ...course, require_face_recognition: undefined }], new Map(), now).eligible, false);
});

for (const [name, changes, eligible] of [
  ['active and open', {}, true], ['expired active', { checkin_closes_at: '2026-10-25T01:59:59Z' }, false],
  ['future active', { checkin_opens_at: '2026-10-25T02:00:01Z' }, false],
  ['scheduled', { status: 'scheduled' }, false], ['closed', { status: 'closed' }, false],
  ['cancelled', { status: 'cancelled' }, false], ['absent time', { checkin_opens_at: undefined }, false],
  ['invalid time', { checkin_closes_at: 'not-a-date' }, false],
  ['reversed window', { checkin_closes_at: '2026-10-25T00:00:00Z' }, false],
]) {
  test(`session eligibility: ${name}`, () => {
    assert.equal(sessionEligibility({ ...session, ...changes }, [course], new Map(), now).eligible, eligible);
  });
}

test('an existing check-in blocks new attempts regardless of attendance verdict', () => {
  for (const status of ['approved', 'pending', 'flagged', 'rejected', 'appealed']) {
    const records = [{ id: status, session_id: session.id, status }];
    const result = sessionEligibility(session, [course], new Map(records.map((item) => [item.session_id, 'settled'])), now);
    assert.equal(result.eligible, false); assert.equal(result.reason, 'Already Checked In');
  }
});

test('UTC, naive UTC, explicit Singapore offsets and inclusive server boundaries agree', () => {
  assert.equal(parseUtcTimestamp('2026-10-25T02:00:00'), now);
  assert.equal(parseUtcTimestamp('2026-10-25T10:00:00+08:00'), now);
  assert.equal(parseUtcTimestamp('2026-10-25T02:00:00.123456Z'), now + 123);
  const open = parseUtcTimestamp(session.checkin_opens_at);
  const close = parseUtcTimestamp(session.checkin_closes_at);
  assert.equal(sessionEligibility(session, [course], new Map(), open).eligible, true);
  assert.equal(sessionEligibility(session, [course], new Map(), close).eligible, true);
  assert.equal(sessionEligibility(session, [course], new Map(), open - 1).eligible, false);
  assert.equal(sessionEligibility(session, [course], new Map(), close + 1).eligible, false);
  assert.match(formatDateTime('2026-10-25T02:00:00Z'), /10:00/);
});

test('invalid and absent timestamps cannot silently become eligible dates', () => {
  for (const value of [undefined, null, '', '2026-02-30T02:00:00Z', '2026-10-25T24:00:00Z', '2026-13-25T02:00:00Z']) {
    assert.equal(parseUtcTimestamp(value), null);
  }
  assert.equal(formatDateTime('bad'), 'Time unavailable');
});

test('page merging preserves order and updates existing IDs without duplicates', () => {
  const result = mergeCheckins([{ id: '1', status: 'pending' }, { id: '2', status: 'approved' }],
    [{ id: '2', status: 'approved' }, { id: '1', status: 'flagged' }, { id: '3', status: 'rejected' }]);
  assert.deepEqual(result.map((item) => item.id), ['1', '2', '3']);
  assert.equal(result[0].status, 'flagged');
});
