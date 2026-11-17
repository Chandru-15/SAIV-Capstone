const { test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const axios = require('axios');
global.window = {};
global.fetch = async () => Response.json({ apiUrl: 'http://localhost:8000', staffPortalUrl: 'http://localhost:8501' });
const { api } = require('../lib/api.ts');
const auth = require('../lib/auth.ts');
const { logoutUser, loginUser, registerUser, getCurrentUser, updateConsent } = require('../lib/auth-api.ts');
const { parseApiError } = require('../lib/api-error.ts');
const ok = (config, data = {}) => ({ config, data, status: 200, statusText: 'OK', headers: {} });
const fail = (config, status) => { throw new axios.AxiosError('HTTP failure', undefined, config, undefined,
  { ...ok(config, { code: 'authentication_error', message: 'Invalid or expired token', details: null }), status }); };
const tokens = { access_token: 'new-access', refresh_token: 'new-refresh', token_type: 'bearer' };
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const settle = () => new Promise((resolve) => setImmediate(resolve));

beforeEach(() => {
  const storage = new Map();
  global.sessionStorage = { getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) };
  auth.saveTokens('old-access', 'old-refresh');
});

test('401 recovers with current JSON refresh contract and rotates both tokens', async () => {
  const paths = [];
  api.defaults.adapter = async (config) => {
    paths.push(config.url);
    if (config.url === '/auth/refresh') {
      assert.equal(config.headers.Authorization, undefined);
      assert.deepEqual(JSON.parse(config.data), { refresh_token: 'old-refresh' });
      return ok(config, tokens);
    }
    if (config.headers.Authorization === 'Bearer old-access') return fail(config, 401);
    assert.equal(config.headers.Authorization, 'Bearer new-access');
    return ok(config, { recovered: true });
  };
  const version = auth.getSessionVersion();
  assert.deepEqual((await api.get('/auth/me')).data, { recovered: true });
  assert.deepEqual(paths, ['/auth/me', '/auth/refresh', '/auth/me']);
  assert.equal(auth.getAccessToken(), 'new-access');
  assert.equal(auth.getRefreshToken(), 'new-refresh');
  assert.equal(auth.getSessionVersion(), version, 'rotation preserves the validated identity');
});

test('concurrent and late old-token 401s share one refresh operation', async () => {
  const gate = deferred();
  const late = deferred();
  let refreshes = 0;
  api.defaults.adapter = async (config) => {
    if (config.url === '/auth/refresh') { refreshes++; await gate.promise; return ok(config, tokens); }
    if (config.headers.Authorization === 'Bearer old-access') {
      if (config.url === '/late') await late.promise;
      return fail(config, 401);
    }
    return ok(config);
  };
  const requests = [api.get('/one'), api.get('/two'), api.get('/late')];
  await settle();
  assert.equal(refreshes, 1);
  gate.resolve();
  await Promise.all(requests.slice(0, 2));
  late.resolve();
  await requests[2];
  assert.equal(refreshes, 1);
});

for (const mode of ['invalid', 'network', 'malformed', 'missing']) {
  test(`refresh ${mode} clears authentication without retry loops`, async () => {
    let refreshes = 0;
    let requests = 0;
    if (mode === 'missing') sessionStorage.removeItem('saiv_refresh_token');
    api.defaults.adapter = async (config) => {
      if (config.url === '/auth/refresh') {
        refreshes++;
        if (mode === 'network') throw new axios.AxiosError('offline', 'ERR_NETWORK', config);
        if (mode === 'malformed') return ok(config, { access_token: 'partial' });
        return fail(config, 401);
      }
      requests++;
      return fail(config, 401);
    };
    await assert.rejects(api.get('/auth/me'));
    assert.equal(auth.getAccessToken(), null);
    assert.equal(auth.getRefreshToken(), null);
    assert.equal(refreshes, mode === 'missing' ? 0 : 1);
    assert.equal(requests, 1);
  });
}

