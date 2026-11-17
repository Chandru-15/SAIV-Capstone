const { test, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { create, act } = require('react-test-renderer');
const axios = require('axios');
const navigation = { replace() {}, push() {} };
const navId = require.resolve('next/navigation');
require.cache[navId] = { id: navId, filename: navId, loaded: true, exports: { useRouter: () => navigation } };
const linkId = require.resolve('next/link');
require.cache[linkId] = { id: linkId, filename: linkId, loaded: true, exports: {
  __esModule: true, default: ({ children, ...props }) => React.createElement('a', props, children),
} };
global.window = {};
global.fetch = async () => Response.json({ apiUrl: 'http://localhost:8000', staffPortalUrl: 'http://localhost:8501' });
const { api } = require('../lib/api.ts');
const { saveTokens, clearAuthSession } = require('../lib/auth.ts');
const { getActiveSessions, getMyCourses, getMyCheckins } = require('../lib/student-api.ts');
const { useStudentData } = require('../hooks/useStudentData.ts');
const Dashboard = require('../app/dashboard/page.tsx').default;
const Profile = require('../app/profile/page.tsx').default;
const CheckInModal = require('../components/checkin/CheckInModal.tsx').default;
const FaceEnrollmentModal = require('../components/face/FaceEnrollmentModal.tsx').default;
const AvailableSessions = require('../components/student/AvailableSessions.tsx').default;
const AttendanceHistory = require('../components/student/AttendanceHistory.tsx').default;
const { deriveFacePolicy } = require('../lib/student-policy.ts');
const { existingBrowserFixture } = require('./device-fixture.cjs');

let renderer; let data; let calls; let records; let sessions; let courses; let actor; let respond;
const course = { id: 'course-1', code: 'SC3099', name: 'Capstone', require_face_recognition: false, require_device_binding: true };
const session = (id = 'session-open', changes = {}) => ({
  id, course_id: 'course-1', name: 'Current Lecture', status: 'active', session_type: 'lecture',
  scheduled_start: new Date(Date.now() - 3600000).toISOString(),
  checkin_opens_at: new Date(Date.now() - 60000).toISOString(), checkin_closes_at: new Date(Date.now() + 600000).toISOString(),
  require_face_match: false, require_liveness_check: false, ...changes,
});
const record = (id, sessionId = 'historic-shared', status = 'approved') => ({
  id: String(id), session_id: sessionId, student_id: 'student-1', status,
  checked_in_at: '2026-10-25T01:00:00', risk_score: 0.1, qr_code_verified: false,
});
const ok = (config, body) => ({ config, data: body, status: 200, statusText: 'OK', headers: {} });
const fail = (config, status, message) => { throw new axios.AxiosError('HTTP', undefined, config, undefined,
  { ...ok(config, { code: 'permission_denied', message, details: null }), status }); };
const mount = async (component, createNodeMock) => { await act(async () => { renderer = create(component, { createNodeMock }); }); };
const text = () => JSON.stringify(renderer.toJSON());
const button = (label) => renderer.root.findAllByType('button').find((item) => item.children.join('') === label || item.props['aria-label'] === label);
function Probe() { data = useStudentData(); return null; }

beforeEach(() => {
  window.localStorage = { getItem: () => 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' };
  const storage = new Map();
  global.sessionStorage = { getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) };
  saveTokens('access', 'refresh');
  calls = []; records = []; sessions = [session()]; courses = [course];
  actor = { id: 'student-1', email: 'student@example.com', full_name: 'Student', role: 'student',
    is_active: true, camera_consent: true, geolocation_consent: true, face_enrolled: true };
  respond = async (config) => {
    if (config.url === '/auth/me') return ok(config, actor);
    if (config.url === '/sessions/active') return ok(config, sessions);
    if (config.url === '/courses/me') return ok(config, courses);
    if (config.url === '/checkins/me') {
      const { offset, limit } = config.params;
      return ok(config, { items: records.slice(offset, offset + limit), offset, limit, total: records.length });
    }
    if (config.url.startsWith('/sessions/')) return ok(config, session(decodeURIComponent(config.url.slice('/sessions/'.length)), { name: 'Closed Lecture 1', status: 'closed' }));
    if (config.url === '/auth/logout') return ok(config, {});
    throw new Error(`Unexpected ${config.url}`);
  };
  api.defaults.adapter = async (config) => {
    // These regression cases start with an already registered browser. Device
    // calls are checked separately by device.test.cjs; retain the core call log.
    if (config.url === '/devices/my-devices') return ok(config, [existingBrowserFixture(actor.id)]);
    calls.push(config); return respond(config);
  };
});
afterEach(async () => { if (renderer) await act(async () => renderer.unmount()); renderer = undefined; });

test('array session/course contracts and paginated history retain their actual response shapes', async () => {
  assert.deepEqual(await getActiveSessions(), sessions);
  assert.deepEqual(await getMyCourses(), courses);
  records = [record('1')];
  const page = await getMyCheckins({ offset: 0, limit: 1 });
  assert.deepEqual(page, { items: records, total: 1, limit: 1, offset: 0 });
  assert.deepEqual(calls.map((item) => item.url), ['/sessions/active', '/courses/me', '/checkins/me']);
});

test('malformed arrays, envelopes and non-progressing pages are rejected defensively', async () => {
  respond = async (config) => ok(config, { items: [] });
  await assert.rejects(getActiveSessions(), /Session data/);
  await assert.rejects(getMyCourses(), /Course enrollment/);
  await assert.rejects(getMyCheckins(), /invalid data/);
  respond = async (config) => ok(config, { items: [], total: 20, offset: 0, limit: 50 });
  await assert.rejects(getMyCheckins(), /changed while loading/);
});

test('empty and single-page attendance history have correct completion and display states', async () => {
  await mount(React.createElement(Probe));
  assert.equal(data.history.complete, true); assert.equal(data.history.total, 0);
  assert.equal(data.historyLoading, false); assert.equal(data.historyError, '');
  records = [record('1')];
  await act(async () => data.refreshHistory());
  assert.equal(data.history.complete, true); assert.equal(data.history.items.length, 1);
  assert.equal(data.labels['historic-shared'], 'Closed Lecture 1');
  await act(async () => data.loadMore());
  assert.equal(calls.filter((item) => item.url === '/checkins/me').length, 2, 'no request beyond a completed history');
});

test('more than 50 records load across pages with complete metadata and cached closed-session labels', async () => {
  records = Array.from({ length: 75 }, (_, index) => record(index));
  await mount(React.createElement(Probe));
  assert.equal(data.history.items.length, 50); assert.equal(data.history.total, 75);
  assert.equal(data.history.complete, false); assert.equal(data.history.nextOffset, 50);
  await act(async () => Promise.all([data.loadMore(), data.loadMore()]));
  assert.equal(data.history.items.length, 75); assert.equal(data.history.complete, true);
  assert.equal(new Set(data.history.items.map((item) => item.id)).size, 75);
  assert.deepEqual(calls.filter((item) => item.url === '/checkins/me').map((item) => item.params.offset), [0, 50]);
  assert.equal(calls.filter((item) => item.url === '/sessions/historic-shared').length, 1);
});

test('overlapping history pages do not duplicate records and visibly request a refresh if totals diverge', async () => {
  records = Array.from({ length: 60 }, (_, index) => record(index));
  const normal = respond;
  respond = async (config) => {
    if (config.url === '/checkins/me' && config.params.offset === 50) {
      return ok(config, { items: [records[49], ...records.slice(50, 59)], total: 60, limit: 50, offset: 50 });
    }
    return normal(config);
  };
  await mount(React.createElement(Probe));
  await act(async () => data.loadMore());
  assert.equal(data.history.items.length, 59);
  assert.equal(new Set(data.history.items.map((item) => item.id)).size, 59);
  assert.match(data.historyError, /changed while loading/);
});

test('history labels only request own session IDs, with at most three concurrent detail lookups', async () => {
  records = Array.from({ length: 12 }, (_, index) => record(index, `own-session-${index}`));
  const normal = respond;
  let active = 0; let maximum = 0;
  respond = async (config) => {
    if (config.url.startsWith('/sessions/own-session-')) {
      active++; maximum = Math.max(maximum, active);
      await new Promise((resolve) => setTimeout(resolve, 2)); active--;
    }
    return normal(config);
  };
  await mount(React.createElement(Probe));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 80)); });
  assert.ok(maximum <= 3); assert.ok(maximum > 0);
  assert.equal(Object.keys(data.labels).length, 12);
  assert.ok(calls.filter((item) => item.url.startsWith('/sessions/') && item.url !== '/sessions/active')
    .every((item) => item.url.startsWith('/sessions/own-session-')));
});

