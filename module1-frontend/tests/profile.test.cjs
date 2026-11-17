const { test, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { create, act } = require('react-test-renderer');
const axios = require('axios');
const redirects = [];
const router = { replace: path => redirects.push(path), push: path => redirects.push(path) };
function mock(name, exports) { const id = require.resolve(name); require.cache[id] = { id, filename: id, loaded: true, exports }; }
mock('next/navigation', { useRouter: () => router });
mock('next/link', { __esModule: true, default: ({ children, ...props }) => React.createElement('a', props, children) });
global.window = {};
global.fetch = async () => Response.json({ apiUrl: 'http://localhost:8000', staffPortalUrl: 'http://localhost:8501' });
const { api } = require('../lib/api.ts');
const auth = require('../lib/auth.ts');
const Profile = require('../app/profile/page.tsx').default;
const Dashboard = require('../app/dashboard/page.tsx').default;
const Header = require('../components/ui/StudentHeader.tsx').default;
const BrowserRegistration = require('../components/student/BrowserRegistration.tsx').default;
const { shortIdentifier } = require('../components/student/BrowserRegistration.tsx');
const { readBrowserDeviceIdentifier, BROWSER_DEVICE_KEY } = require('../lib/browser-device.ts');
const browserKeys = require('../lib/browser-key.ts');
const { identifier, existingBrowserFixture } = require('./device-fixture.cjs');
let renderer, actor, calls, cameraCalls, locationCalls, respond, local, writes;
const profile = (changes = {}) => ({ id: 'student-1', email: 'disposable@example.test', full_name: 'Example Student',
  role: 'student', is_active: true, camera_consent: false, geolocation_consent: false, face_enrolled: false, ...changes });
const ok = (config, data) => ({ config, data, status: 200, statusText: 'OK', headers: {} });
const text = () => JSON.stringify(renderer.toJSON());
const renderedText = node => node.children.map(child => typeof child === 'string' ? child : renderedText(child)).join(' ');
const mount = async () => { await act(async () => { renderer = create(React.createElement(Profile)); }); };
const click = async label => { await act(async () => renderer.root.findAllByType('button').find(n => n.children.join('') === label).props.onClick()); };
beforeEach(() => {
  calls = []; redirects.length = 0; cameraCalls = 0; locationCalls = 0; actor = profile();
  const storage = new Map();
  global.sessionStorage = { getItem: k => storage.get(k) ?? null, setItem: (k,v) => storage.set(k,v), removeItem: k => storage.delete(k) };
  local = new Map(); writes = [];
  global.window = { localStorage: { getItem: k => local.get(k) ?? null, setItem: (k,v) => { writes.push([k,v]); local.set(k,v); } },
    crypto: { randomUUID() { throw Error('Profile must not generate a UUID'); } } };
  Object.defineProperty(global, 'navigator', { configurable: true, value: {
    mediaDevices: { getUserMedia: async () => { cameraCalls++; return { getTracks: () => [{stop(){}}] }; } },
    geolocation: { getCurrentPosition: done => { locationCalls++; done({ coords: { latitude: 1, longitude: 103, accuracy: 10 } }); } },
  } });
  auth.clearAuthSession(); auth.saveTokens('test-access', 'test-refresh');
  respond = async config => {
    if (config.url === '/auth/me' && config.method === 'get') return ok(config, actor);
    if (config.url === '/auth/me' && config.method === 'patch') { actor = { ...actor, ...JSON.parse(config.data) }; return ok(config, actor); }
    if (config.url === '/devices/my-devices') return ok(config, []);
    throw Error(`Unexpected call ${config.method} ${config.url}`);
  };
  api.defaults.adapter = async config => { calls.push(config); return respond(config); };
});
afterEach(async () => { if (renderer) await act(async () => renderer.unmount()); renderer = undefined; });

test('profile without credentials redirects before account/device requests or permissions', async () => {
  auth.clearAuthSession(); await mount();
  assert.deepEqual(redirects, ['/login']); assert.deepEqual(calls, []);
  assert.equal(cameraCalls, 0); assert.equal(locationCalls, 0);
});
test('profile validates through AuthGuard and shows real account and saved consent without permission prompts', async () => {
  await mount();
  assert.match(text(), /Example Student/); assert.match(text(), /disposable@example.test/);
  assert.match(text(), /Not given/); assert.match(text(), /Not enrolled/);
  assert.match(text(), /SAIV consent and browser permissions are separate/);
  assert.equal(cameraCalls, 0); assert.equal(locationCalls, 0);
  assert.ok(calls.every(c => c.method === 'get'));
  assert.ok(!calls.some(c => c.url.startsWith('/sessions') || c.url.startsWith('/checkins')));
});
test('staff profile access retains the existing staff boundary', async () => {
  actor = profile({ role: 'instructor' }); await mount();
  assert.match(text(), /SAIV staff access/); assert.doesNotMatch(text(), /Verification & Consent/);
  assert.deepEqual(calls.map(c => c.url), ['/auth/me']);
});
test('profile navigation points to Next.js settings and dashboard', async () => {
  await act(async () => { renderer = create(React.createElement(Header, { current: 'dashboard', onSignOut(){} })); });
  const links = renderer.root.findAllByType('a');
  assert.ok(links.some(n => n.props.href === '/profile' && n.children.join('') === 'Profile & Settings'));
  assert.ok(links.some(n => n.props.href === '/dashboard' && n.props['aria-current'] === 'page'));
});
test('opening setup does not request browser access; explicit camera action and consent save do', async () => {
  await mount(); await click('Set up camera'); assert.equal(cameraCalls, 0);
  await click('Request camera access'); assert.equal(cameraCalls, 1);
  await click('Confirm camera consent');
  assert.match(text(), /Consented/);
  assert.deepEqual(JSON.parse(calls.find(c => c.method === 'patch').data), { camera_consent: true });
});
test('explicit location setup saves backend consent and never automatically requests a camera', async () => {
  await mount(); await click('Set up location'); assert.equal(locationCalls, 0);
  await click('Request location access');
  await click('Confirm location consent');
  assert.match(text(), /Consented/); assert.equal(cameraCalls, 0);
  assert.deepEqual(JSON.parse(calls.find(c => c.method === 'patch').data), { geolocation_consent: true });
});
test('revocation uses the existing API and adopts returned enrollment state', async () => {
  actor = profile({ camera_consent: true, geolocation_consent: true, face_enrolled: true });
  respond = async config => {
    if (config.url === '/devices/my-devices') return ok(config, []);
    if (config.method === 'patch') { actor = { ...actor, camera_consent: false, face_enrolled: false }; }
    return ok(config, actor);
  };
  await mount(); await click('Revoke camera consent');
  assert.match(text(), /Not enrolled/); assert.match(text(), /Not given/);
  assert.deepEqual(JSON.parse(calls.find(c => c.method === 'patch').data), { camera_consent: false });
});
test('consent API failure remains visible without claiming saved consent', async () => {
  actor = profile({ camera_consent: true }); await mount();
  respond = async config => { throw new axios.AxiosError('offline', 'ERR_NETWORK', config); };
  await click('Revoke camera consent');
  assert.match(text(), /Consented/); assert.ok(renderer.root.findAllByProps({ role: 'alert' }).length);
});

test('dashboard omits setup controls and retains the profile navigation destination', async () => {
  respond = async config => {
    if (config.url === '/auth/me') return ok(config, actor);
    if (config.url === '/checkins/me') return ok(config, { items: [], total: 0, limit: 50, offset: 0 });
    return ok(config, []);
  };
  await act(async () => { renderer = create(React.createElement(Dashboard)); });
  assert.equal(renderer.root.findAllByProps({ id: 'setup-title' }).length, 0);
  assert.doesNotMatch(text(), /Account Setup|Set up camera|Revoke camera consent|Enroll Face/);
  assert.ok(renderer.root.findAllByProps({ href: '/profile' }).length);
  for (const id of ['sessions-title', 'courses-title', 'history-title']) assert.equal(renderer.root.findAllByType('h2').filter(node => node.props.id === id).length, 1);
});
test('joined metadata displays a Singapore calendar date without a time', async () => {
  actor = profile({ created_at: '2026-11-04T23:30:00Z' }); await mount();
  const joined = renderer.root.findByProps({ className: 'joined-date' });
  assert.match(renderedText(joined), /5 Nov 2026/);
  assert.doesNotMatch(renderedText(joined), /7:30|am|pm/);
});
test('browser metadata is expandable and includes no invented browser or OS', async () => {
  local.set(BROWSER_DEVICE_KEY, identifier);
  respond = async config => ok(config, config.url === '/devices/my-devices' ? [existingBrowserFixture(actor.id)] : actor);
  await mount();
  const row = renderer.root.findByProps({ className: 'browser-row' });
  assert.equal(row.type, 'details'); assert.notEqual(row.props.open, true);
  const summary = row.findByType('summary');
  const output = renderedText(summary);
  assert.match(text(), /This Browser/); assert.doesNotMatch(output, /First registered|Last seen|Chrome|Android|Windows/);
  assert.match(text(), /First registered/); assert.match(text(), /Last seen/);
});

const enrolledKeyId = 'A'.repeat(43);
const signingKeyLabel = row => row.findByType('dl').findAllByType('dd').at(-1).children.join('');

for (const [name, localKey, expected] of [
  ['matching', { keyId: enrolledKeyId }, 'Key enrolled · Available here'],
  ['missing', null, 'Recovery required · No matching local key'],
  ['different', { keyId: 'B'.repeat(43) }, 'Recovery required · No matching local key'],
  ['unreadable', undefined, 'Unavailable'],
]) {
  test(`current browser reports local key state (${name}) without enrolling`, async t => {
    local.set(BROWSER_DEVICE_KEY, identifier);
    const read = t.mock.method(browserKeys, 'readBrowserKey', async () => {
      if (localKey === undefined) throw new Error('Storage unavailable');
      return localKey;
    });
    const prepare = t.mock.method(browserKeys, 'prepareBrowserKey', () => assert.fail('Profile must not create a key'));
    respond = async config => ok(config, config.url === '/devices/my-devices'
      ? [existingBrowserFixture(actor.id, { browser_key_id: enrolledKeyId, browser_key_usable: true })] : actor);
    await mount();
    const row = renderer.root.findByProps({ className: 'browser-row' });
    assert.equal(signingKeyLabel(row), expected);
    assert.match(renderedText(row.findByType('summary')).replace(/\s+/g, ' '), /This Browser.*web · low trust Active/i);
    assert.equal(read.mock.callCount(), 1);
    assert.deepEqual(read.mock.calls[0].arguments, [actor.id, identifier]);
    assert.equal(prepare.mock.callCount(), 0);
    assert.ok(calls.every(call => call.method === 'get'));
    assert.deepEqual(writes, []);
  });
}

test('profile reports absent and unavailable keys separately and uses only server metadata for other browsers', async t => {
  local.set(BROWSER_DEVICE_KEY, identifier);
  const read = t.mock.method(browserKeys, 'readBrowserKey', () => assert.fail('No usable current key to look up'));
  const devices = [
    existingBrowserFixture(actor.id, { browser_key_id: null, browser_key_usable: false }),
    existingBrowserFixture(actor.id, { id: 'other-enrolled', device_fingerprint: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
      browser_key_id: enrolledKeyId, browser_key_usable: true }),
    existingBrowserFixture(actor.id, { id: 'other-inactive', device_fingerprint: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
      browser_key_id: 'C'.repeat(43), browser_key_usable: false, is_active: false }),
    existingBrowserFixture(actor.id, { id: 'other-legacy', device_fingerprint: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd' }),
  ];
  respond = async config => ok(config, config.url === '/devices/my-devices' ? devices : actor);
  await mount();
  const rows = renderer.root.findAllByProps({ className: 'browser-row' });
  assert.deepEqual(rows.map(signingKeyLabel), ['Not enrolled', 'Key enrolled (server record)', 'Unavailable', 'Unavailable']);
  assert.match(renderedText(rows[1].findByType('summary')), /Other Browser/);
  const inactive = renderedText(rows[2].findByType('summary'));
  assert.match(inactive, /Inactive/);
  assert.doesNotMatch(inactive, /Revoked/);
  assert.equal(read.mock.callCount(), 0);

  devices[0] = existingBrowserFixture(actor.id, { browser_key_id: enrolledKeyId, browser_key_usable: false });
  const refresh = renderer.root.findAllByType('button').find(node => node.props['aria-label'] === 'Refresh browsers');
  await act(async () => refresh.props.onClick());
  assert.equal(signingKeyLabel(renderer.root.findAllByProps({ className: 'browser-row' })[0]), 'Unavailable');
  assert.equal(read.mock.callCount(), 0);
});

test('read-only identifier lookup never generates or repairs an absent or malformed UUID', () => {
  assert.equal(readBrowserDeviceIdentifier(), null);
  local.set(BROWSER_DEVICE_KEY, 'malformed'); assert.equal(readBrowserDeviceIdentifier(), null);
  local.set(BROWSER_DEVICE_KEY, identifier); assert.equal(readBrowserDeviceIdentifier(), identifier);
  assert.deepEqual(writes, []);
});
test('browser identifiers use a readable shortened Unicode identifier', () => {
  assert.equal(shortIdentifier(identifier), 'aaaaaaaa…aaaa');
});
test('profile reads existing devices without generating an identifier or registering anything', async () => {
  await mount();
  assert.match(text(), /No saved browser identifier/);
  assert.ok(calls.some(c => c.url === '/devices/my-devices'));
  assert.ok(calls.every(c => c.method === 'get')); assert.deepEqual(writes, []);
  assert.equal(cameraCalls, 0); assert.equal(locationCalls, 0);
});
for (const isActive of [true, false]) {
  test(`current registered browser displays active=${isActive}, shortened identity and backend metadata`, async () => {
    local.set(BROWSER_DEVICE_KEY, identifier);
    respond = async config => ok(config, config.url === '/devices/my-devices'
      ? [existingBrowserFixture(actor.id, { is_active: isActive, browser: 'Chrome' })] : actor);
    await mount();
    assert.match(text(), isActive ? /Active/ : /Inactive/);
    assert.match(text(), /Backend Trust Assessment/); assert.match(text(), /Chrome/);
    assert.doesNotMatch(text(), new RegExp(identifier)); assert.deepEqual(writes, []);
    assert.ok(calls.every(c => c.method === 'get'));
  });
}
test('an existing local identifier without an account record is unregistered for this account', async () => {
  local.set(BROWSER_DEVICE_KEY, identifier); await mount();
  assert.match(text(), /Not registered to this account/); assert.deepEqual(writes, []);
});
test('device API errors do not expose identities or manufacture registration success', async () => {
  local.set(BROWSER_DEVICE_KEY, identifier);
  respond = async config => { if (config.url === '/devices/my-devices') throw Error(identifier); return ok(config, actor); };
  await mount(); assert.match(text(), /information is unavailable/); assert.doesNotMatch(text(), new RegExp(identifier));
  assert.doesNotMatch(text(), /Active/); assert.deepEqual(writes, []);
});
test('foreign-account device responses fail closed without revealing metadata', async () => {
  respond = async config => ok(config, config.url === '/devices/my-devices'
    ? [existingBrowserFixture('foreign-owner', { browser: 'Other-account-private-browser' })] : actor);
  await mount(); assert.match(text(), /information is unavailable/);
  assert.doesNotMatch(text(), /Other-account-private-browser/);
});
test('account switching aborts an old device lookup and cannot show its late response', async () => {
  let finishOld; let count = 0;
  respond = async config => {
    count++;
    if (count === 1) return new Promise(resolve => { finishOld = () => resolve(ok(config, [existingBrowserFixture('student-1', { browser: 'Old-private-browser' })])); });
    return ok(config, [existingBrowserFixture('student-2', { browser: 'New-browser' })]);
  };
  await act(async () => { renderer = create(React.createElement(BrowserRegistration, { userId: 'student-1' })); });
  auth.saveTokens('new-access', 'new-refresh');
  await act(async () => renderer.update(React.createElement(BrowserRegistration, { userId: 'student-2' })));
  await act(async () => finishOld());
  assert.match(text(), /New-browser/); assert.doesNotMatch(text(), /Old-private-browser/);
  assert.deepEqual(writes, []); assert.ok(calls.every(c => c.method === 'get'));
});