test('concurrent failed refresh clears identity once and rejects every waiter', async () => {
  let refreshes = 0;
  let changes = 0;
  const gate = deferred();
  const unsubscribe = auth.subscribeAuthSession(() => { changes++; });
  api.defaults.adapter = async (config) => {
    if (config.url === '/auth/refresh') { refreshes++; await gate.promise; return fail(config, 401); }
    return fail(config, 401);
  };
  const results = Promise.allSettled([api.get('/one'), api.get('/two'), api.get('/three')]);
  await settle(); gate.resolve();
  assert.ok((await results).every((result) => result.status === 'rejected'));
  unsubscribe();
  assert.equal(refreshes, 1);
  assert.equal(changes, 1);
});

test('a second 401 after recovery never refreshes or retries again', async () => {
  let refreshes = 0; let requests = 0;
  api.defaults.adapter = async (config) => {
    if (config.url === '/auth/refresh') { refreshes++; return ok(config, tokens); }
    requests++; return fail(config, 401);
  };
  await assert.rejects(api.get('/auth/me'));
  assert.equal(refreshes, 1); assert.equal(requests, 2); assert.equal(auth.getAccessToken(), null);
});

test('ordinary 403 does not refresh or clear an authenticated session', async () => {
  let calls = 0;
  api.defaults.adapter = async (config) => { calls++; return fail(config, 403); };
  await assert.rejects(api.patch('/auth/me', { camera_consent: true }), (error) => error.response.status === 403);
  assert.equal(calls, 1); assert.equal(auth.getAccessToken(), 'old-access');
});

for (const status of [200, 401, 500, 'offline']) {
  test(`logout audit ${status} always clears locally and uses the captured bearer`, async () => {
    auth.saveUser({ id: 'old-user' });
    const paths = [];
    api.defaults.adapter = async (config) => {
      paths.push(config.url);
      assert.equal(config.headers.Authorization, 'Bearer old-access');
      assert.equal(auth.getAccessToken(), null, 'local logout does not wait for the server');
      if (status === 'offline') throw new axios.AxiosError('offline', 'ERR_NETWORK', config);
      if (status !== 200) return fail(config, status);
      return ok(config);
    };
    await logoutUser();
    assert.equal(auth.getAccessToken(), null); assert.equal(auth.getStoredUser(), null);
    assert.deepEqual(paths, ['/auth/logout']);
  });
}

test('logout during refresh never restores the old session or replays the original request', async () => {
  const gate = deferred(); let originals = 0;
  api.defaults.adapter = async (config) => {
    if (config.url === '/auth/logout') return ok(config);
    if (config.url === '/auth/refresh') { await gate.promise; return ok(config, tokens); }
    originals++; return fail(config, 401);
  };
  const request = api.get('/auth/me').catch((error) => error);
  await settle(); await logoutUser(); gate.resolve();
  assert.ok(axios.isCancel(await request));
  assert.equal(auth.getAccessToken(), null); assert.equal(originals, 1);
});

test('account replacement during refresh preserves the replacement tokens', async () => {
  const gate = deferred();
  api.defaults.adapter = async (config) => {
    if (config.url === '/auth/refresh') { await gate.promise; return ok(config, tokens); }
    return fail(config, 401);
  };
  const request = api.get('/auth/me').catch((error) => error);
  await settle(); auth.saveTokens('account-b-access', 'account-b-refresh'); gate.resolve();
  assert.ok(axios.isCancel(await request));
  assert.equal(auth.getAccessToken(), 'account-b-access');
  assert.equal(auth.getRefreshToken(), 'account-b-refresh');
});

test('delayed login cannot overwrite a newer account or restore a signed-out session', async () => {
  for (const replace of [true, false]) {
    const gate = deferred();
    api.defaults.adapter = async (config) => { await gate.promise; return ok(config, { ...tokens, user: { id: 'old-user' } }); };
    const request = loginUser({ email: 'old@example.com', password: 'private-password' }).catch((error) => error);
    await settle();
    if (replace) auth.saveTokens('account-b', 'refresh-b'); else auth.clearAuthSession();
    gate.resolve();
    assert.ok(axios.isCancel(await request));
    assert.equal(auth.getAccessToken(), replace ? 'account-b' : null);
  }
});

test('profile and consent responses from the previous account cannot overwrite stored user data', async () => {
  for (const call of [() => getCurrentUser(), () => updateConsent({ camera_consent: true })]) {
    auth.saveTokens('account-a', 'refresh-a');
    const gate = deferred();
    api.defaults.adapter = async (config) => { await gate.promise; return ok(config, { id: 'account-a' }); };
    const request = call().catch((error) => error);
    await settle(); auth.saveTokens('account-b', 'refresh-b'); auth.saveUser({ id: 'account-b' }); gate.resolve();
    assert.ok(axios.isCancel(await request));
    assert.equal(auth.getStoredUser().id, 'account-b');
  }
});

