const { test, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const axios = require('axios');
const {
  normalizeApiBaseUrl, normalizeApiPath, resolvePublicConfiguration,
} = require('../lib/public-config.ts');
const { GET, dynamic } = require('../app/api/config/route.ts');

const originalApi = process.env.NEXT_PUBLIC_API_URL;
const originalPortal = process.env.NEXT_PUBLIC_STAFF_PORTAL_URL;
afterEach(() => {
  for (const [key, value] of [
    ['NEXT_PUBLIC_API_URL', originalApi], ['NEXT_PUBLIC_STAFF_PORTAL_URL', originalPortal],
  ]) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
});

test('API base accepts origins, trailing slashes, gateway paths and repeated prefixes', () => {
  for (const input of [undefined, '', 'http://localhost:8000', 'http://localhost:8000/',
    'http://localhost:8000/api/v1', 'http://localhost:8000/api/v1/',
    'http://localhost:8000/api/v1/api/v1///']) {
    assert.equal(normalizeApiBaseUrl(input), 'http://localhost:8000/api/v1');
  }
  assert.equal(normalizeApiBaseUrl(' https://saiv.example/backend/ '),
    'https://saiv.example/backend/api/v1');
});

test('a root-relative base keeps API calls on the page origin', () => {
  for (const input of ['/api/v1', '/api/v1/', '/api/v1/api/v1///', '/']) {
    assert.equal(normalizeApiBaseUrl(input), '/api/v1');
  }
  assert.equal(normalizeApiBaseUrl('/backend'), '/backend/api/v1');
  assert.equal(normalizeApiBaseUrl(' /backend/ '), '/backend/api/v1');
  // Normalising twice must not add a second prefix: client-config.ts re-runs
  // resolvePublicConfiguration on whatever /api/config returned.
  assert.equal(normalizeApiBaseUrl(normalizeApiBaseUrl('/api/v1')), '/api/v1');
});

test('a relative base cannot be used to send the API off-origin', () => {
  // '//host' is protocol-relative, not root-relative: it must not be treated
  // as a same-origin path, or the whole API could be pointed at another site.
  for (const input of ['//other.example/api/v1', '///other.example',
    '/api/v1?token=private', '/api/v1#private', '/api/v1/auth']) {
    assert.throws(() => normalizeApiBaseUrl(input));
  }
});

test('resource paths cannot double the prefix or bypass the backend', () => {
  for (const input of ['/auth/login', '/api/v1/auth/login', '/api/v1/api/v1/auth/login']) {
    assert.equal(normalizeApiPath(input), '/auth/login');
  }
  assert.equal(normalizeApiPath('/api/v1'), '/');
  for (const input of ['https://other.example/auth/me', '//other.example/auth/me', 'auth/me']) {
    assert.throws(() => normalizeApiPath(input));
  }
});

test('configuration rejects credentials, token-like queries, fragments and unsafe protocols', () => {
  for (const input of ['javascript:alert(1)', 'ftp://example.com', 'https://u:p@example.com',
    'https://example.com?token=private', 'https://example.com/#private',
    'https://example.com/api/v1/auth']) {
    assert.throws(() => normalizeApiBaseUrl(input));
  }
  for (const input of ['javascript:alert(1)', 'https://example.com/?token=private']) {
    assert.throws(() => resolvePublicConfiguration({ NEXT_PUBLIC_STAFF_PORTAL_URL: input }));
  }
});

test('runtime endpoint rereads the environment and never caches or exposes unrelated secrets', async () => {
  assert.equal(dynamic, 'force-dynamic');
  process.env.NEXT_PUBLIC_API_URL = 'https://first.example';
  process.env.NEXT_PUBLIC_STAFF_PORTAL_URL = 'https://portal.example/staff';
  let response = await GET();
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.deepEqual(await response.json(), {
    apiUrl: 'https://first.example/api/v1', staffPortalUrl: 'https://portal.example/staff',
  });
  process.env.NEXT_PUBLIC_API_URL = 'https://second.example/api/v1/api/v1';
  response = await GET();
  assert.equal((await response.json()).apiUrl, 'https://second.example/api/v1');
  process.env.NEXT_PUBLIC_API_URL = 'https://user:private@example.com';
  response = await GET();
  assert.equal(response.status, 503);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.deepEqual(await response.json(), { message: 'Invalid application configuration.' });
});

test('Axios uses runtime URL, a single prefix and current bearer token', async () => {
  let fetches = 0;
  global.fetch = async (url, options) => {
    fetches++;
    assert.equal(url, '/api/config');
    assert.equal(options.cache, 'no-store');
    // The same-origin config endpoint may sit behind an authenticated private tunnel.
    assert.equal(options.credentials, 'same-origin');
    return Response.json({ apiUrl: 'https://backend.example/api/v1', staffPortalUrl: 'https://staff.example' });
  };
  global.window = {};
  global.sessionStorage = { getItem: () => 'test-access' };
  const { api } = require('../lib/api.ts');
  const urls = [];
  api.defaults.adapter = async (config) => {
    urls.push(axios.getUri(config));
    assert.equal(config.headers.Authorization, 'Bearer test-access');
    return { data: {}, status: 200, statusText: 'OK', headers: {}, config };
  };
  await Promise.all([api.get('/auth/me'), api.get('/api/v1/sessions/')]);
  assert.equal(fetches, 1);
  assert.deepEqual(urls.sort(), [
    'https://backend.example/api/v1/auth/me', 'https://backend.example/api/v1/sessions/',
  ]);
});

test('failed runtime configuration loads are retriable and never send a backend request', async () => {
  delete require.cache[require.resolve('../lib/client-config.ts')];
  delete require.cache[require.resolve('../lib/api.ts')];
  let fetches = 0;
  let requests = 0;
  global.fetch = async () => {
    fetches++;
    return fetches === 1 ? new Response('', { status: 503 }) : Response.json({
      apiUrl: 'http://localhost:8000', staffPortalUrl: 'http://localhost:8501',
    });
  };
  const { api } = require('../lib/api.ts');
  api.defaults.adapter = async (config) => {
    requests++;
    return { data: {}, status: 200, statusText: 'OK', headers: {}, config };
  };
  await assert.rejects(api.get('/auth/me'), /configuration/);
  assert.equal(requests, 0);
  await api.get('/auth/me');
  assert.equal(requests, 1);
  assert.equal(fetches, 2);
});

test('runtime configuration deadline is a timeout rather than silent user cancellation', async () => {
  delete require.cache[require.resolve('../lib/client-config.ts')];
  delete require.cache[require.resolve('../lib/api.ts')];
  global.fetch = async () => { throw new DOMException('deadline', 'AbortError'); };
  const { api } = require('../lib/api.ts');
  const { parseApiError } = require('../lib/api-error.ts');
  await assert.rejects(api.get('/auth/me'), (error) => parseApiError(error).kind === 'timeout');
});

test('runtime configuration preserves the local-only portal restriction in the browser',async()=>{
  delete require.cache[require.resolve('../lib/client-config.ts')];
  global.fetch=async()=>Response.json({apiUrl:'http://localhost:8000',staffPortalUrl:'http://localhost:8501',
    studentPortalUrl:'http://localhost:8501/portal/',studentPortalLocalOnly:true});
  const config=await require('../lib/client-config.ts').getPublicConfiguration();
  assert.equal(config.studentPortalLocalOnly,true);
  assert.equal(config.studentPortalUrl,'http://localhost:8501/portal/');
});
