// Frontend-only smoke test of an already-built app. Never contacts the configured backend.
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { createServer } = require('node:net');
const path = require('node:path');

async function main() {
  const probe = createServer();
  await new Promise((resolve, reject) => {
    probe.once('error', reject);
    probe.listen(0, '127.0.0.1', resolve);
  });
  const port = probe.address().port;
  await new Promise((resolve) => probe.close(resolve));
  const origin = `http://127.0.0.1:${port}`;
  const child = spawn(process.execPath, [require.resolve('next/dist/bin/next'),
    'start', '--hostname', '127.0.0.1', '--port', String(port)], {
    cwd: path.resolve(__dirname, '..'), windowsHide: true,
    env: {
      ...process.env, NODE_ENV: 'production', NEXT_TELEMETRY_DISABLED: '1',
      // Deliberately different from build defaults: prove these are read at runtime.
      NEXT_PUBLIC_API_URL: 'https://runtime-api.invalid/gateway/api/v1/api/v1',
      NEXT_PUBLIC_STAFF_PORTAL_URL: 'https://runtime-staff.invalid/portal',
      SAIV_BACKEND_INTERNAL_URL: '',
      SAIV_STUDENT_PORTAL_ENABLED: 'false',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let output = '';
  let spawnError;
  child.stdout.on('data', (chunk) => { output += chunk; });
  child.stderr.on('data', (chunk) => { output += chunk; });
  child.on('error', (error) => { spawnError = error; });

  try {
    let configResponse;
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
      if (spawnError) throw spawnError;
      if (child.exitCode !== null) throw new Error(`Frontend exited: ${output}`);
      try {
        configResponse = await fetch(`${origin}/api/config`, { signal: AbortSignal.timeout(1000) });
        break;
      } catch {
        await new Promise((resolve) => setTimeout(resolve, 200));
      }
    }
    assert.ok(configResponse, `production frontend starts; server output: ${output}`);
    assert.equal(configResponse.status, 200);
    assert.equal(configResponse.headers.get('cache-control'), 'no-store');
    assert.deepEqual(await configResponse.json(), {
      apiUrl: 'https://runtime-api.invalid/gateway/api/v1',
      staffPortalUrl: 'https://runtime-staff.invalid/portal',
    });
    console.log('PASS: built app reads public URLs at runtime with one API prefix and no-store');

    const root = await fetch(origin, { redirect: 'manual' });
    assert.equal(root.status, 307);
    assert.equal(root.headers.get('location'), '/dashboard');
    console.log('PASS: root redirects to the validated dashboard entry');

    for (const route of ['/login', '/register', '/dashboard', '/profile', '/forgot-password']) {
      const response = await fetch(`${origin}${route}`);
      assert.equal(response.status, 200);
      const html = await response.text();
      if (route === '/dashboard' || route === '/profile') {
        assert.match(html, /Checking your session/);
        assert.doesNotMatch(html, /Account Setup|Enroll Face/);
      } else {
        assert.match(html, /<form/);
        if (route === '/login') assert.match(html, /href="\/forgot-password"/);
        if (route === '/forgot-password') {
          assert.match(html, /Reset your password/);
          assert.match(html, /Send Verification Code/);
          assert.match(html, /autoComplete="email"/i);
          assert.doesNotMatch(html, /id="reset-code"|id="new-password"/);
        }
        if (route === '/register') {
          for (const role of ['student', 'ta', 'instructor', 'admin']) {
            assert.ok(html.includes(`value="${role}"`));
          }
        }
      }
      console.log(`PASS: ${route} renders its expected safe initial state`);
    }
    const manifest = await fetch(`${origin}/manifest.json`);
    assert.equal(manifest.status, 200);
    assert.equal((await manifest.json()).start_url, '/login');
    console.log('PASS: existing PWA start URL is preserved');
  } finally {
    if (child.pid && child.exitCode === null) {
      await new Promise((resolve) => {
        child.once('exit', resolve);
        child.kill();
      });
    }
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