test('request identity is captured before asynchronous runtime configuration or dispatch', async () => {
  let sent = 0;
  api.defaults.adapter = async (config) => { sent++; return ok(config); };
  const request = api.get('/auth/me').catch((error) => error);
  auth.saveTokens('other-account', 'other-refresh');
  assert.ok(axios.isCancel(await request));
  assert.equal(sent, 0);
});

test('cancelling one refresh waiter does not cancel recovery for another request', async () => {
  const gate = deferred(); const controller = new AbortController(); let retried = 0;
  api.defaults.adapter = async (config) => {
    if (config.url === '/auth/refresh') { await gate.promise; return ok(config, tokens); }
    if (config.headers.Authorization === 'Bearer old-access') return fail(config, 401);
    retried++; return ok(config);
  };
  const cancelled = api.get('/one', { signal: controller.signal }).catch((error) => error);
  const recovered = api.get('/two');
  await settle(); controller.abort(); gate.resolve();
  assert.ok(axios.isCancel(await cancelled)); await recovered;
  assert.equal(retried, 1); assert.equal(auth.getAccessToken(), 'new-access');
});

test('pre-cancelled requests neither dispatch nor refresh', async () => {
  const controller = new AbortController(); controller.abort(); let calls = 0;
  api.defaults.adapter = async (config) => { calls++; return ok(config); };
  await assert.rejects(api.get('/auth/me', { signal: controller.signal }), (error) => parseApiError(error).kind === 'cancelled');
  assert.equal(calls, 0); assert.equal(auth.getAccessToken(), 'old-access');
});

test('401 retry preserves POST body and operation timeout without replaying network failures', async () => {
  const payload = { session_id: 'session-1', image: 'synthetic-test-image', qr_code_secret: 'synthetic-test-qr' };
  const posts = [];
  api.defaults.adapter = async (config) => {
    if (config.url === '/auth/refresh') return ok(config, tokens);
    posts.push({ body: config.data, timeout: config.timeout });
    if (posts.length === 1) return fail(config, 401);
    return ok(config, { status: 'flagged' });
  };
  const response = await api.post('/checkins/', payload, { timeout: 45000 });
  assert.equal(response.data.status, 'flagged');
  assert.equal(posts.length, 2); assert.deepEqual(posts[0], posts[1]);
  assert.deepEqual(JSON.parse(posts[1].body), payload);
  let attempts = 0;
  api.defaults.adapter = async (config) => { attempts++; throw new axios.AxiosError('offline', 'ERR_NETWORK', config); };
  await assert.rejects(api.post('/checkins/', payload));
  assert.equal(attempts, 1);
});

test('a forbidden current profile fails closed without attempting refresh', async () => {
  const calls = [];
  api.defaults.adapter = async (config) => { calls.push(config.url); return fail(config, 403); };
  await assert.rejects(getCurrentUser());
  assert.equal(auth.getAccessToken(), null);
  assert.deepEqual(calls, ['/auth/me']);
});

test('a delayed registration response cannot navigate an already-replaced session', async () => {
  const gate = deferred();
  api.defaults.adapter = async (config) => { await gate.promise; return ok(config, { id: 'registered-user' }); };
  const request = registerUser({ email: 'new@example.com', password: 'private-password', role: 'student', full_name: 'Student' }).catch((error) => error);
  await settle(); auth.saveTokens('another-session', 'another-refresh'); gate.resolve();
  assert.ok(axios.isCancel(await request));
  assert.equal(auth.getAccessToken(), 'another-session');
});

