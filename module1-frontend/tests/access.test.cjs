const { test, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { create, act } = require('react-test-renderer');
const axios = require('axios');

const redirects = [];
const pushes = [];
const router = { replace: (path) => redirects.push(path), push: (path) => pushes.push(path) };
function mockModule(name, exports) {
  const id = require.resolve(name);
  require.cache[id] = { id, filename: id, loaded: true, exports };
}
mockModule('next/navigation', {
  useRouter: () => router,
  redirect: (path) => { redirects.push(path); throw new Error('NEXT_REDIRECT'); },
});
mockModule('next/link', { __esModule: true, default: ({ children, ...props }) => React.createElement('a', props, children) });

global.window = {};
global.fetch = async () => Response.json({
  apiUrl: 'http://localhost:8000/api/v1', staffPortalUrl: 'https://staff.saiv.example/portal',
});
const { api } = require('../lib/api.ts');
const { saveTokens, saveUser, getAccessToken, clearAuthSession, getAuthCleanupWarning } = require('../lib/auth.ts');
const Dashboard = require('../app/dashboard/page.tsx').default;
const Home = require('../app/page.tsx').default;
const Login = require('../app/login/page.tsx').default;
const Register = require('../app/register/page.tsx').default;

for (const [Component, field, label] of [
  [Login, 'password', 'Press and hold to show password'],
  [Register, 'password', 'Press and hold to show password'],
  [Register, 'confirmPassword', 'Press and hold to show confirm password'],
]) {
  test(`${Component.name} ${field} supports keyboard hold-to-reveal and hides on release/blur`, async () => {
    await mount(Component);
    const control = renderer.root.findByProps({ 'aria-label': label });
    for (const key of ['Enter', ' ']) {
      await act(async () => control.props.onKeyDown({ key, preventDefault(){} }));
      assert.equal(renderer.root.findByProps({ id: field }).props.type, 'text');
      await act(async () => control.props.onKeyUp({ key, preventDefault(){} }));
      assert.equal(renderer.root.findByProps({ id: field }).props.type, 'password');
    }
    await act(async () => control.props.onKeyDown({ key: 'Enter', preventDefault(){} }));
    await act(async () => control.props.onBlur());
    assert.equal(renderer.root.findByProps({ id: field }).props.type, 'password');
  });
}

let renderer;
let calls;
let actor;
let respond;
let cameraCalls;
const profile = (role = 'student') => ({
  id: 'user-1', email: 'student@example.com', full_name: 'Test User', role, is_active: true,
  camera_consent: false, geolocation_consent: false, face_enrolled: false,
});
const response = (config, data) => ({ data, config, status: 200, statusText: 'OK', headers: {} });
const httpError = (config, status) => new axios.AxiosError('Request rejected', undefined, config, undefined, {
  ...response(config, {}), status,
});
const text = () => JSON.stringify(renderer.toJSON());
const button = (label) => renderer.root.findAllByType('button').find((node) => (node.children.join('') === label || node.props['aria-label'] === label));
const mount = async (Component) => { await act(async () => { renderer = create(React.createElement(Component)); }); };

beforeEach(() => {
  redirects.length = 0;
  pushes.length = 0;
  calls = [];
  actor = profile();
  cameraCalls = 0;
  const storage = new Map();
  global.sessionStorage = {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  };
  clearAuthSession();
  Object.defineProperty(global, 'navigator', { configurable: true, value: {
    mediaDevices: { getUserMedia: async () => { cameraCalls++; throw new Error('unexpected camera request'); } },
  } });
  respond = async (config) => {
    if (config.url === '/auth/me') return response(config, actor);
    if (config.url === '/sessions/active' || config.url === '/courses/me') return response(config, []);
    if (config.url === '/checkins/me') {
      return response(config, { items: [], total: 0, limit: 50, offset: 0 });
    }
    if (config.url === '/auth/login') return response(config, {
      access_token: 'test-access', refresh_token: 'test-refresh', token_type: 'bearer', user: actor,
    });
    if (config.url === '/auth/register') return response(config, actor);
    throw new Error(`Unexpected API call: ${config.url}`);
  };
  api.defaults.adapter = async (config) => {
    calls.push(config);
    return respond(config);
  };
});

afterEach(async () => {
  if (renderer) await act(async () => renderer.unmount());
  renderer = undefined;
  assert.equal(cameraCalls, 0, 'entry/access flows never request camera permission');
});

test('direct dashboard navigation without a token redirects without API or capture', async () => {
  await mount(Dashboard);
  assert.deepEqual(redirects, ['/login']);
  assert.deepEqual(calls, []);
  assert.doesNotMatch(text(), /Account Setup|Enroll Face|Active Sessions/);
});

test('profile-loading state cannot mount student requests or trust a stored user', async () => {
  saveTokens('test-access', 'test-refresh');
  saveUser(profile());
  let finish;
  respond = (config) => new Promise((resolve) => { finish = () => resolve(response(config, profile('instructor'))); });
  await mount(Dashboard);
  assert.match(text(), /Checking your session/);
  assert.deepEqual(calls.map((call) => call.url), ['/auth/me']);
  assert.doesNotMatch(text(), /Active Sessions|Enroll Face/);
  await act(async () => finish());
  assert.match(text(), /SAIV staff access/);
  assert.deepEqual(calls.map((call) => call.url), ['/auth/me']);
});

test('validated students mount the existing dashboard after one profile request', async () => {
  saveTokens('test-access', 'test-refresh');
  await mount(Dashboard);
  assert.match(text(), /Active Sessions/);
  assert.doesNotMatch(text(), /Account Setup|Enroll Face/);
  assert.ok(renderer.root.findAllByProps({ href: '/profile' }).length);
  assert.deepEqual(calls.map((call) => call.url), ['/auth/me', '/sessions/active', '/courses/me', '/checkins/me']);
  assert.deepEqual(redirects, []);
});

for (const role of ['ta', 'instructor', 'admin']) {
  test(`${role} direct navigation shows only staff access and a token-free configured portal link`, async () => {
    actor = profile(role);
    saveTokens('test-access', 'test-refresh');
    saveUser(profile()); // Tampered/stale storage must not determine access.
    await mount(Dashboard);
    assert.match(text(), /SAIV staff access/);
    assert.match(text(), /Sign in separately/);
    assert.doesNotMatch(text(), /Account Setup|Enroll Face|Active Sessions|Recent Attendance/);
    assert.deepEqual(calls.map((call) => call.url), ['/auth/me']);
    const link = renderer.root.findByType('a');
    assert.equal(link.props.href, 'https://staff.saiv.example/portal');
    assert.doesNotMatch(link.props.href, /test-access|test-refresh|token=/);
    await act(async () => button('Sign out').props.onClick());
    assert.equal(getAccessToken(), null);
    assert.deepEqual(redirects, ['/login']);
  });
}

for (const value of [profile('unrecognized'), { ...profile(), is_active: false },
  { ...profile(), is_active: undefined }, { role: 'student' }, null]) {
  test(`unknown, inactive or malformed profile fails closed: ${JSON.stringify(value)}`, async () => {
    actor = value;
    saveTokens('test-access', 'test-refresh');
    await mount(Dashboard);
    assert.match(text(), /Access unavailable/);
    assert.doesNotMatch(text(), /Active Sessions|SAIV staff access/);
    assert.deepEqual(calls.map((call) => call.url), ['/auth/me']);
  });
}

for (const status of [401, 403]) {
  test(`profile ${status} clears authentication and redirects safely`, async () => {
    saveTokens('test-access', 'test-refresh');
    respond = async (config) => { throw httpError(config, status); };
    await mount(Dashboard);
    assert.equal(getAccessToken(), null);
    assert.deepEqual(redirects, ['/login']);
    assert.deepEqual(calls.map((call) => call.url), status === 401 ? ['/auth/me', '/auth/refresh'] : ['/auth/me']);
  });
}

test('network failure offers retry without mounting student functionality', async () => {
  saveTokens('test-access', 'test-refresh');
  const success = respond;
  respond = async () => { throw new Error('offline'); };
  await mount(Dashboard);
  assert.match(text(), /Unable to verify your account/);
  assert.deepEqual(calls.map((call) => call.url), ['/auth/me']);
  respond = success;
  await act(async () => button('Try again').props.onClick());
  assert.match(text(), /Active Sessions/);
});

test('a profile resolving after session replacement never mounts student functionality', async () => {
  saveTokens('test-access', 'test-refresh');
  let finish;
  respond = (config) => new Promise((resolve) => { finish = () => resolve(response(config, profile())); });
  await mount(Dashboard);
  saveTokens('different-session', 'different-refresh');
  await act(async () => finish());
  assert.deepEqual(redirects, ['/login']);
  assert.deepEqual(calls.map((call) => call.url), ['/auth/me']);
  assert.doesNotMatch(text(), /Active Sessions/);
});

test('unmount during profile loading aborts the request and discards the result', async () => {
  saveTokens('test-access', 'test-refresh');
  let finish;
  respond = (config) => new Promise((resolve) => { finish = () => resolve(response(config, profile())); });
  await mount(Dashboard);
  const signal = calls[0].signal;
  await act(async () => renderer.unmount());
  renderer = undefined;
  assert.equal(signal.aborted, true);
  await act(async () => finish());
  assert.deepEqual(redirects, []);
  assert.deepEqual(calls.map((call) => call.url), ['/auth/me']);
});

test('root routes through the same validated entry for anonymous, student and staff users', async () => {
  for (const role of [null, 'student', 'ta', 'instructor', 'admin', 'unrecognized']) {
    redirects.length = 0;
    calls.length = 0;
    sessionStorage.removeItem('saiv_access_token');
    if (role) { actor = profile(role); saveTokens('test-access', 'test-refresh'); }
    assert.throws(() => Home(), /NEXT_REDIRECT/);
    assert.deepEqual(redirects, ['/dashboard']);
    await mount(Dashboard);
    if (!role) assert.deepEqual(redirects, ['/dashboard', '/login']);
    else if (role === 'student') assert.match(text(), /Active Sessions/);
    else if (role === 'unrecognized') assert.match(text(), /Access unavailable/);
    else assert.match(text(), /SAIV staff access/);
    await act(async () => renderer.unmount());
    renderer = undefined;
  }
});

test('login still trims email, stores tokens and routes every allowed role through the gate', async () => {
  for (const role of ['student', 'ta', 'instructor', 'admin']) {
    actor = profile(role);
    await mount(Login);
    await act(async () => {
      renderer.root.findByProps({ id: 'email' }).props.onChange({ target: { value: ' student@example.com ' } });
      renderer.root.findByProps({ id: 'password' }).props.onChange({ target: { value: 'valid-password' } });
    });
    await act(async () => renderer.root.findByType('form').props.onSubmit({ preventDefault() {} }));
    const request = calls.at(-1);
    assert.equal(request.url, '/auth/login');
    assert.deepEqual(JSON.parse(request.data), { email: 'student@example.com', password: 'valid-password' });
    assert.equal(getAccessToken(), 'test-access');
    assert.equal(pushes.at(-1), '/dashboard');
    await act(async () => renderer.unmount());
    renderer = undefined;
  }
});

test('login validation and invalid-credentials feedback are preserved', async () => {
  await mount(Login);
  const submit = async () => act(async () => renderer.root.findByType('form').props.onSubmit({ preventDefault() {} }));
  await submit();
  assert.match(text(), /Please enter your email and password/);
  assert.equal(calls.length, 0);
  await act(async () => {
    renderer.root.findByProps({ id: 'email' }).props.onChange({ target: { value: 'student@example.com' } });
    renderer.root.findByProps({ id: 'password' }).props.onChange({ target: { value: 'wrong' } });
  });
  respond = async (config) => { throw httpError(config, 401); };
  await submit();
  assert.match(text(), /Invalid email or password/);
  assert.equal(pushes.length, 0);
});

test('registration retains all professor-allowed roles and its existing request/navigation', async () => {
  for (const role of ['student', 'ta', 'instructor', 'admin']) {
    await mount(Register);
    assert.deepEqual(renderer.root.findAllByType('option').map((node) => node.props.value),
      ['', 'student', 'ta', 'instructor', 'admin']);
    await act(async () => {
      for (const [id, value] of Object.entries({ fullName: 'Test User', email: 'student@example.com',
        password: 'valid-password', confirmPassword: 'valid-password', role })) {
        renderer.root.findByProps({ id }).props.onChange({ target: { value } });
      }
    });
    await act(async () => renderer.root.findByType('form').props.onSubmit({ preventDefault() {} }));
    assert.equal(calls.at(-1).url, '/auth/register');
    assert.deepEqual(JSON.parse(calls.at(-1).data), {
      email: 'student@example.com', password: 'valid-password', full_name: 'Test User', role,
    });
    assert.equal(pushes.at(-1), '/login');
    await act(async () => renderer.unmount());
    renderer = undefined;
  }
});

test('registration password length and confirmation validation remain enforced', async () => {
  await mount(Register);
  await act(async () => renderer.root.findByType('form').props.onSubmit({ preventDefault() {} }));
  assert.match(text(), /Password must be at least 8 characters/);
  await act(async () => renderer.root.findByProps({ id: 'password' }).props.onChange({ target: { value: 'valid-password' } }));
  await act(async () => renderer.root.findByType('form').props.onSubmit({ preventDefault() {} }));
  assert.match(text(), /Passwords do not match/);
  assert.equal(calls.length, 0);
});

test('profile validation survives token rotation and still separates recovered students from staff', async () => {
  for (const role of ['student', 'instructor']) {
    calls.length = 0;
    actor = profile(role);
    saveTokens('expired-access', 'test-refresh');
    const normal = respond;
    respond = async (config) => {
      if (config.url === '/auth/refresh') return response(config, {
        access_token: 'recovered-access', refresh_token: 'rotated-refresh', token_type: 'bearer',
      });
      if (config.url === '/auth/me' && config.headers.Authorization === 'Bearer expired-access') throw httpError(config, 401);
      return normal(config);
    };
    await mount(Dashboard);
    assert.equal(getAccessToken(), 'recovered-access');
    assert.equal(calls.filter((call) => call.url === '/auth/refresh').length, 1);
    if (role === 'student') assert.match(text(), /Active Sessions/);
    else {
      assert.match(text(), /SAIV staff access/);
      assert.doesNotMatch(text(), /Active Sessions/);
      assert.equal(calls.filter((call) => call.url === '/sessions/active').length, 0);
    }
    await act(async () => renderer.unmount()); renderer = undefined;
  }
});

test('browser back-cache restoration revalidates and cannot revive a locally logged-out dashboard', async () => {
  const handlers = new Map();
  window.addEventListener = (name, fn) => handlers.set(name, fn);
  window.removeEventListener = (name) => handlers.delete(name);
  saveTokens('test-access', 'test-refresh');
  await mount(Dashboard);
  await act(async () => handlers.get('pagehide')());
  assert.doesNotMatch(text(), /Active Sessions/);
  sessionStorage.removeItem('saiv_access_token');
  await act(async () => handlers.get('pageshow')({ persisted: true }));
  assert.equal(redirects.at(-1), '/login');
  assert.doesNotMatch(text(), /Active Sessions/);
  delete window.addEventListener; delete window.removeEventListener;
});

test('denied authentication storage on direct navigation never mounts protected requests or permission controls', async () => {
  saveTokens('test-access', 'test-refresh'); saveUser(profile());
  sessionStorage.getItem = () => { throw new DOMException('private storage diagnostic', 'SecurityError'); };
  await mount(Dashboard);
  assert.equal(redirects.at(-1), '/login');
  assert.equal(calls.length, 0);
  assert.doesNotMatch(text(), /Account Setup|Enroll Face|Active Sessions|private storage/);
});

test('logout hides a validated dashboard even when authentication storage removal is denied', async () => {
  saveTokens('test-access', 'test-refresh'); saveUser(profile());
  await mount(Dashboard); assert.match(text(), /Active Sessions/);
  sessionStorage.removeItem = () => { throw new DOMException('private storage diagnostic', 'SecurityError'); };
  await act(async () => button('Sign out').props.onClick());
  assert.equal(getAccessToken(), null);
  assert.equal(redirects.at(-1), '/login');
  assert.doesNotMatch(text(), /Account Setup|Enroll Face|Active Sessions|private storage/);
});

test('login reports denied token storage without navigating into the protected dashboard', async () => {
  await mount(Login);
  await act(async () => {
    renderer.root.findByProps({ id: 'email' }).props.onChange({ target: { value: 'student@example.com' } });
    renderer.root.findByProps({ id: 'password' }).props.onChange({ target: { value: 'synthetic-password' } });
  });
  sessionStorage.setItem = () => { throw new DOMException('private storage diagnostic', 'SecurityError'); };
  await act(async () => renderer.root.findByType('form').props.onSubmit({ preventDefault() {} }));
  assert.match(text(), /Browser session storage is unavailable.*Allow site storage/);
  assert.doesNotMatch(text(), /private storage diagnostic/);
  assert.equal(getAccessToken(), null);
  assert.deepEqual(pushes, []);
});

for (const role of ['student', 'instructor']) {
  test(`${role} logout with blocked cleanup displays a safe warning after redirecting to login`, async () => {
    actor = profile(role); saveTokens('test-access', 'test-refresh'); saveUser(actor);
    await mount(Dashboard);
    sessionStorage.removeItem = () => { throw new DOMException('private storage diagnostic', 'SecurityError'); };
    sessionStorage.setItem = () => { throw new DOMException('private storage diagnostic', 'SecurityError'); };
    await act(async () => button('Sign out').props.onClick());
    assert.equal(redirects.at(-1), '/login');
    assert.equal(getAccessToken(), null);
    assert.doesNotMatch(text(), /Active Sessions|SAIV staff access/);
    await act(async () => renderer.unmount()); renderer = undefined;
    await mount(Login);
    assert.match(text(), /couldn't confirm.*Clear this site's browser data.*before sharing this device/);
    assert.match(text(), /Reloading may restore your previous sign-in/);
    assert.doesNotMatch(text(), /private storage diagnostic|test-access|test-refresh|JWT|sessionStorage/);
    assert.ok(renderer.root.findAllByProps({ role: 'alert' }).length > 0);
  });
}

test('verified fallback logout displays no incomplete-cleanup warning on the login page', async () => {
  saveTokens('test-access', 'test-refresh'); saveUser(profile());
  await mount(Dashboard);
  sessionStorage.removeItem = () => { throw new DOMException('denied removal', 'SecurityError'); };
  await act(async () => button('Sign out').props.onClick());
  assert.equal(getAuthCleanupWarning(), null);
  await act(async () => renderer.unmount()); renderer = undefined;
  await mount(Login);
  assert.doesNotMatch(text(), /couldn't confirm|Clear this site's browser data/);
});

test('the login warning updates when cleanup later becomes verifiable without a page reload', async () => {
  await mount(Login);
  saveTokens('test-access', 'test-refresh');
  const originalRemove = sessionStorage.removeItem; const originalSet = sessionStorage.setItem;
  sessionStorage.removeItem = () => { throw new DOMException('denied', 'SecurityError'); };
  sessionStorage.setItem = () => { throw new DOMException('denied', 'SecurityError'); };
  await act(async () => clearAuthSession());
  assert.match(text(), /Clear this site's browser data/);
  sessionStorage.removeItem = originalRemove; sessionStorage.setItem = originalSet;
  await act(async () => clearAuthSession());
  assert.doesNotMatch(text(), /Clear this site's browser data/);
});