test('restricted historical metadata uses a safe fallback without clearing general 403 authentication', async () => {
  records = [record('1')];
  const normal = respond;
  respond = async (config) => config.url === '/sessions/historic-shared' ? fail(config, 403, 'Not permitted') : normal(config);
  await mount(React.createElement(Probe));
  assert.equal(data.labels['historic-shared'], 'Attendance session (details unavailable)');
  assert.equal(sessionStorage.getItem('saiv_access_token'), 'access');
});

test('rapid Load More keeps the detail-lookup concurrency bound across pages', async () => {
  records = Array.from({ length: 60 }, (_, index) => record(index, `own-session-${index}`));
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  let active = 0; let maximum = 0;
  const normal = respond;
  respond = async (config) => {
    if (config.url.startsWith('/sessions/own-session-')) {
      active++; maximum = Math.max(maximum, active);
      await gate; active--;
    }
    return normal(config);
  };
  await mount(React.createElement(Probe));
  await act(async () => data.loadMore());
  assert.equal(data.history.items.length, 60);
  assert.ok(maximum <= 3);
  await act(async () => release());
  assert.ok(maximum <= 3);
  assert.equal(Object.keys(data.labels).length, 60);
});

test('unenrolled courses are excluded and missing course policy never defaults to optional face matching', async () => {
  sessions = [session(), session('unrelated', { course_id: 'other-course' })];
  courses = [{ ...course, require_face_recognition: undefined }];
  await mount(React.createElement(Dashboard));
  assert.doesNotMatch(text(), /unrelated/);
  assert.match(text(), /Verification policy unavailable/);
  const action = button('Verification policy unavailable');
  assert.equal(action.props.disabled, true);
});