const authKeys = ['saiv_access_token', 'saiv_refresh_token', 'saiv_user'];
const storageDenied = () => new DOMException('private browser storage diagnostic', 'SecurityError');
function assertSignedOut() {
  assert.equal(auth.getAccessToken(), null);
  assert.equal(auth.getRefreshToken(), null);
  assert.equal(auth.getStoredUser(), null);
  assert.equal(auth.isAuthenticated(), false);
}
function assertStorageFailure(error) {
  assert.match(parseApiError(error).message, /Browser session storage is unavailable.*Allow site storage/);
  assert.doesNotMatch(parseApiError(error).message, /private browser|old-access|old-refresh|new-access|new-refresh/);
  return true;
}
function freshAuthModule() {
  // Simulate a full reload with fresh module state and the same tab storage.
  // Do not replace the live module or its dependency cache.
  const Module = require('node:module');
  const filename = require.resolve('../lib/auth.ts');
  const reloaded = new Module(filename, module);
  reloaded.filename = filename;
  reloaded.paths = Module._nodeModulePaths(require('node:path').dirname(filename));
  require.extensions['.ts'](reloaded, filename);
  return reloaded.exports;
}

for (const operation of ['getAccessToken', 'getRefreshToken', 'getStoredUser']) {
  test(`${operation} fails closed on denied reads and does not revive residual credentials after storage recovers`, () => {
    auth.saveUser({ id: 'old-user' });
    const version = auth.getSessionVersion();
    const storage = global.sessionStorage;
    global.sessionStorage = { getItem() { throw storageDenied(); }, removeItem() { throw storageDenied(); } };
    try { assert.equal(auth[operation](), null); }
    finally { global.sessionStorage = storage; }
    assert.ok(auth.getSessionVersion() > version);
    assert.equal(storage.getItem('saiv_access_token'), 'old-access', 'the browser refused physical removal');
    assert.equal(auth.isCurrentSession(version), false);
    assertSignedOut();
  });
}

test('a denied sessionStorage property getter cannot interrupt logout or listener invalidation', async () => {
  const descriptor = Object.getOwnPropertyDescriptor(global, 'sessionStorage');
  const version = auth.getSessionVersion();
  let changes = 0;
  const unsubscribe = auth.subscribeAuthSession(() => { changes++; assertSignedOut(); });
  Object.defineProperty(global, 'sessionStorage', { configurable: true, get() { throw storageDenied(); } });
  try { await logoutUser(); }
  finally { Object.defineProperty(global, 'sessionStorage', descriptor); unsubscribe(); }
  assert.ok(auth.getSessionVersion() > version);
  assert.ok(changes > 0);
  assertSignedOut();
});

for (const failedKey of authKeys) {
  test(`logout invalidates before storage cleanup and continues after ${failedKey} removal fails`, async () => {
    auth.saveUser({ id: 'old-user' });
    const version = auth.getSessionVersion();
    const originalRemove = sessionStorage.removeItem;
    const removed = []; const paths = [];
    sessionStorage.removeItem = (key) => {
      assert.equal(auth.isCurrentSession(version), false);
      removed.push(key);
      if (key === failedKey) throw storageDenied();
      originalRemove(key);
    };
    api.defaults.adapter = async (config) => {
      paths.push(config.url);
      assert.equal(config.headers.Authorization, 'Bearer old-access');
      assertSignedOut();
      return ok(config);
    };
    await logoutUser();
    assert.deepEqual(removed, authKeys);
    assert.deepEqual(paths, ['/auth/logout']);
    assertSignedOut();
  });
}

for (const failedKey of authKeys) {
  test(`login rejects a denied ${failedKey} write and discards partial authentication`, async () => {
    const originalSet = sessionStorage.setItem;
    sessionStorage.setItem = (key, value) => {
      if (key === failedKey) throw storageDenied();
      originalSet(key, value);
    };
    api.defaults.adapter = async (config) => ok(config, { ...tokens, user: { id: 'new-user' } });
    await assert.rejects(loginUser({ email: 'student@example.com', password: 'synthetic-password' }), assertStorageFailure);
    assertSignedOut();
  });
}

for (const failedKey of authKeys) {
  test(`login rejects a silently discarded ${failedKey} write`, async () => {
    const originalSet = sessionStorage.setItem;
    sessionStorage.setItem = (key, value) => { if (key !== failedKey) originalSet(key, value); };
    api.defaults.adapter = async (config) => ok(config, { ...tokens, user: { id: 'new-user' } });
    await assert.rejects(loginUser({ email: 'student@example.com', password: 'synthetic-password' }), assertStorageFailure);
    assertSignedOut();
  });
}

