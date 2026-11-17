const { test, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { create, act } = require('react-test-renderer');
const axios = require('axios');
let decodedQr;
const qrId = require.resolve('jsqr');
require.cache[qrId] = { id: qrId, filename: qrId, loaded: true, exports: () => decodedQr ? { data: decodedQr } : null };
global.window = {};
global.fetch = async () => Response.json({ apiUrl: 'http://localhost:8000', staffPortalUrl: 'http://localhost:8501' });
const { api } = require('../lib/api.ts');
const { saveTokens, saveUser } = require('../lib/auth.ts');
const { markCheckin, pendingCheckin } = require('../lib/checkin-attempt.ts');
const { requestLivenessChallenge, findSessionCheckin } = require('../lib/student-api.ts');
const { validateChallenge, captureLivenessFrames, capturePlan } = require('../lib/liveness.ts');
const CheckInModal = require('../components/checkin/CheckInModal.tsx').default;
const { existingBrowserFixture } = require('./device-fixture.cjs');
const actor = { id: 'student', role: 'student', is_active: true, camera_consent: true, geolocation_consent: true, face_enrolled: true };

let renderer, calls, respond, issued, target, listeners, tracksStopped, cameraCalls, framesEncoded, frameFrozen;
let video, positionTime, completed, raf, ended, storageWrites;
const record = (status = 'approved', sessionId = 'active-batch3') => ({
  id: 'attendance', session_id: sessionId, student_id: 'student', status, checked_in_at: new Date().toISOString(),
  risk_score: 0.12, qr_code_verified: false, liveness_passed: status === 'approved',
});
const challenge = (changes = {}) => ({ challenge_id: 'server-issued-id', challenge_type: 'blink',
  instruction: 'Look at the camera and blink once, slowly.', min_frames: 3, recommended_frames: 8,
  max_frames: 30, expires_at: new Date(Date.now() + 60000).toISOString(), ...changes });
const ok = (config, data) => ({ config, data, status: 201, statusText: 'Created', headers: {} });
const fail = (config, status, message) => { throw new axios.AxiosError('HTTP', undefined, config, undefined,
  { ...ok(config, { code: status === 422 ? 'validation_error' : 'business_rule_violation', message, details: null }), status }); };
const text = () => {
  const visit = (node) => typeof node === 'string' ? node : Array.isArray(node) ? node.map(visit).join(' ') : visit(node?.children ?? []);
  return visit(renderer.toJSON()).replace(/\s+/g, ' ').trim();
};
const button = (label) => renderer.root.findAllByType('button').find((item) => item.children.join('') === label);
const click = async (label) => { const action = button(label); assert.ok(action, label); assert.notEqual(action.props.disabled, true, `${label} enabled`);
  await act(async () => action.props.onClick()); };
const mount = async (session = target) => {
  await act(async () => { renderer = create(React.createElement(CheckInModal, { user: actor, session,
    policy: { faceMatchRequired: false, imageRequired: true }, onClose() { renderer.unmount(); },
    async onCompleted(value) { completed = value; }, onUnauthorized() {} }),
  { createNodeMock: (element) => element.type === 'video' ? video : null }); });
};
const ready = async () => { await mount(); await click('Get Current Location'); await click('Open Camera'); };
function fakeTime(t) { t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'], now: Date.now() }); }
async function advance(t, milliseconds) {
  for (let elapsed = 0; elapsed < milliseconds; elapsed += 25) {
    await act(async () => t.mock.timers.tick(Math.min(25, milliseconds - elapsed)));
  }
}
async function captured(t) {
  await click('Request liveness challenge'); await click('Capture challenge'); await advance(t, 5500);
  assert.match(text(), /Frames captured/);
}
beforeEach(() => {
  calls = []; listeners = {}; tracksStopped = 0; cameraCalls = 0; framesEncoded = 0; frameFrozen = false;
  positionTime = undefined; completed = undefined; decodedQr = undefined; storageWrites = [];
  const storage = new Map();
  global.sessionStorage = { getItem: (key) => storage.get(key) ?? null,
    setItem(key, value) { storage.set(key, value); storageWrites.push([key, value]); }, removeItem: (key) => storage.delete(key) };
  saveTokens('access', 'refresh'); saveUser({ id: 'student', role: 'student' }); markCheckin('active-batch3', false); markCheckin('other', false);
  target = { id: 'active-batch3', course_id: 'course', name: 'Current lecture', status: 'active',
    require_liveness_check: true, require_face_match: false,
    checkin_opens_at: new Date(Date.now() - 60000).toISOString(), checkin_closes_at: new Date(Date.now() + 600000).toISOString() };
  issued = challenge();
  video = { readyState: 2, videoWidth: 640, videoHeight: 480, srcObject: null, async play() {} };
  const track = { stop() { tracksStopped++; }, addEventListener(type, callback) { if (type === 'ended') ended = callback; } };
  Object.defineProperty(global, 'navigator', { configurable: true, value: {
    mediaDevices: { async getUserMedia() { cameraCalls++; return { getTracks: () => [track] }; } },
    geolocation: { getCurrentPosition(done) { done({ timestamp: positionTime ?? Date.now(), coords: { latitude: 1.3, longitude: 103.7, accuracy: 10 } }); } },
  } });
  global.HTMLMediaElement = { HAVE_CURRENT_DATA: 2 };
  global.document = { visibilityState: 'visible', addEventListener(name, callback) { listeners[name] = callback; },
    removeEventListener(name, callback) { if (listeners[name] === callback) delete listeners[name]; },
    createElement: () => ({ width: 0, height: 0, getContext: () => ({ drawImage() {}, clearRect() {},
      getImageData: () => ({ data: new Uint8ClampedArray(4), width: 1, height: 1 }) }),
    toDataURL(format, quality) { assert.equal(format, 'image/jpeg'); assert.equal(quality, 0.9);
      return `data:image/jpeg;base64,${Buffer.from(`camera-frame-${frameFrozen ? 1 : ++framesEncoded}`).toString('base64')}`; } }),
  };
  window.crypto = { randomUUID: () => 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' };
  window.localStorage = { getItem: () => 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', setItem(key, value) { storageWrites.push([key, value]); } };
  global.requestAnimationFrame = (callback) => { raf = callback; return 1; };
  global.cancelAnimationFrame = () => { raf = undefined; };
  respond = async (config) => {
    if (config.url === '/checkins/liveness-challenge') return ok(config, issued);
    if (config.url === '/checkins/') return ok(config, record());
    if (config.url === '/checkins/me') return ok(config, { items: [], total: 0, limit: 100, offset: 0 });
    throw new Error('Unexpected endpoint');
  };
  api.defaults.adapter = async (config) => {
    // Device integration has its own call log and assertions in device.test.cjs.
    if (config.url === '/devices/my-devices') return ok(config, [existingBrowserFixture(actor.id)]);
    calls.push(config); return respond(config);
  };
});
afterEach(async () => { if (renderer) await act(async () => renderer.unmount()); renderer = undefined;
  markCheckin('active-batch3', false); markCheckin('other', false); });

test('challenge POST uses versioned base, query session_id, no client challenge type or biometric body', async () => {
  assert.deepEqual(await requestLivenessChallenge('session /?'), issued);
  assert.equal(calls[0].baseURL, 'http://localhost:8000/api/v1');
  assert.equal(calls[0].url, '/checkins/liveness-challenge'); assert.equal(calls[0].method, 'post');
  assert.deepEqual(calls[0].params, { session_id: 'session /?' }); assert.equal(calls[0].data, 'null');
});
for (const changes of [
  { challenge_type: 'passive' }, { challenge_type: 'speak' }, { challenge_id: '' }, { instruction: '' },
  { min_frames: 2 }, { max_frames: 31 }, { recommended_frames: 2 }, { max_frames: 7 },
  { min_frames: 3.5 }, { expires_at: 'invalid' },
]) test(`malformed server challenge fails closed: ${JSON.stringify(changes)}`, () => {
  assert.throws(() => validateChallenge(challenge(changes)), /challenge is unavailable/);
});

for (const type of ['blink', 'head_turn']) test(`${type} captures distinct ordered JPEG frames within returned limits`, async () => {
  const issued = challenge({ challenge_type: type, min_frames: type === 'blink' ? 3 : 2 });
  let index = 0; const times = []; const plan = capturePlan(issued); const progress = [];
  const output = await captureLivenessFrames(issued, () => `frame-${++index}`, new AbortController().signal,
    (count, total) => progress.push([count, total]), async (ms) => { times.push(ms); });
  assert.deepEqual(output, Array.from({ length: plan.count }, (_, i) => `frame-${i + 1}`));
  assert.equal(new Set(output).size, output.length); assert.ok(output.length >= issued.min_frames && output.length <= issued.max_frames);
  assert.ok(times.slice(1).reduce((a, b) => a + b, 0) >= 3500); assert.equal(progress.at(-1)[0], plan.count);
});
test('small and large server frame limits are respected without padding', async () => {
  for (const count of [3, 8, 30]) {
    const item = challenge({ min_frames: 3, recommended_frames: count, max_frames: count }); let sequence = 0;
    const output = await captureLivenessFrames(item, () => String(++sequence), new AbortController().signal, () => {}, async () => {});
    assert.equal(output.length, count);
  }
});
test('frozen images are rejected instead of repeating a static frame', async () => {
  await assert.rejects(captureLivenessFrames(challenge(), () => 'same-frame', new AbortController().signal, () => {}, async () => {}), /did not update/);
});
test('expired or cancelled capture cannot produce evidence', async () => {
  await assert.rejects(captureLivenessFrames(challenge({ expires_at: new Date(Date.now() - 1).toISOString() }), () => 'frame',
    new AbortController().signal, () => {}, async () => {}), /expired/);
  const controller = new AbortController(); controller.abort();
  await assert.rejects(captureLivenessFrames(challenge(), () => 'frame', controller.signal, () => {}, async () => {}), { name: 'AbortError' });
});

for (const type of ['blink', 'head_turn']) test(`${type} renders server instructions and submits active evidence even when face match is optional`, async (t) => {
  fakeTime(t); issued = challenge({ challenge_type: type, min_frames: type === 'blink' ? 3 : 2,
    instruction: type === 'blink' ? 'Blink instruction from server.' : 'Turn instruction from server.' });
  await ready(); await captured(t);
  assert.match(text(), new RegExp(issued.instruction)); assert.match(text(), type === 'blink' ? /Blink challenge/ : /Head-turn challenge/);
  await click('Submit Check-in');
  const payload = JSON.parse(calls.find((config) => config.url === '/checkins/').data);
  assert.equal(payload.challenge_id, issued.challenge_id); assert.equal(payload.challenge_frames.length, 24);
  const decoded = payload.challenge_frames.map((item) => Buffer.from(item, 'base64').toString());
  assert.deepEqual(decoded, Array.from({ length: 24 }, (_, i) => `camera-frame-${i + 1}`));
  assert.equal(payload.session_id, target.id); assert.equal(payload.latitude, 1.3); assert.equal(payload.location_accuracy_meters, 10);
  assert.match(payload.device_fingerprint, /^aaaaaaaa/);
  for (const field of ['image', 'liveness_challenge_type', 'liveness_passed', 'liveness_score', 'face_match_passed', 'face_match_score', 'face_embedding_hash']) assert.equal(payload[field], undefined);
  assert.equal(completed.status, 'approved'); assert.ok(tracksStopped > 0);
  assert.equal(storageWrites.some((entry) => /camera-frame|data:image|server-issued-id/.test(entry[1])), false);
  assert.equal(storageWrites.some((entry) => payload.challenge_frames.some((frame) => entry[1].includes(frame))), false);
});
test('challenge requests require camera preview, fresh location and session eligibility', async () => {
  await mount(); assert.equal(button('Request liveness challenge').props.disabled, true);
  await click('Open Camera'); assert.equal(button('Request liveness challenge').props.disabled, true);
  await click('Get Current Location'); video.readyState = 0;
  await click('Request liveness challenge'); assert.match(text(), /Wait for the camera preview/); assert.equal(calls.length, 0);
  video.readyState = 2; target.status = 'cancelled';
  await click('Request liveness challenge'); assert.match(text(), /Session is not active/); assert.equal(calls.length, 0);
});
test('challenge creation failure shows safe backend error and can be retried', async () => {
  respond = async (config) => fail(config, 429, 'Too many challenges. Please wait.');
  await ready(); await click('Request liveness challenge'); assert.match(text(), /Too many challenges/);
  respond = async (config) => ok(config, issued);
  await click('Request liveness challenge'); assert.ok(button('Capture challenge'));
});
test('simultaneous challenge clicks share one operation and cancellation ignores a delayed response', async () => {
  let release; respond = (config) => new Promise((resolve) => { release = () => resolve(ok(config, issued)); });
  await ready(); const request = button('Request liveness challenge').props.onClick;
  await act(async () => { request(); request(); });
  assert.equal(calls.length, 1); await click('Cancel challenge');
  await act(async () => release()); assert.equal(button('Capture challenge'), undefined); assert.ok(tracksStopped > 0);
});
test('expiry during QR scanning discards the challenge and permits a new one after stopping scan', async (t) => {
  fakeTime(t); await ready(); await click('Request liveness challenge'); await click('Scan QR Code');
  await advance(t, 60100); assert.match(text(), /Challenge expired/); assert.equal(button('Capture challenge'), undefined);
  await click('Stop QR Scanning'); await click('Refresh location'); issued = challenge(); await click('Request liveness challenge'); assert.ok(button('Capture challenge'));
});
test('expiry during frame capture discards partially captured images', async (t) => {
  fakeTime(t); issued = challenge({ expires_at: new Date(Date.now() + 1700).toISOString() });
  await ready(); await click('Request liveness challenge'); await click('Capture challenge'); await advance(t, 2200);
  assert.match(text(), /expired/); assert.equal(button('Submit Check-in').props.disabled, true);
  assert.equal(calls.filter((config) => config.url === '/checkins/').length, 0);
});
test('cancel midway stops the camera and never submits retained frames', async (t) => {
  fakeTime(t); await ready(); await click('Request liveness challenge'); await click('Capture challenge');
  await advance(t, 1500); await click('Cancel challenge'); await advance(t, 5000);
  assert.ok(tracksStopped > 0); assert.equal(button('Capture challenge'), undefined);
  assert.equal(calls.filter((config) => config.url === '/checkins/').length, 0);
});
test('switching sessions unmounts and isolates all challenge, QR, location and camera state', async (t) => {
  fakeTime(t); await ready(); await captured(t);
  await act(async () => renderer.update(React.createElement(CheckInModal, { user: actor, session: { ...target, id: 'other' },
    policy: { imageRequired: true, faceMatchRequired: false }, onClose() {}, async onCompleted() {}, onUnauthorized() {} })));
  assert.equal(button('Capture challenge'), undefined); assert.equal(button('Submit Check-in').props.disabled, true);
  assert.ok(tracksStopped > 0);
});
test('updating the selected session releases the old camera and cancels pending challenge issuance', async () => {
  let release; respond = (config) => new Promise((resolve) => { release = () => resolve(ok(config, issued)); });
  await ready(); await click('Request liveness challenge');
  await act(async () => renderer.update(React.createElement(CheckInModal, { user: actor, session: { ...target, id: 'other' },
    policy: { imageRequired: true, faceMatchRequired: false }, onClose() {}, async onCompleted() {}, onUnauthorized() {} })));
  await act(async () => release()); assert.ok(tracksStopped > 0); assert.equal(button('Capture challenge'), undefined);
  assert.equal(button('Submit Check-in').props.disabled, true);
});
test('hidden tab cancels capture, clears evidence and releases camera tracks', async (t) => {
  fakeTime(t); await ready(); await click('Request liveness challenge'); await click('Capture challenge'); await advance(t, 1200);
  await act(async () => { document.visibilityState = 'hidden'; listeners.visibilitychange(); });
  await advance(t, 5000); assert.match(text(), /tab was hidden/); assert.ok(tracksStopped > 0);
  assert.equal(button('Capture challenge'), undefined); assert.equal(button('Submit Check-in').props.disabled, true);
});
test('camera loss invalidates a captured challenge and requires a new camera attempt', async (t) => {
  fakeTime(t); await ready(); await captured(t); await act(async () => ended());
  assert.match(text(), /camera became unavailable/); assert.equal(button('Capture challenge'), undefined);
  assert.equal(button('Submit Check-in').props.disabled, true); assert.ok(tracksStopped > 0);
});
test('denied camera permissions are explained without requesting a challenge', async () => {
  navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('denied', 'NotAllowedError'); };
  await mount(); await click('Open Camera'); assert.match(text(), /Camera permission was denied/); assert.equal(calls.length, 0);
});
test('cancelled modal releases a delayed camera grant', async () => {
  let grant; navigator.mediaDevices.getUserMedia = () => new Promise((resolve) => { grant = resolve; });
  await mount(); await act(async () => { void button('Open Camera').props.onClick(); }); await click('Cancel');
  await act(async () => grant({ getTracks: () => [{ stop() { tracksStopped++; } }] })); assert.equal(tracksStopped, 1);
});
test('a platform that keeps returning one cached fix must not dead-end the check-in', async (t) => {
  fakeTime(t);
  // macOS and iOS hand back a cached fix carrying its ORIGINAL acquisition
  // timestamp even with maximumAge: 0, and keep handing back the same one.
  // Rejecting purely on that timestamp leaves the student with nowhere to go:
  // every retry returns an identical reading, and the advice to "enable
  // precise location" cannot change a timestamp the platform already chose.
  const cached = Date.now() - 300000;
  let asked = 0;
  navigator.geolocation.getCurrentPosition = (done) => {
    asked++;
    done({ timestamp: cached, coords: { latitude: 1.3, longitude: 103.7, accuracy: 10 } });
  };
  await mount();
  await click('Get Current Location');
  await click('Get Current Location');
  await click('Get Current Location');
  assert.ok(asked >= 3, `asked ${asked} times`);
  assert.match(text(), /Location captured/);
  await click('Open Camera');
  assert.equal(button('Request liveness challenge').props.disabled, false);
});

// The escape hatch above is bounded in three ways, pinned here: the student is
// told the reading is old, the budget is restored the moment a real fix
// arrives, and it applies only to an over-age timestamp -- never to one that
// is missing, malformed or in the future, where retrying is not the remedy and
// the reading itself cannot be trusted.
// One unchanging cached fix, however many times it is asked for.
const stuckFixAt = (acquiredAt) => {
  navigator.geolocation.getCurrentPosition = (done) => done(
    { timestamp: acquiredAt, coords: { latitude: 1.3, longitude: 103.7, accuracy: 10 } });
};

test('an accepted cached fix discloses its real age and can still be submitted', async (t) => {
  fakeTime(t);
  stuckFixAt(Date.now() - 300000);
  await mount();
  await click('Get Current Location'); await click('Get Current Location'); await click('Get Current Location');
  // The age shown is the platform's, not the moment acceptance happened.
  assert.match(text(), /about 5 minutes ago/);
  assert.match(text(), /accepted to let you continue/);
  assert.match(text(), /refresh your location before submitting/);
  await click('Open Camera'); await captured(t); await click('Submit Check-in');
  const payload = JSON.parse(calls.find((config) => config.url === '/checkins/').data);
  assert.equal(payload.latitude, 1.3); assert.equal(payload.longitude, 103.7);
  assert.equal(payload.location_accuracy_meters, 10);
  // No evidence timestamp exists in the API contract; the age is disclosed to
  // the student, and the backend judges the position itself.
  assert.equal(payload.captured_at, undefined); assert.equal(payload.location_captured_at, undefined);
});

test('a refresh that returns the same stuck fix keeps the reading rather than discarding it', async (t) => {
  fakeTime(t);
  stuckFixAt(Date.now() - 300000);
  await mount();
  await click('Get Current Location'); await click('Get Current Location'); await click('Get Current Location');
  assert.match(text(), /Location captured/);
  // Mid-check-in, the student tries for something better and gets the same
  // cached fix back. Re-running the refusal budget here would take away a
  // reading they already hold.
  await click('Refresh location');
  assert.match(text(), /Location captured/);
  assert.doesNotMatch(text(), /more than a minute old/);
});

test('a genuinely fresh fix restores the full refusal budget', async (t) => {
  fakeTime(t); positionTime = Date.now() - 61000;
  await mount();
  await click('Get Current Location'); await click('Get Current Location');
  assert.match(text(), /more than a minute old/);
  positionTime = Date.now(); await click('Get Current Location');
  assert.match(text(), /Location captured/);
  assert.doesNotMatch(text(), /accepted to let you continue/);
  // Two refusals were already spent before the fresh fix; they must not carry
  // over, or the next single stale reading would be waved through.
  positionTime = Date.now() - 61000; await click('Refresh location');
  assert.match(text(), /more than a minute old/);
  assert.doesNotMatch(text(), /Location captured/);
});

test('a fresh reading carries no staleness disclosure', async (t) => {
  fakeTime(t); await mount(); await click('Get Current Location');
  assert.match(text(), /Location captured/);
  assert.doesNotMatch(text(), /accepted to let you continue/);
});

for (const [label, timestamp] of [['a future', () => Date.now() + 5000], ['a missing', () => undefined],
  ['a malformed', () => NaN], ['a zero', () => 0]]) {
  test(`${label} acquisition timestamp is never rescued by repeated attempts`, async (t) => {
    fakeTime(t);
    navigator.geolocation.getCurrentPosition = (done) => done(
      { timestamp: timestamp(), coords: { latitude: 1.3, longitude: 103.7, accuracy: 10 } });
    await mount();
    for (let attempt = 0; attempt < 4; attempt++) await click('Get Current Location');
    assert.match(text(), /Location timing could not be verified/);
    assert.doesNotMatch(text(), /Location captured/);
    assert.doesNotMatch(text(), /accepted to let you continue/);
    await click('Open Camera');
    assert.equal(button('Request liveness challenge').props.disabled, true);
    assert.equal(button('Submit Check-in').props.disabled, true);
    assert.equal(calls.length, 0);
  });
}

test('stale geolocation is rejected on receipt and cannot be refreshed into a fresh fix', async (t) => {
  fakeTime(t); positionTime = Date.now() - 61000;
  await mount(); await click('Get Current Location');
  assert.match(text(), /more than a minute old/);
  assert.doesNotMatch(text(), /Location captured/);
  await click('Open Camera');
  assert.equal(button('Request liveness challenge').props.disabled, true);
  assert.equal(button('Submit Check-in').props.disabled, true);
  await click('Get Current Location');
  assert.match(text(), /more than a minute old/);
  assert.equal(button('Request liveness challenge').props.disabled, true);
  assert.equal(calls.length, 0);
  positionTime = Date.now(); await click('Get Current Location');
  await click('Request liveness challenge');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/checkins/liveness-challenge');
});

test('recent cached geolocation retains its acquisition age rather than a new minute', async (t) => {
  fakeTime(t); positionTime = Date.now() - 45000; await ready();
  assert.match(text(), /Location captured/);
  await advance(t, 15001); await click('Request liveness challenge');
  assert.match(text(), /more than a minute old/);
  assert.equal(calls.length, 0);
});

for (const [age, accepted] of [[60000, true], [60001, false]]) {
  test(`location acquisition age ${age}ms is ${accepted ? 'accepted' : 'rejected'} at receipt`, async (t) => {
    fakeTime(t); positionTime = Date.now() - age; await ready();
    assert.equal(/Location captured/.test(text()), accepted);
    assert.equal(button('Request liveness challenge').props.disabled, !accepted);
    assert.equal(calls.length, 0);
  });
}

for (const [label, timestamp] of [['missing', undefined], ['null', null], ['NaN', NaN],
  ['infinite', Infinity], ['zero', 0], ['negative', -1], ['string', '1700000000000']]) {
  test(`${label} acquisition timestamp cannot be replaced with receipt time`, async () => {
    const normal = navigator.geolocation.getCurrentPosition;
    navigator.geolocation.getCurrentPosition = (done) => normal((position) => done({ ...position, timestamp }));
    await mount(); await click('Get Current Location');
    assert.match(text(), /Location timing could not be verified/);
    assert.doesNotMatch(text(), /Location captured/);
    await click('Open Camera');
    assert.equal(button('Request liveness challenge').props.disabled, true);
    assert.equal(button('Submit Check-in').props.disabled, true);
    assert.equal(calls.length, 0);
  });
}

test('a location acquisition timestamp more than one second in the future is rejected', async (t) => {
  fakeTime(t); positionTime = Date.now() + 1001;
  await mount(); await click('Get Current Location');
  assert.match(text(), /Location timing could not be verified/);
  await click('Open Camera');
  assert.equal(button('Request liveness challenge').props.disabled, true);
  assert.equal(calls.length, 0);
});

test('small acquisition clock skew cannot extend the location freshness budget', async (t) => {
  fakeTime(t); positionTime = Date.now() + 1000; await ready();
  assert.match(text(), /Location captured/);
  await advance(t, 60001); await click('Request liveness challenge');
  assert.match(text(), /more than a minute old/);
  assert.equal(calls.length, 0);
});

test('a fix that ages while its callback is delayed is rejected on receipt', async (t) => {
  fakeTime(t); let deliver;
  navigator.geolocation.getCurrentPosition = (done) => { deliver = done; };
  await mount(); const acquiredAt = Date.now(); await click('Get Current Location');
  await advance(t, 61000);
  await act(async () => deliver({ timestamp: acquiredAt, coords: { latitude: 1.3, longitude: 103.7, accuracy: 10 } }));
  assert.match(text(), /more than a minute old/);
  assert.doesNotMatch(text(), /Location captured/);
  assert.equal(calls.length, 0);
});
test('stale QR is discarded before a challenge, while manual entry retains the submitted secret', async (t) => {
  fakeTime(t); await ready();
  const edit = (value) => renderer.root.findByProps({ id: 'checkin-qr-code' }).props.onChange({ target: { value } });
  await act(async () => edit('old-qr-secret')); await advance(t, 30100);
  await click('Request liveness challenge'); assert.match(text(), /newly issued code/); assert.equal(calls.length, 0);
  await act(async () => edit('fresh-qr-secret')); await captured(t); await click('Submit Check-in');
  assert.equal(JSON.parse(calls.find((config) => config.url === '/checkins/').data).qr_code_secret, 'fresh-qr-secret');
});
test('QR scanner still decodes instructor secrets, stops repeated scans and preserves the payload', async (t) => {
  fakeTime(t); await ready(); await click('Scan QR Code'); decodedQr = 'instructor_secret_123456';
  await act(async () => raf(performance.now() + 1000)); assert.match(text(), /QR code scanned/); assert.equal(raf, undefined);
  await captured(t); await click('Submit Check-in');
  assert.equal(JSON.parse(calls.find((config) => config.url === '/checkins/').data).qr_code_secret, decodedQr);
});
for (const status of ['approved', 'pending', 'flagged', 'rejected']) test(`active result displays server-derived ${status} without inferring approval`, async (t) => {
  fakeTime(t); const normal = respond; respond = async (config) => config.url === '/checkins/' ? ok(config, record(status)) : normal(config);
  await ready(); await captured(t); await click('Submit Check-in'); assert.equal(completed.status, status); assert.match(text(), new RegExp(`Check-in ${status}`, 'i'));
  if (status === 'flagged') assert.match(text(), /not yet approved/);
  if (status === 'rejected') assert.match(text(), /not approved/);
});
for (const [status, message] of [[400, 'Liveness challenge is invalid, expired or already used'],
  [403, 'This liveness challenge was issued for a different check-in'], [422, 'A blink challenge needs at least 3 frames']]) test(`backend ${status} failure discards single-use evidence: ${message}`, async (t) => {
  fakeTime(t); const normal = respond; respond = async (config) => config.url === '/checkins/' ? fail(config, status, message) : normal(config);
  await ready(); await captured(t); await click('Submit Check-in'); assert.match(text(), new RegExp(message));
  assert.equal(pendingCheckin(target.id), false); assert.equal(button('Capture challenge'), undefined);
  assert.equal(button('Submit Check-in').props.disabled, true); assert.ok(tracksStopped > 0);
});
test('an expired QR is represented by the backend verification flag without changing its attendance verdict', async (t) => {
  fakeTime(t); const normal = respond;
  respond = async (config) => config.url === '/checkins/' ? ok(config, { ...record('approved'), qr_code_verified: false }) : normal(config);
  await ready(); await captured(t); await click('Submit Check-in');
  assert.equal(completed.status, 'approved'); assert.equal(completed.qr_code_verified, false);
  assert.match(text(), /QR verification: Not verified/);
});
test('challenge issuance preserves authenticated access-token recovery', async () => {
  let attempts = 0;
  respond = async (config) => {
    if (config.url === '/auth/refresh') return ok(config, { access_token: 'new-access', refresh_token: 'new-refresh', token_type: 'bearer' });
    if (!attempts++) return fail(config, 401, 'Token expired');
    return ok(config, issued);
  };
  assert.deepEqual(await requestLivenessChallenge(target.id), issued);
  assert.deepEqual(calls.map((item) => item.url), ['/checkins/liveness-challenge', '/auth/refresh', '/checkins/liveness-challenge']);
  assert.equal(calls.at(-1).headers.Authorization, 'Bearer new-access');
  assert.equal(calls.at(-1).params.session_id, target.id);
});
test('rapid duplicate submit clicks create only one POST', async (t) => {
  fakeTime(t); let release; const normal = respond;
  respond = (config) => config.url === '/checkins/' ? new Promise((resolve) => { release = () => resolve(ok(config, record())); }) : normal(config);
  await ready(); await captured(t); const submit = button('Submit Check-in').props.onClick;
  await act(async () => { void submit(); void submit(); }); assert.equal(calls.filter((config) => config.url === '/checkins/').length, 1);
  await act(async () => release()); assert.equal(completed.status, 'approved');
});
test('timeout never replays POST and reconciliation displays an existing flagged record', async (t) => {
  fakeTime(t); const normal = respond;
  respond = async (config) => {
    if (config.url === '/checkins/') throw new axios.AxiosError('timeout', 'ECONNABORTED', config);
    if (config.url === '/checkins/me') return ok(config, { items: [record('flagged')], total: 1, limit: 100, offset: 0 });
    return normal(config);
  };
  await ready(); await captured(t); await click('Submit Check-in'); assert.match(text(), /outcome is unknown/);
  assert.equal(pendingCheckin(target.id), true); assert.equal(button('Submit Check-in').props.disabled, true);
  await click('Check attendance status'); assert.equal(completed.status, 'flagged'); assert.equal(pendingCheckin(target.id), false);
  assert.equal(calls.filter((config) => config.url === '/checkins/').length, 1);
});
test('uncertain submissions survive modal closure and require complete history reconciliation before a fresh attempt', async (t) => {
  fakeTime(t); const normal = respond;
  respond = async (config) => { if (config.url === '/checkins/') throw new axios.AxiosError('offline', 'ERR_NETWORK', config); return normal(config); };
  await ready(); await captured(t); await click('Submit Check-in'); await click('Cancel'); await mount();
  assert.equal(button('Submit Check-in').props.disabled, true); assert.equal(button('Start a fresh attempt'), undefined);
  await click('Check attendance status'); assert.match(text(), /server may still be processing/); await click('Start a fresh attempt');
  assert.equal(pendingCheckin(target.id), false); assert.equal(calls.filter((config) => config.url === '/checkins/').length, 1);
  assert.equal(button('Request liveness challenge').props.disabled, true);
});
test('face-service HTTP failure remains uncertain instead of allowing replay', async (t) => {
  fakeTime(t); const normal = respond; respond = async (config) => config.url === '/checkins/' ? fail(config, 503, 'private service stack') : normal(config);
  await ready(); await captured(t); await click('Submit Check-in');
  assert.match(text(), /outcome is unknown/); assert.doesNotMatch(text(), /private service stack/); assert.equal(pendingCheckin(target.id), true);
});
test('malformed successful check-in response requires reconciliation instead of showing fabricated approval', async (t) => {
  fakeTime(t); const normal = respond; respond = async (config) => config.url === '/checkins/' ? ok(config, { success: true }) : normal(config);
  await ready(); await captured(t); await click('Submit Check-in');
  assert.match(text(), /outcome is unknown/); assert.equal(completed, undefined); assert.equal(pendingCheckin(target.id), true);
});
test('failed status reconciliation leaves fresh attempts blocked', async () => {
  markCheckin(target.id, true); respond = async (config) => fail(config, 503, 'internal failure');
  await mount(); await click('Check attendance status'); assert.equal(button('Start a fresh attempt'), undefined);
  assert.equal(button('Submit Check-in').props.disabled, true); assert.equal(pendingCheckin(target.id), true);
});
test('a closing session stops capture before it can start', async () => {
  await ready(); await click('Request liveness challenge'); target.status = 'closed'; await click('Capture challenge');
  assert.match(text(), /Session is not active/); assert.equal(button('Capture challenge'), undefined); assert.ok(tracksStopped > 0);
});
test('location becoming stale after capture prevents submission without discarding the requirement', async (t) => {
  fakeTime(t); await ready(); await advance(t, 10000); issued = challenge(); await captured(t); await advance(t, 46000);
  await click('Submit Check-in'); assert.match(text(), /more than a minute old/);
  assert.equal(calls.filter((config) => config.url === '/checkins/').length, 0);
});
test('unavailable camera hardware is explained and no challenge is requested', async () => {
  navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('missing', 'NotFoundError'); };
  await mount(); await click('Open Camera'); assert.match(text(), /No camera was found/); assert.equal(calls.length, 0);
});
test('capture failure discards evidence and offers camera restart without exposing the native exception', async (t) => {
  fakeTime(t); await ready(); await click('Request liveness challenge');
  const normal = document.createElement;
  document.createElement = () => ({ getContext() { return { drawImage() { throw new Error('private hardware stack'); }, clearRect() {} }; } });
  await click('Capture challenge'); await advance(t, 1100);
  assert.match(text(), /Camera capture was interrupted/); assert.doesNotMatch(text(), /private hardware stack/);
  assert.equal(button('Capture challenge'), undefined); assert.equal(button('Submit Check-in').props.disabled, true);
  await click('Close camera'); document.createElement = normal; await click('Open Camera'); assert.equal(cameraCalls, 2);
});
test('pending-submission markers are isolated between signed-in accounts', () => {
  markCheckin(target.id, true); assert.equal(pendingCheckin(target.id), true);
  saveUser({ id: 'different-student', role: 'student' }); assert.equal(pendingCheckin(target.id), false);
  saveUser({ id: 'student', role: 'student' }); assert.equal(pendingCheckin(target.id), true);
});
test('invalid QR retains manual fallback without being submitted as a secret', async () => {
  await ready(); await click('Scan QR Code'); decodedQr = 'https://unrelated.invalid';
  await act(async () => raf(performance.now() + 1000)); assert.match(text(), /does not contain a supported SAIV/);
  assert.equal(renderer.root.findByProps({ id: 'checkin-qr-code' }).props.value, '');
  assert.equal(renderer.root.findByProps({ id: 'checkin-qr-code' }).props.disabled, false);
});
test('PWA service worker does not intercept biometric POSTs or API GETs for caching', () => {
  const fs = require('node:fs'); const vm = require('node:vm'); const handlers = {}; let cacheUsed = false;
  vm.runInNewContext(fs.readFileSync(require.resolve('../public/sw.js'), 'utf8'), {
    self: { addEventListener(name, callback) { handlers[name] = callback; }, location: { origin: 'https://student.invalid' } },
    caches: { open() { cacheUsed = true; }, match() { cacheUsed = true; } }, URL,
  });
  for (const request of [{ method: 'POST', mode: 'cors', url: 'https://student.invalid/api/v1/checkins/' },
    { method: 'GET', mode: 'cors', url: 'https://student.invalid/api/v1/checkins/me' }]) {
    handlers.fetch({ request, respondWith() { assert.fail('Sensitive API requests must go directly to the network'); } });
  }
  assert.equal(cacheUsed, false);
});
test('history reconciliation scans older pages and fails closed on incomplete/changing history', async () => {
  const rows = Array.from({ length: 120 }, (_, index) => ({ ...record('approved', `old-${index}`), id: String(index) })); rows[110] = record('rejected');
  respond = async (config) => ok(config, { items: rows.slice(config.params.offset, config.params.offset + 100), total: 120, limit: 100, offset: config.params.offset });
  assert.equal((await findSessionCheckin(target.id)).status, 'rejected'); assert.equal(calls.length, 2);
  respond = async (config) => ok(config, { items: Array.from({ length: 100 }, (_, i) => ({ ...record('approved', 'unrelated'), id: String(i) })),
    total: 3000, limit: 100, offset: config.params.offset });
  await assert.rejects(findSessionCheckin('not-found'), /complete attendance history/);
});

test('check-in has labelled steps, optional code and no implicit camera or location requests', async () => {
  let locationRequests = 0;
  navigator.geolocation.getCurrentPosition = () => { locationRequests++; };
  await mount();
  assert.equal(cameraCalls, 0); assert.equal(locationRequests, 0);
  assert.deepEqual(renderer.root.findAllByType('section').map(node => node.props['aria-label']), ['Location', 'Face verification', 'QR code']);
  const dialog = renderer.root.findByProps({ role: 'dialog' });
  assert.equal(dialog.props['aria-modal'], 'true');
  assert.equal(dialog.props['aria-labelledby'], 'checkin-dialog-title');
  assert.ok(renderer.root.findByProps({ id: 'checkin-dialog-title' }));
  assert.equal(renderer.root.findByType('label').props.htmlFor, 'checkin-qr-code');
  assert.match(text(), /Codes expire 30 seconds/);
});

test('preview explicitly remains unmirrored and native-size frames are submitted without transforms', async (t) => {
  fakeTime(t);
  const draws = [], createCanvas = document.createElement;
  document.createElement = (...args) => {
    const canvas = createCanvas(...args), context = canvas.getContext();
    context.drawImage = (...parameters) => draws.push(parameters);
    // No translate/scale/rotate methods: a capture transform would fail this test.
    canvas.getContext = () => context;
    return canvas;
  };
  await ready();
  const preview = renderer.root.findByType('video');
  assert.deepEqual(preview.props.style, { transform: 'none', scale: 'none', rotate: 'none' });
  assert.equal(preview.props.playsInline, true); assert.equal(preview.props.muted, true);
  await captured(t);
  assert.equal(renderer.root.findByType('video'), preview, 'challenge updates keep the same video element');
  assert.equal(cameraCalls, 1); assert.equal(draws.length, 24);
  for (const parameters of draws) assert.deepEqual(parameters, [video, 0, 0, 640, 480]);
  await click('Submit Check-in');
  const payload = JSON.parse(calls.find(config => config.url === '/checkins/').data);
  assert.equal(payload.challenge_frames.length, 24);
  assert.equal(Buffer.from(payload.challenge_frames[0], 'base64').toString(), 'camera-frame-1');
});

test('challenge progress, server direction and expiry stay visible without reopening the camera', async (t) => {
  fakeTime(t); issued = challenge({ challenge_type: 'head_turn', min_frames: 2, instruction: 'Turn left, as instructed by the server.' });
  await ready(); await click('Request liveness challenge');
  const preview = renderer.root.findByType('video');
  assert.match(text(), /Turn left, as instructed by the server/);
  assert.match(text(), /Expires in 60\s*s/);
  await click('Capture challenge'); await advance(t, 1000);
  const progress = renderer.root.findByType('progress');
  assert.equal(progress.props['aria-label'], 'Liveness capture progress');
  assert.ok(progress.props.value > 0 && progress.props.value < progress.props.max);
  assert.match(text(), new RegExp(`${progress.props.value}\\s*/\\s*24\\s*frames`));
  assert.equal(renderer.root.findByType('video'), preview); assert.equal(cameraCalls, 1);
  await advance(t, 60100);
  assert.match(text(), /Challenge expired/); assert.equal(button('Capture challenge'), undefined);
  assert.equal(button('Submit Check-in').props.disabled, true);
});

test('closing the camera from the compact toolbar releases tracks and invalidates the challenge', async () => {
  await ready(); await click('Request liveness challenge');
  await click('Close camera');
  assert.ok(tracksStopped > 0); assert.equal(video.srcObject, null);
  assert.equal(renderer.root.findByType('video').props.className, 'hidden');
  assert.equal(button('Capture challenge'), undefined); assert.equal(button('Submit Check-in').props.disabled, true);
});

// ---------------------------------------------------------------------------
// Colour-reflection challenge
//
// The screen is the light source, so what is pinned here is TIMING and the
// exactness of the frame count. The backend works out which colour each frame
// was taken under from the frame's position in the list, so a frame short, a
// frame extra, or a frame taken before the panel finished its transition all
// score pixels against a colour that was not on the screen.
// ---------------------------------------------------------------------------

const { captureColourFrames } = require('../lib/liveness.ts');
const { validateColourChallenge, changesPerSecond, holdScreenAwake,
        MAX_CHANGES_PER_SECOND } = require('../lib/colour-liveness.ts');

const SEQ = ['#ff0000', '#00ffff', '#00ff00', '#ff00ff', '#0000ff', '#ffff00'];
const colourChallenge = (changes = {}) => challenge({
  challenge_type: 'colour_reflection',
  instruction: 'Hold the camera at arm’s length and look at the screen while it changes colour.',
  colour_sequence: SEQ, hold_ms: 480, settle_ms: 200, frames_per_step: 2,
  min_frames: 12, recommended_frames: 12, max_frames: 30, ...changes });

test('a colour challenge is accepted and plans exactly one frame set per colour', () => {
  const issued = colourChallenge();
  assert.deepEqual(validateChallenge(issued), issued);
  const plan = capturePlan(issued);
  assert.equal(plan.count, 12);
  assert.equal(plan.interval, 480);
  assert.equal(validateColourChallenge(issued).totalFrames, issued.min_frames);
});

test('every frame is taken after the panel has settled, never at the colour change', async () => {
  const issued = colourChallenge();
  const timeline = []; let clock = 0; let index = 0;
  await captureColourFrames(issued, () => `frame-${++index}`,
    (colour) => timeline.push({ at: clock, show: colour }),
    new AbortController().signal, () => {},
    async (ms) => { clock += ms; });

  const shown = timeline.filter((e) => e.show !== null);
  assert.deepEqual(shown.map((e) => e.show), SEQ);
  // Each colour starts one full hold after the previous one.
  shown.forEach((event, step) => assert.equal(event.at, step * 480));
  // And the emitter is cleared at the end, so no colour is left on screen.
  assert.equal(timeline.at(-1).show, null);
});

test('frames land inside their own colour step, at the settle offset and one gap later', async () => {
  const issued = colourChallenge();
  let clock = 0; let index = 0; const taken = []; let current = null;
  await captureColourFrames(issued, () => { taken.push({ at: clock, colour: current }); return `frame-${++index}`; },
    (colour) => { current = colour; }, new AbortController().signal, () => {},
    async (ms) => { clock += ms; });

  assert.equal(taken.length, 12);
  taken.forEach((frame, position) => {
    const step = Math.floor(position / 2);
    const offset = position % 2 === 0 ? 200 : 340;   // settle, then settle + gap
    assert.equal(frame.at, step * 480 + offset, `frame ${position}`);
    // The colour recorded against this frame is the one the backend will
    // score it against: sequence[step], each colour twice.
    assert.equal(frame.colour, SEQ[step]);
  });
});

test('the colour sequence produces exactly the frame count the server demands', async () => {
  for (const [steps, perStep] of [[3, 1], [4, 3], [6, 2]]) {
    const issued = colourChallenge({
      colour_sequence: SEQ.slice(0, steps), frames_per_step: perStep,
      min_frames: steps * perStep, recommended_frames: steps * perStep });
    let index = 0;
    const output = await captureColourFrames(issued, () => `f${++index}`, () => {},
      new AbortController().signal, () => {}, async () => {});
    assert.equal(output.length, steps * perStep);
    assert.equal(new Set(output).size, output.length);
  }
});

test('a malformed colour challenge is refused rather than captured against', () => {
  const bad = [
    { colour_sequence: [] },
    { colour_sequence: ['#ff0000', '#00ff00'] },             // too few steps
    { colour_sequence: ['red', '#00ff00', '#0000ff'] },      // not hex
    { colour_sequence: ['#f00', '#00ff00', '#0000ff'] },     // short hex
    { frames_per_step: 0 },
    { hold_ms: 100 },                                        // too fast to settle
    { settle_ms: 480 },                                      // settle fills the hold
    { min_frames: 11 },                                      // disagrees with the plan
    { max_frames: 6 },                                       // cannot carry the frames
  ];
  for (const change of bad) {
    assert.throws(() => validateChallenge(colourChallenge(change)),
      /unavailable/, JSON.stringify(change));
  }
});

test('a challenge that would flash faster than the accessibility limit is refused', () => {
  // WCAG 2.3.1 treats large-area flashing above three per second as a
  // photosensitive-seizure risk. The server keeps itself under it; the client
  // refuses to drive the screen that fast regardless, because the safe
  // failure is "request a new challenge" and not "flash the screen".
  const fast = colourChallenge({ hold_ms: 300, settle_ms: 100 });
  assert.ok(changesPerSecond(validateColourChallenge(fast)) > MAX_CHANGES_PER_SECOND);
  assert.throws(() => validateChallenge(fast), /unavailable/);
  assert.ok(changesPerSecond(validateColourChallenge(colourChallenge())) <= MAX_CHANGES_PER_SECOND);
});

test('a frozen camera is caught mid-sequence and discards every frame', async () => {
  await assert.rejects(captureColourFrames(colourChallenge(), () => 'same', () => {},
    new AbortController().signal, () => {}, async () => {}), /did not update/);
});

test('expiry and cancellation clear the screen and produce no evidence', async () => {
  const cleared = [];
  await assert.rejects(captureColourFrames(
    colourChallenge({ expires_at: new Date(Date.now() - 1).toISOString() }),
    () => 'frame', (colour) => cleared.push(colour), new AbortController().signal,
    () => {}, async () => {}), /expired/);
  // Even on the failure path the emitter is taken down; a full-screen colour
  // left behind would cover the page with nothing to dismiss it.
  assert.equal(cleared.at(-1), null);

  const controller = new AbortController(); controller.abort();
  await assert.rejects(captureColourFrames(colourChallenge(), () => 'frame', () => {},
    controller.signal, () => {}, async () => {}), { name: 'AbortError' });
});

test('progress is reported per frame against the exact total', async () => {
  let index = 0; const progress = [];
  await captureColourFrames(colourChallenge(), () => `f${++index}`, () => {},
    new AbortController().signal, (count, total) => progress.push([count, total]),
    async () => {});
  assert.deepEqual(progress.map(([count]) => count), [...Array(12)].map((_, i) => i + 1));
  assert.ok(progress.every(([, total]) => total === 12));
});

test('a missing or refused Wake Lock never blocks the capture', async () => {
  // Unsupported in several browsers and refused outside a secure context. A
  // student without it must still be able to check in; the only cost is that
  // the system may dim the panel part-way through, which the backend reports
  // as an unmeasurable response rather than a spoof.
  assert.equal(typeof await holdScreenAwake(undefined), 'function');
  assert.equal(typeof await holdScreenAwake({}), 'function');
  (await holdScreenAwake({ wakeLock: { request: async () => { throw new Error('denied'); } } }))();
});

test('a granted wake lock is released once, however many times it is asked', async () => {
  let released = 0;
  const release = await holdScreenAwake({
    wakeLock: { request: async () => ({ release: async () => { released++; } }) } });
  release(); release();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(released, 1);
});

test('the wake lock is released even when the capture fails', async () => {
  // Otherwise a failed capture leaves the student's screen pinned awake.
  let released = 0;
  const lock = async () => () => { released++; };
  await assert.rejects(captureColourFrames(colourChallenge(), () => 'same', () => {},
    new AbortController().signal, () => {}, async () => {}, lock), /did not update/);
  assert.equal(released, 1);
});

test('the wake lock is released after a successful capture too', async () => {
  let released = 0;
  const lock = async () => () => { released++; };
  let index = 0;
  await captureColourFrames(colourChallenge(), () => `f${++index}`, () => {},
    new AbortController().signal, () => {}, async () => {}, lock);
  assert.equal(released, 1);
});

test('the gesture challenges are untouched by the colour plan', () => {
  for (const type of ['blink', 'head_turn']) {
    const issued = challenge({ challenge_type: type, min_frames: type === 'blink' ? 3 : 2 });
    assert.deepEqual(validateChallenge(issued), issued);
    assert.equal(capturePlan(issued).count, 24);
    assert.throws(() => validateColourChallenge(issued), /unavailable/);
  }
});