test('failed course retrieval leaves check-in unavailable and offers refresh', async () => {
  const normal = respond;
  respond = async (config) => config.url === '/courses/me' ? fail(config, 503, 'database internal secret') : normal(config);
  await mount(React.createElement(Dashboard));
  assert.match(text(), /required service is unavailable/);
  assert.doesNotMatch(text(), /database internal secret/);
  assert.ok(button('Refresh sessions'));
  assert.equal(button('Check In'), undefined);
});

test('duplicate beyond the first history page is found before any check-in modal opens', async () => {
  records = Array.from({ length: 70 }, (_, index) => record(index, index === 60 ? 'session-open' : 'historic-shared'));
  await mount(React.createElement(Dashboard));
  assert.equal(button('Check In').props.disabled, false);
  await act(async () => button('Check In').props.onClick());
  assert.match(text(), /already checked in to this session/);
  assert.equal(renderer.root.findAllByProps({ role: 'dialog' }).length, 0);
  assert.deepEqual(calls.filter((item) => item.url === '/checkins/me').map((item) => item.params.offset), [0, 50]);
});

test('approved, pending, flagged and rejected history preserve their actual statuses', async () => {
  const items = ['approved', 'pending', 'flagged', 'rejected'].map((status) => record(status, status, status));
  await mount(React.createElement(AttendanceHistory, { items, total: 4, complete: true, loading: false, error: '',
    labels: { approved: 'Closed approved session', pending: 'Pending session', flagged: 'Flagged session', rejected: 'Rejected session' },
    onLoadMore() {}, onRefresh() {} }));
  for (const status of ['approved', 'pending', 'flagged', 'rejected']) assert.match(text(), new RegExp(status));
  for (const status of ['approved', 'pending', 'flagged', 'rejected']) assert.match(text(), new RegExp(`tag-${status}`));
  assert.doesNotMatch(text(), /awaiting staff review|Contact course staff/);
});

test('Load More and empty/loading history states are understandable', async () => {
  let loaded = 0;
  const props = { items: [], total: 0, complete: true, loading: true, error: '', labels: {}, onLoadMore() { loaded++; }, onRefresh() {} };
  await mount(React.createElement(AttendanceHistory, props));
  assert.match(text(), /Loading attendance history/);
  await act(async () => renderer.update(React.createElement(AttendanceHistory, { ...props, loading: false })));
  assert.match(text(), /No attendance records/);
  await act(async () => renderer.update(React.createElement(AttendanceHistory, { ...props, items: [record('1')], total: 60, complete: false, loading: false })));
  assert.match(text(), /Showing .*1.* of .*60/);
  await act(async () => button('Load more attendance').props.onClick());
  assert.equal(loaded, 1);
});