test('a failed write/readback cannot recover authentication even when cleanup also fails', () => {
  const originalSet = sessionStorage.setItem;
  sessionStorage.setItem = (key, value) => {
    if (key === 'saiv_refresh_token' || value === '') throw storageDenied();
    originalSet(key, value);
  };
  sessionStorage.removeItem = () => { throw storageDenied(); };
  assert.throws(() => auth.saveTokens('partial-access', 'partial-refresh'), assertStorageFailure);
  assert.equal(sessionStorage.getItem('saiv_access_token'), 'partial-access');
  assertSignedOut();
});

test('denied token readback invalidates a partially written session', () => {
  sessionStorage.getItem = () => { throw storageDenied(); };
  assert.throws(() => auth.saveTokens('partial-access', 'partial-refresh'), assertStorageFailure);
  assertSignedOut();
});

for (const failedKey of ['saiv_access_token', 'saiv_refresh_token']) {
  test(`refresh ${failedKey} write failure invalidates all waiters without replaying protected requests`, async () => {
    const gate = deferred(); let refreshes = 0; let originals = 0;
    api.defaults.adapter = async (config) => {
      if (config.url === '/auth/refresh') { refreshes++; await gate.promise; return ok(config, tokens); }
      originals++; return fail(config, 401);
    };
    const results = Promise.allSettled([api.get('/one'), api.get('/two')]);
    await settle();
    const originalSet = sessionStorage.setItem;
    sessionStorage.setItem = (key, value) => {
      if (key === failedKey) throw storageDenied();
      originalSet(key, value);
    };
    gate.resolve();
    assert.ok((await results).every((result) => result.status === 'rejected'));
    assert.equal(refreshes, 1); assert.equal(originals, 2);
    assertSignedOut();
  });
}

test('logout with failed removals blocks pending refresh even after storage becomes usable again', async () => {
  const gate = deferred(); let originals = 0;
  api.defaults.adapter = async (config) => {
    if (config.url === '/auth/logout') return ok(config);
    if (config.url === '/auth/refresh') { await gate.promise; return ok(config, tokens); }
    originals++; return fail(config, 401);
  };
  const request = api.get('/auth/me').catch((error) => error);
  await settle();
  const originalRemove = sessionStorage.removeItem;
  sessionStorage.removeItem = () => { throw storageDenied(); };
  await logoutUser(); sessionStorage.removeItem = originalRemove;
  gate.resolve();
  assert.ok(axios.isCancel(await request));
  assert.equal(originals, 1); assertSignedOut();
});

test('logout with failed removals blocks a delayed login response', async () => {
  const gate = deferred();
  api.defaults.adapter = async (config) => { await gate.promise; return ok(config, { ...tokens, user: { id: 'old-user' } }); };
  const request = loginUser({ email: 'old@example.com', password: 'synthetic-password' }).catch((error) => error);
  await settle();
  sessionStorage.removeItem = () => { throw storageDenied(); };
  auth.clearAuthSession(); gate.resolve();
  assert.ok(axios.isCancel(await request)); assertSignedOut();
});

test('late profile and consent responses cannot restore a session after logout cleanup fails', async () => {
  for (const operation of [() => getCurrentUser(), () => updateConsent({ camera_consent: true })]) {
    auth.saveTokens('account-a', 'refresh-a'); auth.saveUser({ id: 'account-a' });
    const gate = deferred();
    api.defaults.adapter = async (config) => { await gate.promise; return ok(config, { id: 'account-a' }); };
    const request = operation().catch((error) => error);
    await settle();
    sessionStorage.removeItem = () => { throw storageDenied(); };
    auth.clearAuthSession(); gate.resolve();
    assert.ok(axios.isCancel(await request)); assertSignedOut();
  }
});

test('within the current page a fresh successful sign-in is required to re-enable storage-backed authentication', async () => {
  const originalRemove = sessionStorage.removeItem;
  sessionStorage.removeItem = () => { throw storageDenied(); };
  auth.clearAuthSession(); assertSignedOut(); sessionStorage.removeItem = originalRemove;
  api.defaults.adapter = async (config) => ok(config, { ...tokens, user: { id: 'replacement' } });
  await loginUser({ email: 'replacement@example.com', password: 'synthetic-password' });
  assert.equal(auth.getAccessToken(), tokens.access_token);
  assert.equal(auth.getRefreshToken(), tokens.refresh_token);
  assert.equal(auth.getStoredUser().id, 'replacement');
});

