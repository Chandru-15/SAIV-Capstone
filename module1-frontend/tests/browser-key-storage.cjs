// Exercise the production browser-key module in a fresh Chrome profile.
// The loopback fixture uses no SAIV deployment, database, or account data.
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const { mkdtemp, readFile, realpath, rm } = require('node:fs/promises');
const { createServer } = require('node:http');
const { tmpdir } = require('node:os');
const path = require('node:path');
const { setTimeout: delay } = require('node:timers/promises');
const ts = require('typescript');

async function devtoolsPort(profile, child) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (child.exitCode !== null) break;
    try { return (await readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]; }
    catch { await delay(100); }
  }
  throw new Error('Chrome did not open its debugging port');
}

function client(socket) {
  let id = 0;
  const calls = new Map();
  socket.addEventListener('message', event => {
    const reply = JSON.parse(event.data);
    calls.get(reply.id)?.(reply);
  });
  return (method, params = {}) => new Promise((resolve, reject) => {
    const callId = ++id;
    const timer = setTimeout(() => {
      calls.delete(callId);
      reject(new Error(`Chrome command timed out: ${method}`));
    }, 10000);
    calls.set(callId, reply => {
      calls.delete(callId); clearTimeout(timer);
      if (reply.error) reject(new Error(reply.error.message)); else resolve(reply.result);
    });
    socket.send(JSON.stringify({ id: callId, method, params }));
  });
}

async function main() {
  const source = await readFile(path.resolve(__dirname, '../lib/browser-key.ts'), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  } });
  const app = `<script>
    const exports = {};
    const require = () => ({ ClientDataError: class extends Error {} });
    ${outputText}
    globalThis.keys = exports;
  </script>`;
  const server = createServer((_request, response) => {
    response.setHeader('Content-Type', 'text/html; charset=utf-8');
    response.end(app);
  });
  const tempRoot = await realpath(tmpdir());
  const profile = await mkdtemp(path.join(tempRoot, 'saiv-browser-key-'));
  let child, socket;
  try {
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    const chrome = process.env.CHROME_PATH || (process.platform === 'win32'
      ? 'C:/Program Files/Google/Chrome/Application/chrome.exe' : 'chromium');
    child = spawn(chrome, ['--headless=new', '--remote-debugging-address=127.0.0.1',
      '--remote-debugging-port=0', `--user-data-dir=${profile}`,
      '--no-first-run', '--no-default-browser-check', 'about:blank'],
    { windowsHide: true, stdio: 'ignore' });
    let launchError;
    child.once('error', error => { launchError = error; });
    const port = await devtoolsPort(profile, child).catch(error => { throw launchError || error; });
    const tabs = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    socket = new WebSocket(tabs.find(tab => tab.type === 'page').webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
    const call = client(socket);
    const evaluate = async expression => {
      const result = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
      return result.result.value;
    };
    const origin = `http://127.0.0.1:${server.address().port}`;
    async function navigate(page) {
      await call('Page.navigate', { url: `${origin}/?page=${page}` });
      for (let attempt = 0; attempt < 50; attempt++) {
        if (await evaluate(`location.search === '?page=${page}' &&
          typeof keys !== 'undefined' && document.readyState === 'complete'`)) return;
        await delay(100);
      }
      throw new Error('Browser key fixture did not load');
    }
    await navigate(1);
    const enrolled = await evaluate(`(async () => {
      const student = 'test-student', browser = 'test-browser';
      const initial = await keys.readBrowserKey(student, browser);
      const [one, two] = await Promise.all([1, 2].map(() =>
        keys.prepareBrowserKey(student, browser, new AbortController().signal)));
      let privateExportBlocked = false;
      try { await crypto.subtle.exportKey('pkcs8', one.privateKey); } catch { privateExportBlocked = true; }
      return { initial, keyId: one.keyId, sameKey: one.keyId === two.keyId, privateExportBlocked };
    })()`);
    assert.equal(enrolled.initial, null);
    assert.ok(enrolled.sameKey);
    assert.ok(enrolled.privateExportBlocked);

    await navigate(2);
    const reloaded = await evaluate(`(async () => {
      const key = await keys.readBrowserKey('test-student', 'test-browser');
      const other = await keys.readBrowserKey('another-student', 'test-browser');
      const wrongBrowser = await keys.readBrowserKey('test-student', 'another-browser');
      const challenge = { challenge_id: 'test', message: 'test-message',
        expires_at: new Date(Date.now() + 60000).toISOString() };
      const signature = await keys.signBrowserChallenge(key, challenge, '{"exact":true}');
      const [replacement, concurrent] = await Promise.all([1, 2].map(() =>
        keys.prepareBrowserKey('test-student', 'test-browser', new AbortController().signal, key.keyId)));
      const rotated = await keys.readBrowserKey('test-student', 'test-browser');
      await new Promise((resolve, reject) => {
        const request = indexedDB.deleteDatabase('saiv-browser-keys');
        request.onsuccess = resolve;
        request.onerror = reject;
        request.onblocked = reject;
      });
      const erased = await keys.readBrowserKey('test-student', 'test-browser');
      return { keyId: key.keyId, other, wrongBrowser, signature, erased,
        rotated: rotated.keyId, sameReplacement: replacement.keyId === concurrent.keyId };
    })()`);
    assert.equal(reloaded.keyId, enrolled.keyId);
    assert.equal(reloaded.other, null);
    assert.equal(reloaded.wrongBrowser, null);
    assert.equal(Buffer.from(reloaded.signature, 'base64url').length, 64);
    assert.notEqual(reloaded.rotated, enrolled.keyId);
    assert.ok(reloaded.sameReplacement);
    assert.equal(reloaded.erased, null);
    console.log('Chrome passed: non-extractable key, concurrent setup and rotation, reload, account/browser isolation, signing, and storage loss.');
  } finally {
    socket?.close();
    if (child?.pid && child.exitCode === null) {
      const exited = once(child, 'exit');
      child.kill();
      await exited;
    }
    server.close();
    if (path.dirname(profile) === tempRoot && path.basename(profile).startsWith('saiv-browser-key-')) {
      await rm(profile, { recursive: true, force: true, maxRetries: 8, retryDelay: 150 });
    }
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