test('course-only face matching requires enrollment on the dashboard and image capture in the modal', async () => {
  courses = [{ ...course, require_face_recognition: true }]; actor.face_enrolled = false;
  await mount(React.createElement(Dashboard));
  assert.match(text(), /Face match: .*Required/);
  assert.equal(button('Check In').props.disabled, true);
  await act(async () => renderer.unmount()); renderer = undefined;
  const policy = deriveFacePolicy(session(), courses[0]);
  await mount(React.createElement(CheckInModal, { user: { ...actor, face_enrolled: true }, session: session(), policy, onClose() {}, async onCompleted() {}, onUnauthorized() {} }));
  assert.match(text(), /Face verification/); assert.ok(button('Open Camera'));
});

test('missing modal policy never exposes camera, location or submit actions', async () => {
  await mount(React.createElement(CheckInModal, { user: actor, session: session(), policy: null, onClose() {}, async onCompleted() {}, onUnauthorized() {} }));
  assert.match(text(), /Verification policy is unavailable/);
  assert.equal(button('Open Camera'), undefined); assert.equal(button('Get Current Location'), undefined);
  assert.equal(button('Submit Check-in'), undefined);
});

test('click-time eligibility is reevaluated after a delayed duplicate lookup', async () => {
  const originalNow = Date.now;
  let clock = originalNow();
  Date.now = () => clock;
  const closes = clock + 200;
  let resolve; let begun = 0;
  const target = session('closing', { checkin_closes_at: new Date(closes).toISOString() });
  try {
    await mount(React.createElement(AvailableSessions, { sessions: [target], courses: [course], attempts: new Map(),
      loading: false, historyReady: true, error: '', user: actor, onRefresh() {},
      findExistingAttempt: () => new Promise((done) => { resolve = done; }), onBegin() { begun++; } }));
    await act(async () => button('Check In').props.onClick());
    await act(async () => { clock = closes + 1; resolve(null); });
    assert.equal(begun, 0); assert.match(text(), /Check-in window closed/);
  } finally { Date.now = originalNow; }
});

test('an unsettled check-in offers another attempt instead of locking the session', async () => {
  // The bug this covers: any existing record showed 'Already Checked In', so a
  // flagged attempt could never be retried from the session list.
  let began = null;
  const target = session('retryable-session');
  await mount(React.createElement(AvailableSessions, {
    sessions: [target], courses: [course],
    attempts: new Map([['retryable-session', 'retryable']]),
    loading: false, historyReady: true, error: '', user: actor, onRefresh() {},
    findExistingAttempt: async () => 'retryable',
    onBegin: (_session, _policy, retry) => { began = retry; },
  }));
  assert.ok(button('Try Check-in Again'), 'the card should offer another attempt');
  assert.equal(button('Already Checked In'), undefined);
  await act(async () => button('Try Check-in Again').props.onClick());
  assert.equal(began, true, 'the first submission must be sent as a retry');
});

test('a settled check-in still closes the session off', async () => {
  let began = false;
  const target = session('settled-session');
  await mount(React.createElement(AvailableSessions, {
    sessions: [target], courses: [course],
    attempts: new Map([['settled-session', 'settled']]),
    loading: false, historyReady: true, error: '', user: actor, onRefresh() {},
    findExistingAttempt: async () => 'settled',
    onBegin: () => { began = true; },
  }));
  assert.ok(button('Already Checked In'));
  assert.equal(began, false);
});

test('logout unmounts the protected profile and releases a pending camera grant', async () => {
  let grant; let stopped = 0;
  Object.defineProperty(global, 'navigator', { configurable: true, value: { mediaDevices: {
    getUserMedia: () => new Promise((resolve) => { grant = resolve; }),
  } } });
  await mount(React.createElement(Profile));
  // Open consent setup despite the test actor normally having consent.
  actor.camera_consent = false;
  await act(async () => renderer.unmount()); renderer = undefined;
  await mount(React.createElement(Profile));
  await act(async () => button('Set up camera').props.onClick());
  await act(async () => { void button('Request camera access').props.onClick(); });
  await act(async () => clearAuthSession());
  await act(async () => grant({ getTracks: () => [{ stop() { stopped++; } }] }));
  assert.equal(stopped, 1);
  assert.doesNotMatch(text(), /Account Setup|Enroll Face/);
});

test('duplicate scan is bounded and never invents a clear result for incomplete history', async () => {
  records = Array.from({ length: 2500 }, (_, index) => record(index));
  await mount(React.createElement(Probe));
  await act(async () => {
    await assert.rejects(data.findExistingAttempt('unseen-session'), /complete attendance history/);
  });
  assert.equal(calls.filter((item) => item.url === '/checkins/me').length, 21);
  assert.equal(data.history.items.length, 50, 'scan does not automatically render thousands of records');
});