test('characterization: full reload can read residual credentials when removal and overwrite are both blocked', async () => {
  auth.saveUser({ id: 'old-user' });
  const originalRemove = sessionStorage.removeItem;
  const originalSet = sessionStorage.setItem;
  sessionStorage.removeItem = () => { throw storageDenied(); };
  sessionStorage.setItem = () => { throw storageDenied(); };
  let logoutCalls = 0;
  api.defaults.adapter = async (config) => {
    assert.equal(config.url, '/auth/logout');
    logoutCalls++;
    return ok(config);
  };
  await logoutUser();
  assert.equal(logoutCalls, 1, 'even a successful logout response does not prove token revocation');
  assertSignedOut();
  assert.match(auth.getAuthCleanupWarning(), /Clear this site's browser data.*Reloading may restore/);
  sessionStorage.removeItem = originalRemove;
  sessionStorage.setItem = originalSet;
  const freshAuth = freshAuthModule();

  // This deliberately documents the unresolved reload gap. It is not a secure
  // logout acceptance test, nor proof that a real backend accepted these mocks.
  assert.equal(freshAuth.getSessionVersion(), 0);
  assert.equal(freshAuth.getAccessToken(), 'old-access');
  assert.equal(freshAuth.getRefreshToken(), 'old-refresh');
  assert.equal(freshAuth.getStoredUser().id, 'old-user');
  assert.equal(freshAuth.isAuthenticated(), true);
  assertSignedOut(); // The original page remains protected by its memory block.
});

test('successful removal verifies every auth entry without unnecessary overwrites or a warning', async () => {
  auth.saveUser({ id: 'old-user' });
  const removed = []; const read = []; const written = [];
  const storage = { ...sessionStorage };
  sessionStorage.removeItem = (key) => { removed.push(key); storage.removeItem(key); };
  sessionStorage.getItem = (key) => { read.push(key); return storage.getItem(key); };
  sessionStorage.setItem = (key, value) => { written.push([key, value]); storage.setItem(key, value); };
  api.defaults.adapter = async (config) => ok(config);
  await logoutUser();
  assert.deepEqual(removed, authKeys);
  for (const key of authKeys) assert.ok(read.includes(key));
  assert.deepEqual(written, []);
  assert.equal(auth.getAuthCleanupWarning(), null);
  assert.equal(freshAuthModule().isAuthenticated(), false);
});

for (const removal of ['throws', 'silently leaves values']) {
  test(`removal that ${removal} falls back to verified empty values that stay signed out after reload`, async () => {
    auth.saveUser({ id: 'old-user' });
    const overwritten = []; const originalSet = sessionStorage.setItem;
    sessionStorage.removeItem = () => { if (removal === 'throws') throw storageDenied(); };
    sessionStorage.setItem = (key, value) => { overwritten.push([key, value]); originalSet(key, value); };
    api.defaults.adapter = async (config) => ok(config);
    await logoutUser();
    assert.deepEqual(overwritten, authKeys.map((key) => [key, '']));
    assert.equal(auth.getAuthCleanupWarning(), null);
    const fresh = freshAuthModule();
    assert.equal(fresh.getAccessToken(), null); assert.equal(fresh.getRefreshToken(), null);
    assert.equal(fresh.getStoredUser(), null); assert.equal(fresh.isAuthenticated(), false);
  });
}

test('silent overwrite failure leaves an explicit warning and no ready authentication in this page', () => {
  sessionStorage.removeItem = () => {};
  sessionStorage.setItem = () => {};
  auth.clearAuthSession();
  assert.match(auth.getAuthCleanupWarning(), /Clear this site's browser data/);
  assertSignedOut();
  assert.equal(freshAuthModule().getAccessToken(), 'old-access');
});

test('unreadable cleanup status warns even if the storage entries were actually removed', () => {
  sessionStorage.getItem = () => { throw storageDenied(); };
  auth.clearAuthSession();
  assert.match(auth.getAuthCleanupWarning(), /couldn't confirm/);
  assertSignedOut();
});

for (const failedKey of ['saiv_access_token', 'saiv_refresh_token']) {
  test(`partial cleanup with leftover ${failedKey} warns and still attempts the other entries`, () => {
    auth.saveUser({ id: 'old-user' });
    const storage = { ...sessionStorage }; const attempted = [];
    sessionStorage.removeItem = (key) => {
      attempted.push(key); if (key === failedKey) throw storageDenied(); storage.removeItem(key);
    };
    sessionStorage.setItem = (key, value) => { if (key === failedKey) throw storageDenied(); storage.setItem(key, value); };
    auth.clearAuthSession();
    assert.deepEqual(attempted, authKeys);
    assert.match(auth.getAuthCleanupWarning(), /before sharing this device/);
    assert.equal(storage.getItem(failedKey), failedKey === 'saiv_access_token' ? 'old-access' : 'old-refresh');
    assert.equal(storage.getItem(failedKey === 'saiv_access_token' ? 'saiv_refresh_token' : 'saiv_access_token'), null);
    assertSignedOut();
  });
}

for (const value of ['', ' ', '\t', 'null', 'undefined', 'token\nvalue', 42, {}]) {
  test(`invalid stored credential ${JSON.stringify(value)} is never treated as authenticated`, () => {
    sessionStorage.getItem = () => value;
    const fresh = freshAuthModule();
    assert.equal(fresh.getAccessToken(), null); assert.equal(fresh.getRefreshToken(), null);
    assert.equal(fresh.isAuthenticated(), false);
  });
}

test('invalid credentials from a successful HTTP response cannot establish authentication', async () => {
  api.defaults.adapter = async (config) => ok(config, { ...tokens, access_token: 'null', user: { id: 'new-user' } });
  await assert.rejects(loginUser({ email: 'student@example.com', password: 'synthetic-password' }), /Invalid authentication response/);
  assertSignedOut();
});

test('cleanup warning is cleared only after a new cleanup is verified or a new sign-in succeeds', () => {
  const originalRemove = sessionStorage.removeItem; const originalSet = sessionStorage.setItem;
  sessionStorage.removeItem = () => { throw storageDenied(); };
  sessionStorage.setItem = () => { throw storageDenied(); };
  auth.clearAuthSession(); assert.ok(auth.getAuthCleanupWarning());
  auth.getAccessToken(); assert.ok(auth.getAuthCleanupWarning());
  sessionStorage.removeItem = originalRemove;
  sessionStorage.setItem = originalSet;
  auth.clearAuthSession(); assert.equal(auth.getAuthCleanupWarning(), null);
});

for (const operation of ['login', 'refresh', 'profile', 'consent']) {
  test(`blocked cleanup preserves its warning and rejects delayed ${operation} responses`, async () => {
    const gate = deferred();
    const originalSet = sessionStorage.setItem;
    if (operation === 'login') {
      // Login itself clears the previous session. Block that cleanup as well so
      // this fixture really has residual credentials when logout is requested.
      sessionStorage.removeItem = () => { throw storageDenied(); };
      sessionStorage.setItem = () => { throw storageDenied(); };
    }
    const operations = {
      login: () => loginUser({ email: 'student@example.com', password: 'synthetic-password' }),
      refresh: () => api.get('/protected'), profile: () => getCurrentUser(),
      consent: () => updateConsent({ camera_consent: true }),
    };
    api.defaults.adapter = async (config) => {
      if (config.url === '/protected') return fail(config, 401);
      await gate.promise;
      return ok(config, config.url === '/auth/refresh' ? tokens : { ...tokens, user: { id: 'old-user' }, id: 'old-user' });
    };
    const pending = operations[operation]().catch((error) => error);
    await settle();
    sessionStorage.removeItem = () => { throw storageDenied(); };
    sessionStorage.setItem = () => { throw storageDenied(); };
    auth.clearAuthSession();
    sessionStorage.setItem = originalSet; // Permission recovery must not enable the stale response.
    gate.resolve();
    assert.ok(axios.isCancel(await pending));
    assertSignedOut(); assert.ok(auth.getAuthCleanupWarning());
  });
}

test('blocked storage writes do not cause a false warning when reads confirm no credentials remain', () => {
  auth.clearAuthSession();
  sessionStorage.removeItem = () => { throw storageDenied(); };
  sessionStorage.setItem = () => { throw storageDenied(); };
  auth.clearAuthSession();
  assert.equal(auth.getAuthCleanupWarning(), null);
  assertSignedOut();
});
