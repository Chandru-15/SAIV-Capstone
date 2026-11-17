const { test, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { create, act } = require('react-test-renderer');
const axios = require('axios');
const navId = require.resolve('next/navigation');
require.cache[navId] = { id: navId, filename: navId, loaded: true, exports: { useRouter: () => ({ replace() {} }) } };
global.window = {};
global.fetch = async () => Response.json({ apiUrl: 'http://localhost:8000', staffPortalUrl: 'http://localhost:8501' });
const { api } = require('../lib/api.ts');
const { saveTokens, saveUser, clearAuthSession, getSessionVersion } = require('../lib/auth.ts');
const { getBrowserDeviceIdentifier, BROWSER_DEVICE_KEY } = require('../lib/browser-device.ts');
const { getMyDevices, registerBrowserDevice } = require('../lib/device-api.ts');
const { prepareBrowserRegistration, confirmBrowserRegistration } = require('../lib/device-registration.ts');
const { markCheckin } = require('../lib/checkin-attempt.ts');
const Gate = require('../components/checkin/DeviceRegistrationGate.tsx').default;
const CheckInModal = require('../components/checkin/CheckInModal.tsx').default;
const { identifier, existingBrowserFixture } = require('./device-fixture.cjs');
const browserKeys = require('../lib/browser-key.ts');

let renderer, calls, devices, respond, user, local, sessionStore, randomCalls, cameraCalls, locationCalls, stopped, writes, video, completed;
const ok = (config, data) => ({ config, data, status: config.method === 'post' ? 201 : 200, statusText: 'OK', headers: {} });
const fail = (config, status, message) => { throw new axios.AxiosError('HTTP', undefined, config, undefined,
  { ...ok(config, { code: 'permission_denied', message, details: null }), status }); };
const target = () => ({ id: 'device-session', course_id: 'course', name: 'Current lecture', status: 'active',
  require_face_match: false, require_liveness_check: false,
  checkin_opens_at: new Date(Date.now() - 10000).toISOString(), checkin_closes_at: new Date(Date.now() + 600000).toISOString() });
const attendance = (status = 'approved') => ({ id: 'attendance', session_id: 'device-session', student_id: user.id,
  status, risk_score: 0.12, qr_code_verified: false, checked_in_at: new Date().toISOString() });
const text = () => {
  const visit = (node) => typeof node === 'string' ? node : Array.isArray(node) ? node.map(visit).join(' ') : visit(node?.children ?? []);
  return visit(renderer.toJSON()).replace(/\s+/g, ' ').trim();
};
const button = (label) => renderer.root.findAllByType('button').find((item) => item.children.join('') === label);
const click = async (label) => { const action = button(label); assert.ok(action, label); assert.notEqual(action.props.disabled, true);
  await act(async () => action.props.onClick()); };
const gate = (actor = user) => React.createElement(Gate, { user: actor, onClose() {} },
  (fingerprint) => React.createElement('p', null, `Ready: ${fingerprint}`));
async function mount(element = gate()) {
  await act(async () => { renderer = create(element, { createNodeMock: (element) => element.type === 'video' ? video : null }); });
}
function modal(overrides = {}) {
  return React.createElement(CheckInModal, { user, session: target(), policy: { faceMatchRequired: false, imageRequired: false },
    onClose() { renderer.unmount(); }, async onCompleted(record) { completed = record; }, onUnauthorized() {}, ...overrides });
}
beforeEach(() => {
  calls = []; devices = []; randomCalls = 0; cameraCalls = 0; locationCalls = 0; stopped = 0; writes = []; completed = undefined;
  local = new Map(); sessionStore = new Map();
  global.sessionStorage = { getItem: (key) => sessionStore.get(key) ?? null,
    setItem: (key, value) => sessionStore.set(key, value), removeItem: (key) => sessionStore.delete(key),
    get length() { return sessionStore.size; }, key: (index) => [...sessionStore.keys()][index] ?? null };
  window.localStorage = { getItem: (key) => local.get(key) ?? null,
    setItem(key, value) { local.set(key, value); writes.push([key, value]); } };
  window.crypto = { randomUUID() { randomCalls++; return identifier; } };
  user = { id: 'device-student', role: 'student', is_active: true, camera_consent: true, geolocation_consent: true, face_enrolled: true };
  saveTokens('access', 'refresh'); saveUser(user); markCheckin('device-session', false);
  video = { readyState: 2, videoWidth: 640, videoHeight: 480, srcObject: null, async play() {} };
  global.HTMLMediaElement = { HAVE_CURRENT_DATA: 2 };
  global.document = { visibilityState: 'visible', addEventListener() {}, removeEventListener() {}, createElement: () => ({
    getContext: () => ({ drawImage() {}, clearRect() {} }), toDataURL: () => 'data:image/jpeg;base64,camera-face-image' }) };
  Object.defineProperty(global, 'navigator', { configurable: true, value: {
    mediaDevices: { async getUserMedia() { cameraCalls++; return { getTracks: () => [{ stop() { stopped++; }, addEventListener() {} }] }; } },
    geolocation: { getCurrentPosition(done) { locationCalls++; done({ timestamp: Date.now(), coords: { latitude: 1.3, longitude: 103.7, accuracy: 5 } }); } },
  } });
  respond = async (config) => {
    if (config.url === '/devices/my-devices') return ok(config, devices);
    if (config.url === '/devices/') {
      const body = JSON.parse(config.data);
      const result = existingBrowserFixture(user.id, { device_fingerprint: body.device_fingerprint }); devices = [result]; return ok(config, result);
    }
    if (config.url === '/checkins/') return ok(config, attendance());
    if (config.url === '/checkins/me') return ok(config, { items: [attendance('flagged')], total: 1, limit: 100, offset: 0 });
    throw new Error('Unexpected endpoint');
  };
  api.defaults.adapter = async (config) => { calls.push(config); return respond(config); };
});
afterEach(async () => { if (renderer) await act(async () => renderer.unmount()); renderer = undefined; markCheckin('device-session', false); clearAuthSession(); });

test('generates a cryptographic UUID once and persists using the existing application key', () => {
  assert.equal(getBrowserDeviceIdentifier(), identifier); assert.equal(local.get(BROWSER_DEVICE_KEY), identifier);
  assert.equal(getBrowserDeviceIdentifier(), identifier); assert.equal(randomCalls, 1); assert.deepEqual(writes, [[BROWSER_DEVICE_KEY, identifier]]);
});
test('reuses an existing valid identifier across reload-style repeated calls without mutation', () => {
  const existing = 'BBBBBBBB-BBBB-4BBB-8BBB-BBBBBBBBBBBB'; local.set(BROWSER_DEVICE_KEY, existing);
  assert.equal(getBrowserDeviceIdentifier(), existing); assert.equal(randomCalls, 0); assert.deepEqual(writes, []);
});
test('malformed stored identifiers are replaced with a valid crypto-generated UUID', () => {
  local.set(BROWSER_DEVICE_KEY, 'not-a-valid-uuid'); assert.equal(getBrowserDeviceIdentifier(), identifier); assert.equal(randomCalls, 1);
});
test('getRandomValues provides UUID v4 fallback without Math.random or environment fingerprinting', () => {
  window.crypto = { getRandomValues(bytes) { bytes.fill(0xff); return bytes; } };
  assert.equal(getBrowserDeviceIdentifier(), 'ffffffff-ffff-4fff-bfff-ffffffffffff');
});
test('without Web Crypto it fails safely and does not persist a weak identifier', () => {
  window.crypto = undefined; assert.throws(getBrowserDeviceIdentifier, /Secure browser identification is unavailable/); assert.deepEqual(writes, []);
});
test('SSR does not touch browser storage or generate identifiers', () => {
  const previous = global.window;
  try { delete global.window; assert.throws(getBrowserDeviceIdentifier, /only in the browser/); }
  finally { global.window = previous; }
  assert.equal(randomCalls, 0); assert.deepEqual(writes, []);
});
for (const kind of ['read', 'write', 'silent write']) test(`restricted storage (${kind}) blocks registration instead of using a temporary ID`, async () => {
  if (kind === 'read') window.localStorage.getItem = () => { throw new Error('private browser error'); };
  if (kind === 'write') window.localStorage.setItem = () => { throw new Error('private browser error'); };
  if (kind === 'silent write') window.localStorage.setItem = () => {};
  await mount(); assert.match(text(), /Browser storage is unavailable/); assert.doesNotMatch(text(), /private browser error/);
  assert.equal(calls.length, 0); assert.equal(button('Open Camera'), undefined);
});
test('an existing readable identifier can be reused when new storage writes are blocked', async () => {
  local.set(BROWSER_DEVICE_KEY, identifier); window.localStorage.setItem = () => { throw new Error('blocked'); };
  devices = [existingBrowserFixture(user.id)]; assert.equal((await prepareBrowserRegistration(user)).identifier, identifier);
  assert.equal(randomCalls, 0); assert.equal(calls.filter((config) => config.method === 'post').length, 0);
});
test('actual device API uses array GET and root POST with only UUID and web platform metadata', async () => {
  const result = await prepareBrowserRegistration(user);
  assert.deepEqual(calls.map((config) => [config.method, config.url]), [['get', '/devices/my-devices'], ['post', '/devices/']]);
  assert.equal(calls[0].baseURL, 'http://localhost:8000/api/v1');
  assert.deepEqual(JSON.parse(calls[1].data), { device_fingerprint: identifier, platform: 'web' });
  assert.equal(calls[1].headers.Authorization, 'Bearer access'); assert.equal(calls[0].params, undefined);
  assert.equal(result.userId, user.id); assert.equal(result.identifier, identifier);
});
test('an existing active untrusted device is ready without re-registering or inventing attestation', async () => {
  devices = [existingBrowserFixture(user.id)]; await mount(); assert.match(text(), /Ready:/);
  assert.deepEqual(calls.map((config) => config.method), ['get']);
});
test('concurrent callers coordinate one lookup and registration operation', async () => {
  const normal = respond; let release;
  respond = (config) => config.url === '/devices/my-devices' ? new Promise((resolve) => { release = () => resolve(ok(config, [])); }) : normal(config);
  const first = prepareBrowserRegistration(user); const second = prepareBrowserRegistration(user);
  await new Promise(setImmediate); assert.equal(calls.length, 1); release();
  const result = await Promise.all([first, second]); assert.deepEqual(result[0], result[1]);
  assert.equal(calls.filter((config) => config.method === 'post').length, 1);
});
test('gate shows checking and registering before ready, with no evidence controls', async () => {
  let releaseGet, releasePost;
  respond = (config) => new Promise((resolve) => {
    if (config.method === 'get') releaseGet = () => resolve(ok(config, []));
    else releasePost = () => resolve(ok(config, existingBrowserFixture(user.id)));
  });
  await mount(modal({ policy: { faceMatchRequired: true, imageRequired: true } })); assert.match(text(), /Checking device registration/);
  assert.equal(button('Open Camera'), undefined); assert.equal(button('Get Current Location'), undefined); assert.equal(cameraCalls + locationCalls, 0);
  await act(async () => releaseGet()); assert.match(text(), /Registering this browser/); assert.equal(button('Request liveness challenge'), undefined);
  await act(async () => releasePost()); assert.match(text(), /Browser registration confirmed/); assert.ok(button('Open Camera'));
});
test('gate re-render neither repeats lookups nor registrations; a new attempt performs a fresh read only', async () => {
  await mount(); assert.equal(calls.length, 2); await act(async () => renderer.update(gate({ ...user }))); assert.equal(calls.length, 2);
  await act(async () => renderer.unmount()); renderer = undefined; await mount();
  assert.equal(calls.length, 3); assert.equal(calls.filter((config) => config.method === 'post').length, 1);
});
test('failed lookup never authorizes POST; retry starts with another successful lookup', async () => {
  const normal = respond; respond = async (config) => fail(config, 503, 'private stack');
  await mount(); assert.doesNotMatch(text(), /Ready:|private stack/); assert.equal(calls.filter((config) => config.method === 'post').length, 0);
  respond = normal; await click('Check registration again'); assert.match(text(), /Ready:/); assert.equal(calls.filter((config) => config.method === 'post').length, 1);
});
test('registration timeout is not replayed when lookup still shows no record', async () => {
  const normal = respond; respond = async (config) => config.url === '/devices/' ? Promise.reject(new axios.AxiosError('timeout', 'ECONNABORTED', config)) : normal(config);
  await mount(); assert.doesNotMatch(text(), /Ready:/); await click('Check registration again');
  assert.match(text(), /no registration request will be replayed/); assert.equal(calls.filter((config) => config.method === 'post').length, 1);
});
test('uncertain registration can recover through GET after the original POST committed', async () => {
  const normal = respond; respond = async (config) => {
    if (config.url === '/devices/') { devices = [existingBrowserFixture(user.id)]; throw new axios.AxiosError('offline', 'ERR_NETWORK', config); }
    return normal(config);
  };
  await mount(); await click('Check registration again'); assert.match(text(), /Ready:/);
  assert.equal(calls.filter((config) => config.method === 'post').length, 1);
});
for (const status of [400, 422, 429, 500, 503]) test(`registration HTTP ${status} never reports ready or automatically retries POST`, async () => {
  const normal = respond; respond = async (config) => config.url === '/devices/' ? fail(config, status, 'Registration failed') : normal(config);
  await mount(); assert.doesNotMatch(text(), /Ready:/); await click('Check registration again');
  assert.equal(calls.filter((config) => config.method === 'post').length, 1);
});
for (const status of [403, 409]) test(`registration refusal ${status} is visible, terminal for this login and does not rotate the identifier`, async () => {
  const normal = respond; respond = async (config) => config.url === '/devices/' ? fail(config, status, 'Device refused') : normal(config);
  await mount(); assert.doesNotMatch(text(), /Ready:/); assert.equal(button('Check registration again'), undefined);
  assert.match(text(), status === 409 ? /another account/ : /was refused/); assert.equal(local.get(BROWSER_DEVICE_KEY), identifier); assert.equal(randomCalls, 1);
  await assert.rejects(prepareBrowserRegistration(user), /another account|was refused/); assert.equal(calls.length, 2);
});
test('inactive devices remain blocked and are never re-registered even after a failed lookup elsewhere', async () => {
  devices = [existingBrowserFixture(user.id, { is_active: false })]; await mount();
  assert.match(text(), /disabled or revoked/); assert.equal(button('Check registration again'), undefined);
  respond = async (config) => fail(config, 503, 'outage');
  await assert.rejects(prepareBrowserRegistration(user), /disabled or revoked/);
  assert.equal(calls.filter((config) => config.method === 'post').length, 0); assert.equal(calls.length, 1);
});
test('a previously active device disappearing from the list is not permission to re-register it', async () => {
  devices = [existingBrowserFixture(user.id)]; await prepareBrowserRegistration(user); devices = [];
  await assert.rejects(prepareBrowserRegistration(user), /Registration is not confirmed/);
  assert.equal(calls.filter((config) => config.method === 'post').length, 0);
});
test('inactive POST result fails closed rather than reporting success', async () => {
  const normal = respond; respond = async (config) => config.url === '/devices/' ? ok(config, existingBrowserFixture(user.id, { is_active: false })) : normal(config);
  await mount(); assert.match(text(), /disabled or revoked/); assert.equal(button('Check registration again'), undefined);
});
test('missing status, wrong ownership and paginated envelopes do not authorize registration', async () => {
  for (const body of [[{ ...existingBrowserFixture(user.id), is_active: undefined }], [existingBrowserFixture('different-user')], { items: [] }]) {
    respond = async (config) => ok(config, body); await assert.rejects(prepareBrowserRegistration(user));
  }
  assert.equal(calls.filter((config) => config.method === 'post').length, 0);
});
test('malformed or mismatched POST responses remain unconfirmed and cannot be replayed', async () => {
  const normal = respond; respond = async (config) => config.url === '/devices/' ? ok(config, { success: true }) : normal(config);
  await mount(); assert.doesNotMatch(text(), /Ready:/); await click('Check registration again'); assert.equal(calls.filter((config) => config.method === 'post').length, 1);
});
for (const changes of [{ user_id: 'different-owner' }, { device_fingerprint: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb' }]) {
  test(`mismatched POST identity does not confirm registration: ${JSON.stringify(changes)}`, async () => {
    const normal = respond;
    respond = async (config) => config.url === '/devices/' ? ok(config, existingBrowserFixture(user.id, changes)) : normal(config);
    await mount(); assert.match(text(), /inconsistent device ownership/); assert.doesNotMatch(text(), /Ready:/);
    await click('Check registration again'); assert.equal(calls.filter((config) => config.method === 'post').length, 1);
  });
}
test('a persisted observed-or-attempted marker never confers ready status or permission to replay registration', async () => {
  local.set(BROWSER_DEVICE_KEY, identifier);
  sessionStore.set(`saiv_device_registration_v1:${user.id}:${identifier}`, 'observed-or-attempted');
  await assert.rejects(prepareBrowserRegistration(user), /Registration is not confirmed/);
  assert.deepEqual(calls.map((config) => config.method), ['get']);
  devices = [existingBrowserFixture(user.id)]; assert.equal((await prepareBrowserRegistration(user)).identifier, identifier);
  assert.deepEqual(calls.map((config) => config.method), ['get', 'get']);
});
test('read-only confirmation cannot create an unknown device', async () => {
  await assert.rejects(confirmBrowserRegistration(user, identifier), /Registration is not confirmed/);
  assert.deepEqual(calls.map((config) => config.method), ['get']);
});
test('failed refresh during registration clears authentication and cannot report ready', async () => {
  const normal = respond;
  respond = async (config) => config.url === '/devices/' || config.url === '/auth/refresh' ? fail(config, 401, 'Expired') : normal(config);
  const result = await prepareBrowserRegistration(user).catch((error) => error);
  assert.equal(axios.isCancel(result), true); assert.equal(sessionStore.get('saiv_access_token'), undefined);
  assert.equal(calls.filter((config) => config.url === '/devices/').length, 1); assert.equal(local.get(BROWSER_DEVICE_KEY), identifier);
});
test('registration errors never reflect the identifier in student messages', async () => {
  const normal = respond; respond = async (config) => config.url === '/devices/' ? fail(config, 422, `Bad device ${identifier}`) : normal(config);
  await mount(); assert.doesNotMatch(text(), new RegExp(identifier)); assert.match(text(), /Device registration failed/);
});
test('refresh-token recovery during registration retains the same request identifier', async () => {
  let attempts = 0; const normal = respond;
  respond = async (config) => {
    if (config.url === '/auth/refresh') return ok(config, { access_token: 'new-access', refresh_token: 'new-refresh', token_type: 'bearer' });
    if (config.url === '/devices/' && attempts++ === 0) return fail(config, 401, 'Expired');
    return normal(config);
  };
  const version = getSessionVersion(); assert.equal((await prepareBrowserRegistration(user)).identifier, identifier);
  const posts = calls.filter((config) => config.url === '/devices/'); assert.equal(posts.length, 2); assert.equal(posts[0].data, posts[1].data);
  assert.equal(posts[1].headers.Authorization, 'Bearer new-access'); assert.equal(getSessionVersion(), version);
});
for (const action of ['logout', 'account replacement']) test(`${action} during pending registration aborts stale state and cannot mark a new account ready`, async () => {
  let release; const normal = respond;
  respond = (config) => config.url === '/devices/' ? new Promise((resolve) => { release = () => resolve(ok(config, existingBrowserFixture(user.id))); }) : normal(config);
  const preparation = prepareBrowserRegistration(user).catch((error) => error); await new Promise(setImmediate);
  assert.ok(release);
  if (action === 'logout') clearAuthSession(); else { saveTokens('account-two', 'refresh-two'); saveUser({ ...user, id: 'account-two' }); }
  release(); const error = await preparation; assert.equal(axios.isCancel(error), true); assert.equal(local.get(BROWSER_DEVICE_KEY), identifier);
  assert.equal([...sessionStore.keys()].some((key) => key.startsWith('saiv_device_registration_v1:')), false);
});
test('the same installation cannot transfer registered status to a new account', async () => {
  await prepareBrowserRegistration(user); const first = user.id; saveTokens('second', 'refresh-second'); user = { ...user, id: 'second' }; saveUser(user);
  const normal = respond; respond = async (config) => {
    if (config.url === '/devices/my-devices') return ok(config, []);
    if (config.url === '/devices/') return fail(config, 409, 'Already registered to another account'); return normal(config);
  };
  await assert.rejects(prepareBrowserRegistration(user), /another account/); assert.equal(randomCalls, 1); assert.notEqual(first, user.id);
});
for (const role of ['ta', 'instructor', 'admin', 'unknown']) test(`${role} cannot start registration or generate an installation identifier`, async () => {
  await assert.rejects(prepareBrowserRegistration({ ...user, role }), /active student/); assert.equal(calls.length, 0); assert.equal(randomCalls, 0);
});
test('registration and check-in share exactly one identifier, with a read-only status recheck before submission', async () => {
  await mount(modal()); await click('Get Current Location'); await click('Submit Check-in');
  assert.deepEqual(calls.map((config) => config.url), ['/devices/my-devices', '/devices/', '/devices/my-devices', '/checkins/']);
  const registration = JSON.parse(calls[1].data); const submission = JSON.parse(calls[3].data);
  assert.equal(submission.device_fingerprint, registration.device_fingerprint); assert.equal(randomCalls, 1); assert.equal(completed.status, 'approved');
});
test('device revocation before submission stops camera and attendance POST while discarding the captured workflow', async () => {
  await mount(modal({ policy: { faceMatchRequired: true, imageRequired: true } })); await click('Get Current Location'); await click('Open Camera');
  devices[0].is_active = false; await click('Submit Check-in'); assert.match(text(), /disabled or revoked/); assert.ok(stopped > 0);
  assert.equal(calls.filter((config) => config.url === '/checkins/').length, 0); assert.equal(calls.filter((config) => config.url === '/devices/').length, 1);
});
test('backend outage during final read-only device check cannot submit attendance', async () => {
  await mount(modal()); await click('Get Current Location'); const normal = respond;
  respond = async (config) => config.url === '/devices/my-devices' ? fail(config, 503, 'private stack') : normal(config);
  await click('Submit Check-in'); assert.equal(button('Submit Check-in'), undefined); assert.equal(calls.filter((config) => config.url === '/checkins/').length, 0);
  assert.match(text(), /required service is unavailable/);
});
test('uncertain attendance can be reconciled without device registration or collecting evidence', async () => {
  markCheckin('device-session', true); await mount(modal()); await click('Check attendance status');
  assert.equal(completed.status, 'flagged'); assert.deepEqual(calls.map((config) => config.url), ['/checkins/me']); assert.equal(cameraCalls + locationCalls, 0);
});
test('fresh attempt after empty attendance reconciliation returns through device registration first', async () => {
  markCheckin('device-session', true); const normal = respond;
  respond = async (config) => config.url === '/checkins/me' ? ok(config, { items: [], total: 0, limit: 100, offset: 0 }) : normal(config);
  await mount(modal()); await click('Check attendance status'); await click('Start a fresh attempt');
  assert.deepEqual(calls.map((config) => config.url), ['/checkins/me', '/devices/my-devices', '/devices/']); assert.equal(cameraCalls + locationCalls, 0);
});
test('minimal metadata and persistence contain no tokens, biometrics, browser characteristics or trust claims', async () => {
  await prepareBrowserRegistration(user);
  const body = JSON.parse(calls.find((config) => config.url === '/devices/').data);
  assert.deepEqual(Object.keys(body).sort(), ['device_fingerprint', 'platform']);
  assert.deepEqual(writes, [[BROWSER_DEVICE_KEY, identifier]]);
  for (const config of calls) { assert.doesNotMatch(config.url, new RegExp(identifier)); assert.equal(config.params, undefined); }
  const markers = [...sessionStore].filter(([key]) => key.startsWith('saiv_device_registration_v1:'));
  assert.deepEqual(markers.map(([, value]) => value), ['observed-or-attempted']);
});
test('a session closing during registration never mounts evidence controls', async () => {
  let release; const selected = target();
  respond = (config) => new Promise((resolve) => { release = () => resolve(ok(config, [existingBrowserFixture(user.id)])); });
  await mount(modal({ session: selected })); selected.checkin_closes_at = new Date(Date.now() - 1).toISOString();
  await act(async () => release()); assert.match(text(), /Check-in window closed/);
  assert.equal(button('Open Camera'), undefined); assert.equal(button('Get Current Location'), undefined);
  assert.equal(cameraCalls + locationCalls, 0);
});
test('missing consent and face enrollment remain prerequisites before registration', async () => {
  await mount(modal({ user: { ...user, face_enrolled: false }, policy: { faceMatchRequired: true, imageRequired: true } }));
  assert.match(text(), /Complete the required consent and face enrollment/); assert.equal(calls.length, 0);
});
test('the gate cannot render a late registration success after account replacement', async () => {
  let release; const normal = respond;
  respond = (config) => config.url === '/devices/' ? new Promise((resolve) => { release = () => resolve(ok(config, existingBrowserFixture(user.id))); }) : normal(config);
  await mount(); assert.match(text(), /Registering this browser/);
  await act(async () => { saveTokens('replacement', 'replacement-refresh'); saveUser({ ...user, id: 'replacement' }); });
  await act(async () => release()); assert.match(text(), /account changed/); assert.doesNotMatch(text(), /Ready:/);
});
test('closing the gate during registration never duplicates a POST on reopening', async () => {
  let release; const normal = respond;
  respond = (config) => config.url === '/devices/' ? new Promise((resolve) => { release = () => { devices = [existingBrowserFixture(user.id)]; resolve(ok(config, devices[0])); }; }) : normal(config);
  await mount(); await act(async () => renderer.unmount()); renderer = undefined;
  await mount(); await act(async () => release()); assert.match(text(), /Ready:/);
  assert.equal(calls.filter((config) => config.method === 'post').length, 1);
});
test('invasive browser characteristics are never read by identifier generation or registration', async () => {
  Object.defineProperty(navigator, 'userAgent', { get() { assert.fail('User agent must not be used for fingerprinting'); } });
  Object.defineProperty(navigator, 'platform', { get() { assert.fail('Platform must not be used for fingerprinting'); } });
  navigator.mediaDevices.enumerateDevices = () => assert.fail('Hardware must not be enumerated');
  await prepareBrowserRegistration(user); assert.equal(cameraCalls + locationCalls, 0);
});

const challenge = (message) => ({ challenge_id: 'A'.repeat(43), message,
  expires_at: new Date(Date.now() + 60000).toISOString() });

async function pilotKey(t, { alreadyEnrolled = false, missingKey = false } = {}) {
  const { subtle } = global.crypto;
  const pair = await subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign', 'verify']);
  const spki = await subtle.exportKey('spki', pair.publicKey);
  const key = { privateKey: pair.privateKey, publicKey: Buffer.from(spki).toString('base64url'),
    keyId: Buffer.from(await subtle.digest('SHA-256', spki)).toString('base64url') };
  global.isSecureContext = true;
  global.indexedDB = {};
  t.after(() => { delete global.isSecureContext; delete global.indexedDB; });
  t.mock.method(browserKeys, 'readBrowserKey', async () => alreadyEnrolled && !missingKey ? key : null);
  const prepare = t.mock.method(browserKeys, 'prepareBrowserKey', async () => key);
  user = { ...user, browser_verification_required: true };
  devices = [existingBrowserFixture(user.id, alreadyEnrolled ? { browser_key_id: missingKey ? 'B'.repeat(43) : key.keyId } : {})];
  const verify = (signature, message) => subtle.verify({ name: 'ECDSA', hash: 'SHA-256' },
    pair.publicKey, Buffer.from(signature, 'base64url'), Buffer.from(message));
  return { key, prepare, verify };
}

async function enterBrowserPassword() {
  const field = { value: 'disposable-test-password' };
  const form = renderer.root.findByType('form');
  await act(async () => form.props.onSubmit({ preventDefault() {}, currentTarget: {
    elements: { namedItem(name) { assert.equal(name, 'browser-password'); return field; } },
  } }));
  assert.equal(field.value, '');
}

test('pilot setup waits for explicit password confirmation and then opens check-in', async (t) => {
  const { key, prepare, verify } = await pilotKey(t);
  const normal = respond;
  respond = async (config) => {
    const body = config.data && JSON.parse(config.data);
    if (config.url === '/devices/browser-key/challenge') {
      assert.deepEqual(body, { device_id: devices[0].id, public_key: key.publicKey });
      return ok(config, challenge('enroll-key'));
    }
    if (config.url === '/devices/browser-key/enroll') {
      assert.equal(body.password, 'disposable-test-password');
      assert.equal(body.public_key, key.publicKey);
      assert.ok(await verify(body.signature, 'enroll-key'));
      devices[0] = { ...devices[0], browser_key_id: key.keyId };
      return ok(config, devices[0]);
    }
    return normal(config);
  };
  await mount(modal());
  assert.match(text(), /Set up browser verification/);
  assert.equal(prepare.mock.callCount(), 0);
  assert.equal(button('Get Current Location'), undefined);
  assert.equal(calls.some(call => call.method === 'post'), false);
  await enterBrowserPassword();
  assert.equal(prepare.mock.callCount(), 1);
  assert.ok(button('Get Current Location'));
  assert.match(text(), /Browser key ready/);
  assert.equal(cameraCalls + locationCalls, 0);
});

test('a missing local key offers explicit recovery bound to the existing registration', async (t) => {
  await pilotKey(t, { alreadyEnrolled: true, missingKey: true });
  const normal = respond;
  respond = async (config) => {
    if (config.url === '/devices/browser-key/challenge') {
      assert.equal(JSON.parse(config.data).recovery_device_id, devices[0].id);
      return ok(config, challenge('recover-key'));
    }
    if (config.url === '/devices/browser-key/enroll') return fail(config, 403, 'Password confirmation failed.');
    return normal(config);
  };
  await mount(modal());
  assert.ok(button('Recover browser key'));
  assert.equal(calls.some(call => call.method === 'post'), false);
  await enterBrowserPassword();
  assert.match(text(), /Password confirmation failed/);
  assert.equal(button('Get Current Location'), undefined);
});

test('pilot check-in sends the exact JSON body signed by the existing browser key', async (t) => {
  const { prepare, verify } = await pilotKey(t, { alreadyEnrolled: true });
  const normal = respond;
  respond = async (config) => {
    if (config.url === '/checkins/browser-challenge') {
      assert.deepEqual(JSON.parse(config.data), { session_id: 'device-session', device_fingerprint: identifier });
      return ok(config, challenge('checkin-key'));
    }
    if (config.url === '/checkins/') {
      const { createHash } = require('node:crypto');
      const digest = createHash('sha256').update(config.data, 'utf8').digest('base64url');
      assert.equal(config.headers['X-SAIV-Browser-Challenge'], 'A'.repeat(43));
      assert.ok(await verify(config.headers['X-SAIV-Browser-Signature'], `checkin-key\n${digest}`));
      assert.equal(JSON.parse(config.data).device_fingerprint, identifier);
      return ok(config, { ...attendance(), browser_key_verified: true });
    }
    return normal(config);
  };
  await mount(modal());
  await click('Get Current Location'); await click('Submit Check-in');
  assert.equal(prepare.mock.callCount(), 0);
  assert.deepEqual(calls.map(call => call.url),
    ['/devices/my-devices', '/devices/my-devices', '/checkins/browser-challenge', '/checkins/']);
  assert.match(text(), /Browser key verified for this attempt/);
});

test('a 401 refreshes the session but never replays a signed check-in', async (t) => {
  await pilotKey(t, { alreadyEnrolled: true });
  const normal = respond;
  respond = async (config) => {
    if (config.url === '/checkins/browser-challenge') return ok(config, challenge('checkin-key'));
    if (config.url === '/checkins/') return fail(config, 401, 'Expired');
    if (config.url === '/auth/refresh') return ok(config, {
      access_token: 'rotated-access', refresh_token: 'rotated-refresh', token_type: 'bearer',
    });
    return normal(config);
  };
  await mount(modal());
  await click('Get Current Location'); await click('Submit Check-in');
  assert.equal(calls.filter(call => call.url === '/checkins/').length, 1);
  assert.equal(calls.filter(call => call.url === '/auth/refresh').length, 1);
  assert.match(text(), /session was refreshed/);
  assert.doesNotMatch(text(), /Submission outcome is unknown/);
});

test('a lost signed check-in response goes through read-only reconciliation', async (t) => {
  await pilotKey(t, { alreadyEnrolled: true });
  const normal = respond;
  respond = async (config) => {
    if (config.url === '/checkins/browser-challenge') return ok(config, challenge('checkin-key'));
    if (config.url === '/checkins/') throw new axios.AxiosError('Timed out', 'ECONNABORTED', config);
    return normal(config);
  };
  await mount(modal());
  await click('Get Current Location'); await click('Submit Check-in');
  assert.match(text(), /Submission outcome is unknown/);
  await click('Check attendance status');
  assert.equal(completed.status, 'flagged');
  assert.equal(calls.filter(call => call.url === '/checkins/').length, 1);
  assert.equal(calls.filter(call => call.url === '/checkins/browser-challenge').length, 1);
});

test('a locally present but expired key prompts explicit recovery before capture', async (t) => {
  const { prepare, key } = await pilotKey(t, { alreadyEnrolled: true });
  devices[0].browser_key_usable = false;
  const normal = respond;
  respond = async (config) => {
    if (config.url === '/devices/browser-key/challenge') {
      assert.equal(JSON.parse(config.data).recovery_device_id, devices[0].id);
      return fail(config, 409, 'Use a new key.');
    }
    return normal(config);
  };
  await mount(modal());
  assert.ok(button('Recover browser key'));
  assert.equal(button('Get Current Location'), undefined);
  assert.equal(prepare.mock.callCount(), 0);
  await enterBrowserPassword();
  assert.equal(prepare.mock.calls[0].arguments[3], key.keyId);
});

test('changing accounts while enrollment challenge is pending stops the password-confirmation POST', async (t) => {
  await pilotKey(t);
  let release;
  const normal = respond;
  respond = (config) => config.url === '/devices/browser-key/challenge'
    ? new Promise(resolve => { release = () => resolve(ok(config, challenge('enroll-key'))); })
    : normal(config);
  await mount(modal());
  await act(async () => {
    void renderer.root.findByType('form').props.onSubmit({ preventDefault() {}, currentTarget: {
      elements: { namedItem() { return { value: 'disposable-test-password' }; } },
    } });
    await new Promise(setImmediate);
  });
  assert.ok(release);
  await act(async () => {
    saveTokens('different-token', 'different-refresh');
    saveUser({ ...user, id: 'different-user' });
    release();
  });
  assert.equal(calls.filter(call => call.url === '/devices/browser-key/enroll').length, 0);
  assert.equal(button('Get Current Location'), undefined);
});

test('a signed submission without server proof confirmation requires reconciliation', async (t) => {
  await pilotKey(t, { alreadyEnrolled: true });
  const normal = respond;
  respond = async config => config.url === '/checkins/browser-challenge'
    ? ok(config, challenge('checkin-key')) : normal(config);
  await mount(modal());
  await click('Get Current Location'); await click('Submit Check-in');
  assert.match(text(), /Submission outcome is unknown/);
  assert.doesNotMatch(text(), /Browser key verified/);
  assert.equal(completed, undefined);
});