test('load-more failure preserves existing rows and a subsequent retry resumes at the same offset', async () => {
  records = Array.from({ length: 55 }, (_, index) => record(index));
  const normal = respond;
  let failed = false;
  respond = async (config) => {
    if (config.url === '/checkins/me' && config.params.offset === 50 && !failed) {
      failed = true; throw new axios.AxiosError('offline', 'ERR_NETWORK', config);
    }
    return normal(config);
  };
  await mount(React.createElement(Probe));
  await act(async () => { await assert.rejects(data.loadMore()); });
  assert.equal(data.history.items.length, 50); assert.equal(data.history.nextOffset, 50);
  assert.match(data.historyError, /Unable to reach/);
  await act(async () => data.loadMore());
  assert.equal(data.history.items.length, 55); assert.equal(data.historyError, '');
});

test('refreshing history during a duplicate scan discards the previous scan response', async () => {
  records = Array.from({ length: 75 }, (_, index) => record(index, index === 60 ? 'late-session' : 'historic-shared'));
  const normal = respond;
  let finish;
  respond = async (config) => {
    if (config.url === '/checkins/me' && config.params.offset === 50 && config.params.limit === 100) {
      return new Promise((resolve) => { finish = () => resolve(ok(config, {
        items: records.slice(50), total: 75, offset: 50, limit: 100,
      })); });
    }
    return normal(config);
  };
  await mount(React.createElement(Probe));
  let check;
  await act(async () => { check = data.findExistingAttempt('late-session').catch((error) => error); });
  await act(async () => data.refreshHistory());
  await act(async () => finish());
  assert.match((await check).message, /history refreshed/);
  assert.equal(data.attempts.has('late-session'), false);
});

function captureEnvironment() {
  global.HTMLMediaElement = { HAVE_CURRENT_DATA: 2 };
  let stopped = 0;
  const stream = { getTracks: () => [{ stop() { stopped++; } }] };
  const video = { readyState: 2, videoWidth: 640, videoHeight: 480, srcObject: null, async play() {} };
  Object.defineProperty(global, 'navigator', { configurable: true, value: {
    mediaDevices: { async getUserMedia() { return stream; } },
    geolocation: { getCurrentPosition(done) { done({ timestamp: Date.now(), coords: { latitude: 1.3, longitude: 103.7, accuracy: 5 } }); } },
  } });
  global.document = { createElement: () => ({ width: 0, height: 0,
    getContext: () => ({ drawImage() {}, clearRect() {} }),
    toDataURL: () => 'data:image/jpeg;base64,synthetic-test-image',
  }) };
  window.crypto = { randomUUID: () => 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' };
  window.localStorage = { getItem: () => 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', setItem() {} };
  return { nodeMock: (element) => element.type === 'video' ? video : null, stopped: () => stopped };
}

test('face enrollment displays the actual safe 422 backend guidance', async () => {
  const environment = captureEnvironment();
  respond = async (config) => fail(config, 422, 'Image quality too low (0.45 < 0.5)');
  await mount(React.createElement(FaceEnrollmentModal, { onClose() {}, async onEnrolled() {}, onUnauthorized() {} }), environment.nodeMock);
  await act(async () => button('Open Camera').props.onClick());
  await act(async () => button('Capture and Enroll Face').props.onClick());
  assert.match(text(), /Image quality too low \(0.45 < 0.5\)/);
  assert.doesNotMatch(text(), /synthetic-test-image/);
  assert.equal(calls[0].url, '/users/me/face/enroll');
});

// Re-enrolment replaces the template every future check-in is matched against,
// so it is no longer the account holder's own call. A password proved the
// student was present, which for their own account they always are; authority
// is now an approved request an instructor or admin granted. The modal
// therefore collects nothing but the photo, and a missing approval is a 403
// the student can act on. These cases cover that from the UI side.
const reenrollment = { onClose() {}, async onEnrolled() {}, onUnauthorized() {}, mode: 're-enroll' };
const enrolled = (config) => ({ ...ok(config, { success: true, message: 'Replaced',
  face_enrolled: true, quality_score: 0.91, previous_template_replaced: true }), status: 200 });
const passwordField = () => renderer.root.findAllByType('input').find((item) => item.props.type === 'password');

test('re-enrolment sends the new image and nothing else', async () => {
  const environment = captureEnvironment();
  respond = async (config) => enrolled(config);
  await mount(React.createElement(FaceEnrollmentModal, reenrollment), environment.nodeMock);
  await act(async () => button('Open Camera').props.onClick());
  await act(async () => button('Capture and Replace Template').props.onClick());
  assert.equal(calls[0].url, '/users/me/face');
  assert.equal(calls[0].method, 'put');
  const payload = JSON.parse(calls[0].data);
  assert.equal(payload.image, 'synthetic-test-image');
  // No password, and no approval id either: the server resolves the caller's
  // own unspent approval, so a client that could name one could name another
  // student's.
  assert.equal(payload.password, undefined);
  assert.equal(Object.keys(payload).length, 1);
  assert.match(text(), /Face template replaced/);
});

test('re-enrolment asks for no password at all', async () => {
  const environment = captureEnvironment();
  respond = async (config) => enrolled(config);
  await mount(React.createElement(FaceEnrollmentModal, reenrollment), environment.nodeMock);
  assert.equal(passwordField(), undefined);
  assert.doesNotMatch(text(), /password/i);
});

test('re-enrolment needs no field filled in before the camera opens', async () => {
  const environment = captureEnvironment();
  respond = async (config) => enrolled(config);
  await mount(React.createElement(FaceEnrollmentModal, reenrollment), environment.nodeMock);
  await act(async () => button('Open Camera').props.onClick());
  await act(async () => button('Capture and Replace Template').props.onClick());
  // Previously an empty password stopped this before the network. The gate has
  // moved to the server, which holds the approval.
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/users/me/face');
});

test('a missing approval shows the backend reason and keeps the session', async () => {
  const environment = captureEnvironment();
  let signedOut = 0;
  respond = async (config) => fail(config, 403,
    'Re-enrolment needs an approved request. Ask your instructor to approve one, then try again.');
  await mount(React.createElement(FaceEnrollmentModal,
    { ...reenrollment, onUnauthorized() { signedOut++; } }), environment.nodeMock);
  await act(async () => button('Open Camera').props.onClick());
  await act(async () => button('Capture and Replace Template').props.onClick());
  assert.match(text(), /needs an approved request/);
  // 403 rather than 401 precisely so an unapproved attempt does not end the
  // session: the token is valid, only this one act is refused.
  assert.equal(signedOut, 0);
  assert.ok(button('Capture and Replace Template'), 'a second attempt is still possible');
  assert.doesNotMatch(text(), /synthetic-test-image/);
});

test('first-time enrollment asks for no password and sends none', async () => {
  const environment = captureEnvironment();
  respond = async (config) => enrolled(config);
  await mount(React.createElement(FaceEnrollmentModal,
    { onClose() {}, async onEnrolled() {}, onUnauthorized() {} }), environment.nodeMock);
  assert.equal(passwordField(), undefined);
  await act(async () => button('Open Camera').props.onClick());
  await act(async () => button('Capture and Enroll Face').props.onClick());
  assert.equal(calls[0].url, '/users/me/face/enroll');
  assert.equal(JSON.parse(calls[0].data).password, undefined);
});

for (const verdict of ['approved', 'flagged', 'rejected']) {
  test(`passive course-required image capture is preserved and displays server verdict ${verdict}`, async () => {
    const environment = captureEnvironment();
    const target = session('session-open', { require_liveness_check: false });
    let completed;
    respond = async (config) => ({ ...ok(config, { ...record('new', 'session-open', verdict), liveness_passed: verdict !== 'rejected' }), status: 201 });
    await mount(React.createElement(CheckInModal, { user: actor, session: target,
      policy: deriveFacePolicy(target, { ...course, require_face_recognition: true }),
      onClose() {}, async onCompleted(value) { completed = value; }, onUnauthorized() {} }), environment.nodeMock);
    await act(async () => button('Get Current Location').props.onClick());
    await act(async () => button('Open Camera').props.onClick());
    await act(async () => button('Submit Check-in').props.onClick());
    const payload = JSON.parse(calls[0].data);
    assert.equal(payload.image, 'synthetic-test-image'); assert.equal(payload.liveness_challenge_type, undefined);
    assert.equal(payload.location_accuracy_meters, 5);
    assert.equal(payload.challenge_frames, undefined); assert.equal(payload.liveness_passed, undefined);
    assert.equal(completed.status, verdict); assert.match(text(), new RegExp(verdict));
    assert.ok(environment.stopped() >= 1);
  });
}
