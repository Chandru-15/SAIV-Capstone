// Real Chrome + real Phase 3A API. No camera/GPS evidence, enrollment, device
// registration or attendance submission is fabricated. Reuses the disposable
// account created during Phase 3A; never reads a real student's credentials.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { spawn } = require('node:child_process');

const root = path.resolve(__dirname, '../..');
const origin = process.env.SAIV_BROWSER_ORIGIN || 'http://localhost:8080';
const output = path.join(root, process.env.SAIV_BROWSER_PORTAL_ONLY === 'true' ? '.local/phase3b/local-portal/browser' : '.local/phase3b/preview-ratio/browser', new URL(origin).port || 'https');
const chrome = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
let socket, child, nextId = 0;
const pending = new Map();
const diagnostics = { runtimeErrors: [], consoleErrors: [], failedRequests: [], httpErrors: [], mutations: [], layouts: [], typography: [] };

function call(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++nextId;
    const timer = setTimeout(() => { pending.delete(id); reject(Error(`Chrome command timed out: ${method}`)); }, 30000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw Error(`Browser evaluation failed: ${result.exceptionDetails.text}`);
  return result.result.value;
}
async function waitFor(expression) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    if (await evaluate(expression)) return;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw Error(`Browser state did not become ready: ${expression}`);
}
async function navigate(route) {
  await call('Page.navigate', { url: `${origin}${route}` });
  await waitFor('document.readyState === "complete"');
}
async function clickText(label) {
  const point = await evaluate(`(() => { const el = [...document.querySelectorAll('button,a')].find(el => el.textContent.trim() === ${JSON.stringify(label)}); if (!el || el.disabled) return null; el.scrollIntoView({block:'center'}); const r=el.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2}; })()`);
  assert.ok(point, `enabled action exists: ${label}`);
  await call('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', clickCount: 1, ...point });
  await call('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', clickCount: 1, ...point });
}
async function fill(selector, value) {
  await evaluate(`document.querySelector(${JSON.stringify(selector)}).focus()`);
  await call('Input.insertText', { text: value });
}
async function layout(name, width, height = 850, textSize = '') {
  await call('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: true });
  await evaluate(`document.documentElement.style.fontSize = ${JSON.stringify(textSize)}`);
  await new Promise(resolve => setTimeout(resolve, 120));
  const dimensions = await evaluate(`({viewport:innerWidth, page:document.documentElement.scrollWidth,
    overflowing:[...document.querySelectorAll('main *,header *')].filter(el=>{const r=el.getBoundingClientRect();return r.width>0&&(r.right>innerWidth+1||r.left < -1)}).slice(0,8).map(el=>el.tagName+'.'+el.className)})`);
  diagnostics.layouts.push({ name, width, height, textSize, ...dimensions });
  assert.ok(dimensions.page <= width + 1, `${name}: ${dimensions.page}px page exceeds ${width}px viewport`);
}
async function screenshot(name, fullPage = false) {
  const options = { format: 'png', captureBeyondViewport: fullPage };
  if (fullPage) {
    const { cssContentSize } = await call('Page.getLayoutMetrics');
    options.clip = { x: 0, y: 0, width: await evaluate('innerWidth'), height: Math.ceil(cssContentSize.height), scale: 1 };
  }
  const { data } = await call('Page.captureScreenshot', options);
  await fs.writeFile(path.join(output, `${name}.png`), Buffer.from(data, 'base64'));
}

async function measureReadability(name) {
  const measurements = await evaluate(`(() => {
    const font = selector => { const el=document.querySelector(selector); return el ? parseFloat(getComputedStyle(el).fontSize) : null; };
    const main=document.querySelector('main.page-width');
    const style=main && getComputedStyle(main);
    return { viewport:innerWidth, pageHeight:document.documentElement.scrollHeight,
      contentWidth:main ? main.clientWidth-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight) : null,
      pageHeading:font('main h1'), sectionHeading:font('.section-title'), body:font('.empty-state'),
      secondary:font('.section-heading p'), badge:font('.tag'), button:font('.refresh-button'),
      profileValue:font('.personal-details dd'), profileLabel:font('.personal-details dt'),
      browserInformation:font('.browser-identity'), consentDescription:font('.consent-description'),
      resultLabel:font('.result-metrics dt'), resultMetric:font('.result-metrics dd'), resultDetails:font('.result-details'),
      undersizedTargets:[...document.querySelectorAll('.student-app button:not(:disabled),.student-nav a,.student-app .brand,.browser-row > summary,.result-details > summary')]
        .filter(el=>{const r=el.getBoundingClientRect();return r.width>0&&r.height>0&&(r.height<43.5||r.width<43.5)})
        .map(el=>el.getAttribute('aria-label')||el.textContent.trim().slice(0,50)) };
  })()`);
  diagnostics.typography.push({ name, ...measurements });
  assert.deepEqual(measurements.undersizedTargets, [], `${name}: visible touch targets are at least 44px`);
  if (measurements.sectionHeading != null) assert.ok(measurements.sectionHeading >= 20);
  if (measurements.secondary != null) assert.ok(measurements.secondary >= 14);
  if (measurements.badge != null) assert.ok(measurements.badge >= 13);
  return measurements;
}

async function main() {
  await fs.mkdir(output, { recursive: true });
  const credentials = JSON.parse(await fs.readFile(path.join(root, '.local/phase3a/browser-account.json'), 'utf8'));
  assert.equal(credentials.role, 'student', 'use only the existing disposable student fixture');
  const profile = path.join(output, `chrome-${Date.now()}`);
  child = spawn(chrome, ['--headless=new', '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0',
    `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check', 'about:blank'], { windowsHide: true, stdio: 'ignore' });
  let port;
  for (let attempt = 0; attempt < 100; attempt++) {
    try { port = (await fs.readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]; break; }
    catch { await new Promise(resolve => setTimeout(resolve, 100)); }
  }
  assert.ok(port, 'Chrome debug endpoint starts');
  const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  socket = new WebSocket(pages.find(page => page.type === 'page').webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.id) {
      const request = pending.get(message.id); if (!request) return;
      clearTimeout(request.timer); pending.delete(message.id);
      if (message.error) request.reject(Error(`Chrome ${message.error.message}`)); else request.resolve(message.result);
    } else if (message.method === 'Network.requestWillBeSent' && ['POST','PATCH','PUT','DELETE'].includes(message.params.request.method)) {
      diagnostics.mutations.push({ method: message.params.request.method, path: new URL(message.params.request.url).pathname });
    } else if (message.method === 'Runtime.exceptionThrown') diagnostics.runtimeErrors.push(message.params.exceptionDetails.text);
    else if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') diagnostics.consoleErrors.push('Browser console.error');
    else if (message.method === 'Network.loadingFailed' && !message.params.canceled) diagnostics.failedRequests.push({ type: message.params.type, error: message.params.errorText });
    else if (message.method === 'Network.responseReceived' && message.params.response.status >= 400) {
      const response = message.params.response;
      diagnostics.httpErrors.push({ origin: new URL(response.url).origin, path: new URL(response.url).pathname, status: response.status });
    }
  };
  await Promise.all([call('Page.enable'), call('Runtime.enable'), call('Network.enable')]);
  await call('Page.addScriptToEvaluateOnNewDocument', { source: `
    window.__permissionRequests = { camera:0, location:0 };
    if(navigator.mediaDevices) { const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices); navigator.mediaDevices.getUserMedia=(...args)=>{window.__permissionRequests.camera++;return original(...args)}; }
    if(navigator.geolocation) { const original=navigator.geolocation.getCurrentPosition.bind(navigator.geolocation); navigator.geolocation.getCurrentPosition=(...args)=>{window.__permissionRequests.location++;return original(...args)}; }
  ` });
  for (const route of ['/dashboard', '/profile']) {
    await navigate(route); await waitFor('location.pathname === "/login" && !!document.querySelector("#email")');
    console.log(`PASS: unauthenticated ${route} redirects to login`);
  }
  await navigate('/login'); await waitFor('!!document.querySelector("#email")');
  const authStyles = await evaluate(`({background:getComputedStyle(document.body).backgroundColor,submit:getComputedStyle(document.querySelector('button[type=submit]')).backgroundColor})`);
  assert.deepEqual(authStyles, { background:'rgb(243, 246, 248)', submit:'rgb(6, 94, 98)' });
  console.log('PASS: authentication surface retains its existing styling');
  await navigate('/login'); await waitFor('!!document.querySelector("#email")');
  await fill('#email', credentials.email); await fill('#password', credentials.password); await clickText('Sign in');
  await waitFor('location.pathname === "/dashboard" && !!document.querySelector("#sessions-title")');
  assert.ok(await evaluate(`document.querySelector('h1').textContent.includes(${JSON.stringify(credentials.full_name)})`));
  await waitFor('!document.body.textContent.includes("Loading active sessions") && !document.body.textContent.includes("Loading attendance history") && !document.body.textContent.includes("Loading enrolled courses")');
  const dashboard = await evaluate(`({courses:document.querySelector('#courses-title')?.parentElement.parentElement.parentElement.textContent,
    browserId:localStorage.getItem('saiv_device_fingerprint_v1'), permissions:window.__permissionRequests,
    portalDisabled:[...document.querySelectorAll('button')].find(el=>el.textContent.trim()==='Attendance & Appeals')?.disabled})`);
  const portalConfig=await evaluate(`fetch('/api/config').then(response=>response.json())`);
  const localHost=['localhost','127.0.0.1'].includes(new URL(origin).hostname);
  const portalEnabled=!!portalConfig.studentPortalUrl&&(!portalConfig.studentPortalLocalOnly||localHost);
  assert.equal(dashboard.browserId, null);
  if(portalEnabled) await require('./portal-browser-check.cjs')({call,evaluate,port,url:portalConfig.studentPortalUrl});
  else assert.equal(dashboard.portalDisabled,true);
  assert.deepEqual(dashboard.permissions, { camera: 0, location: 0 });
  console.log('PASS: real student login, authenticated dashboard and configured portal availability; no browser ID or permission request');
  if(process.env.SAIV_BROWSER_PORTAL_ONLY==='true') {
    assert.equal(portalConfig.studentPortalLocalOnly,true);assert.equal(portalEnabled,true);
    await screenshot('local-portal-dashboard');await clickText('Sign out');await waitFor('location.pathname==="/login"');
    assert.deepEqual(diagnostics.runtimeErrors,[]);assert.deepEqual(diagnostics.consoleErrors,[]);
    assert.deepEqual(diagnostics.failedRequests,[]);assert.deepEqual(diagnostics.httpErrors,[]);
    return;
  }
  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  assert.equal(await evaluate('!!document.querySelector("#setup-title")'), false);
  const desktopDashboard = await measureReadability('dashboard-desktop');
  assert.equal(desktopDashboard.body, 16); assert.equal(desktopDashboard.pageHeading, 30);
  assert.equal(desktopDashboard.contentWidth, 1100);
  await screenshot('dashboard-desktop', true);
  for (const width of [320, 375, 393, 430]) await layout('dashboard', width);
  await layout('dashboard-review', 393); await measureReadability('dashboard-mobile'); await screenshot('dashboard-mobile', true); await layout('dashboard-landscape', 850, 393);
  await layout('dashboard-large-text', 320, 850, '125%'); await evaluate('document.documentElement.style.fontSize = ""');
  await clickText('Profile & Settings');
  await waitFor('location.pathname === "/profile" && !!document.querySelector("#browser-title")');
  await waitFor('document.body.textContent.includes("No saved browser identifier")');
  assert.equal(await evaluate(`localStorage.getItem('saiv_device_fingerprint_v1')`), null);
  assert.deepEqual(await evaluate('window.__permissionRequests'), { camera: 0, location: 0 });
  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  const desktopProfile = await measureReadability('profile-desktop');
  assert.equal(desktopProfile.profileValue,16); assert.equal(desktopProfile.profileLabel,14);
  assert.equal(desktopProfile.contentWidth,1140);
  await screenshot('profile-desktop', true);
  await call('Emulation.setDeviceMetricsOverride', { width:1920,height:1080,deviceScaleFactor:1,mobile:false });
  assert.equal((await measureReadability('profile-wide-desktop')).contentWidth,1140);
  await screenshot('profile-wide-desktop',true);
  assert.ok(await evaluate(`[...document.querySelectorAll('.consent-card')].every(card=>getComputedStyle(card).backgroundColor==='rgb(255, 255, 255)')`));
  assert.ok(await evaluate(`!/[0-9]+:[0-9]+/.test(document.querySelector('.joined-date').textContent)`));
  for (const width of [320, 375, 393, 430]) await layout('profile', width);
  await layout('profile-review', 393); await measureReadability('profile-mobile'); await screenshot('profile-mobile', true); await layout('profile-landscape', 850, 393);
  await layout('profile-large-text', 320, 850, '125%'); await evaluate('document.documentElement.style.fontSize = ""');
  await clickText('Set up camera'); await waitFor('!!document.querySelector("[role=dialog]")');
  await layout('camera-setup-modal', 320);
  assert.deepEqual(await evaluate('window.__permissionRequests'), { camera: 0, location: 0 });
  assert.equal(await evaluate('document.activeElement.getAttribute("role")'), 'dialog');
  await call('Input.dispatchKeyEvent', { type:'keyDown', key:'Tab', code:'Tab', windowsVirtualKeyCode:9 });
  await call('Input.dispatchKeyEvent', { type:'keyUp', key:'Tab', code:'Tab', windowsVirtualKeyCode:9 });
  assert.ok(await evaluate('!!document.activeElement.closest("[role=dialog]")'));
  // The last focusable control wraps to the first without leaving the dialog.
  await evaluate(`(() => { const controls=document.querySelector('[role=dialog]').querySelectorAll('button:not(:disabled),a[href],input:not(:disabled)'); controls[controls.length-1].focus(); })()`);
  await call('Input.dispatchKeyEvent', { type:'keyDown', key:'Tab', code:'Tab', windowsVirtualKeyCode:9 });
  await call('Input.dispatchKeyEvent', { type:'keyUp', key:'Tab', code:'Tab', windowsVirtualKeyCode:9 });
  assert.ok(await evaluate('!!document.activeElement.closest("[role=dialog]")'));
  await call('Input.dispatchKeyEvent', { type:'keyDown', key:'Escape', code:'Escape', windowsVirtualKeyCode:27 });
  await call('Input.dispatchKeyEvent', { type:'keyUp', key:'Escape', code:'Escape', windowsVirtualKeyCode:27 });
  await waitFor('!document.querySelector("[role=dialog]")');
  assert.equal(await evaluate('document.activeElement.textContent.trim()'), 'Set up camera');
  await call('Page.reload'); await waitFor('!!document.querySelector("#browser-title")');
  assert.equal(await evaluate(`localStorage.getItem('saiv_device_fingerprint_v1')`), null);
  console.log('PASS: profile navigation, consent display, read-only devices, authentication refresh and modal keyboard handling');
  await clickText('Sign out'); await waitFor('location.pathname === "/login"');
  await navigate('/profile'); await waitFor('location.pathname === "/login"');
  console.log('PASS: logout removes protected profile access');
  // Additional VISUAL FIXTURES only: no backend records are inserted or approved.
  // This exercises populated cards and a failed-challenge result even when the
  // real disposable account has no enrollments or attendance records.
  require('./register.cjs');
  const React = require('react');
  const { renderToStaticMarkup } = require('react-dom/server');
  const Sessions = require('../components/student/AvailableSessions.tsx').default;
  const Summary = require('../components/checkin/CheckinResultSummary.tsx').default;
  const course = { id:'layout-course', code:'QA1000', name:'Presentation layout fixture', require_face_recognition:true, require_device_binding:true };
  const session = { id:'layout-session', course_id:course.id, name:'Presentation session — long title for narrow screens', session_type:'lecture', status:'active',
    venue_name:'North Spine seminar room', scheduled_start:'2026-11-05T00:00:00', scheduled_end:'2026-11-05T02:00:00',
    checkin_opens_at:'2020-01-01T00:00:00', checkin_closes_at:'2099-01-01T00:00:00', require_liveness_check:true, require_face_match:true };
  const html = renderToStaticMarkup(React.createElement('main',{className:'student-app page-width page-content space-y-6'},
    React.createElement(Sessions,{sessions:[session],courses:[course],attempts:new Map(),loading:false,historyReady:true,error:'',user:{camera_consent:true,geolocation_consent:true,face_enrolled:true},onRefresh(){},findExistingAttempt:async()=>null,onBegin(){} }),
    React.createElement('section',{className:'panel'},React.createElement(Summary,{activeChallenge:true,result:{status:'rejected',risk_score:.4289,face_match_passed:true,face_match_score:.89,liveness_passed:false,liveness_score:0,qr_code_verified:false,distance_from_venue_meters:1300.56,risk_factors:'["liveness_failed","geo_out_of_bounds","attestation_failed","vpn_detected"]'}}))));
  await evaluate(`document.body.innerHTML=${JSON.stringify(html)}`);
  for(const width of [320,375,393,430]) await layout('presentation-fixture-only',width);
  await screenshot('session-result-layout-fixture');
  await layout('presentation-fixture-large-text',320,850,'125%');
  console.log('PASS: populated presentation fixtures fit narrow screens; no real attendance or biometric result is claimed');
  const Setup = require('../components/student/StudentSetup.tsx').default;
  const { BrowserRegistrationRow } = require('../components/student/BrowserRegistration.tsx');
  const { existingBrowserFixture } = require('./device-fixture.cjs');
  const layoutUser = { id:'layout-user', role:'student', is_active:true, full_name:'Presentation fixture only',
    email:'layout@example.test', camera_consent:true, geolocation_consent:false, face_enrolled:true };
  const profileFixture = renderToStaticMarkup(React.createElement('main',{className:'student-app page-width page-content space-y-6'},
    React.createElement('p',null,'Presentation fixtures only — no account or device state was changed.'),
    React.createElement(Setup,{user:layoutUser,onUserUpdate(){}}),
    React.createElement('section',{className:'panel'},React.createElement('h2',{className:'section-title mb-4'},'Browser Registration'),React.createElement('div',{className:'browser-list'},
      React.createElement(BrowserRegistrationRow,{device:existingBrowserFixture('layout-user'),current:true}),
      React.createElement(BrowserRegistrationRow,{device:existingBrowserFixture('layout-user',{id:'layout-inactive',device_fingerprint:'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',is_active:false,trust_score:'high'}),current:false})))));
  await evaluate(`document.body.innerHTML=${JSON.stringify(profileFixture)}`);
  for(const width of [320,375,393,430]) await layout('consent-browser-fixture-only',width);
  assert.ok(await evaluate(`[...document.querySelectorAll('.consent-card')].every(card=>getComputedStyle(card).backgroundColor==='rgb(255, 255, 255)')`));
  await screenshot('consent-browser-layout-fixture',true);
  await measureReadability('consent-browser-fixture-mobile');
  const browserWeights = await evaluate(`({identifier:getComputedStyle(document.querySelector('.browser-identity strong')).fontWeight,label:getComputedStyle(document.querySelector('.browser-identity > span')).fontWeight,metadata:getComputedStyle(document.querySelector('.browser-trust')).fontWeight})`);
  assert.equal(browserWeights.identifier,'700'); assert.equal(browserWeights.metadata,'400');
  assert.ok(Number(browserWeights.identifier)>=Number(browserWeights.label));
  await layout('consent-browser-fixture-large-text',320,850,'125%');
  await evaluate(`document.querySelector('.browser-row').open=true`);
  await layout('expanded-browser-fixture-only',320);
  const Modal = require('../components/ui/ModalFrame.tsx').default;
  const resultFixture = renderToStaticMarkup(React.createElement('div',{className:'student-app'},React.createElement(Modal,{label:'Result presentation fixture only',onClose(){}},
    React.createElement('h2',{className:'section-title mb-1'},'Attendance Check-in'),
    React.createElement('p',{className:'text-xs muted mb-5'},'Presentation fixture only — not a real attendance outcome.'),
    React.createElement(Summary,{activeChallenge:true,result:{status:'rejected',risk_score:.41,face_match_passed:true,face_match_score:.87,liveness_passed:false,liveness_score:0,qr_code_verified:false,distance_from_venue_meters:70,risk_factors:'["liveness_failed","attestation_failed"]'}}),
    React.createElement('button',{className:'btn-primary w-full mt-4',type:'button'},'Done'))));
  await evaluate(`document.body.innerHTML=${JSON.stringify(resultFixture)}`);
  for(const width of [320,375,393,430]) {
    await layout('result-dialog-fixture-only',width);
    assert.ok(await evaluate(`document.querySelector('.modal-panel').scrollWidth <= document.querySelector('.modal-panel').clientWidth + 1`));
  }
  await screenshot('result-dialog-layout-fixture');
  const resultTypography = await measureReadability('result-dialog-fixture-mobile');
  assert.equal(resultTypography.resultLabel,14); assert.equal(resultTypography.resultMetric,18); assert.equal(resultTypography.resultDetails,14);
  console.log('PASS: white mixed-state consent cards, populated browser disclosures and result dialog layouts (presentation fixtures only)');
  const History = require('../components/student/AttendanceHistory.tsx').default;
  const Header = require('../components/ui/StudentHeader.tsx').default;
  const polishFixture = renderToStaticMarkup(React.createElement('div',{className:'student-app'},
    React.createElement(Header,{current:'dashboard',onSignOut(){}}),
    React.createElement('main',{className:'page-width page-content space-y-6'},
      React.createElement('p',{className:'muted text-sm'},'Presentation fixture only — no attendance or device records were changed.'),
      React.createElement(History,{items:['approved','rejected','flagged','pending','appealed'].map((status,index)=>({id:`layout-${status}`,session_id:`layout-${status}`,student_id:'layout-user',status,checked_in_at:['2026-11-05T05:14:00','2026-11-05T03:58:00','2026-11-05T02:36:00','2026-11-05T02:24:00','2026-11-05T02:16:00'][index],risk_score:0})),total:5,complete:true,loading:false,error:'',labels:{'layout-approved':'Example approved session','layout-rejected':'Example rejected session with a longer title','layout-flagged':'Example flagged session','layout-pending':'Example pending session','layout-appealed':'Example appealed session'},onRefresh(){},onLoadMore(){}}),
      React.createElement('section',{className:'panel'},React.createElement('h2',{className:'section-title mb-4'},'Browser Registration'),
        React.createElement('div',{className:'browser-list'},React.createElement(BrowserRegistrationRow,{device:existingBrowserFixture('layout-user'),current:true}),React.createElement(BrowserRegistrationRow,{device:existingBrowserFixture('layout-user',{id:'layout-other',device_fingerprint:'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'}),current:false}))))));
  await evaluate(`document.body.innerHTML=${JSON.stringify(polishFixture)}`);
  assert.equal(await evaluate(`document.querySelectorAll('.history-row .tag-approved svg,.history-row .tag-rejected svg').length`),0);
  assert.deepEqual(await evaluate(`[...document.querySelectorAll('.history-row .tag-approved,.history-row .tag-rejected')].map(el=>({text:el.textContent.trim(),background:getComputedStyle(el).backgroundColor}))`),[
    {text:'approved',background:'rgb(227, 245, 233)'},{text:'rejected',background:'rgb(255, 235, 237)'}]);
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  async function checkHistoryAlignment(desktop) {
    const positions = await evaluate(`[...document.querySelectorAll('.history-row')].map(row=>{const badge=row.querySelector('.tag').getBoundingClientRect();const date=row.querySelector('p').getBoundingClientRect();return {width:badge.width,height:badge.height,right:badge.right,dateLeft:date.left};})`);
    assert.equal(positions.length,5);
    for (const position of positions) {
      assert.ok(Math.abs(position.width-positions[0].width)<.5,'all history status badges have equal widths');
      assert.ok(Math.abs(position.height-positions[0].height)<.5,'all history status badges have equal heights');
      assert.ok(Math.abs(position.right-positions[0].right)<.5,'all history status badges share the same right edge');
      if (desktop) assert.ok(Math.abs(position.dateLeft-positions[0].dateLeft)<.5,'desktop dates share one column');
    }
  }
  await checkHistoryAlignment(true);
  await screenshot('visual-polish-fixture-desktop',true);
  for(const width of [320,375,393,430]) { await layout('visual-polish-fixture-only',width); await checkHistoryAlignment(false); }
  await layout('visual-polish-fixture-review',393); await screenshot('visual-polish-fixture-mobile',true);
  await layout('history-alignment-large-text',320,850,'125%'); await checkHistoryAlignment(false);
  console.log('PASS: all five history badges share equal size/alignment, desktop dates align and browser identifiers are bold (presentation fixture only)');
  // Render snapshots of the actual check-in component. The fixture advances
  // only in memory; its fake frames are never sent to an API or stored as biometrics.
  const checkinFixtures = await require('./checkin-visual-fixture.cjs')();
  async function showCheckin(name) {
    await evaluate(`document.body.innerHTML=${JSON.stringify(checkinFixtures[name])}`);
  }
  async function checkModalLayout() {
    const dimensions = await evaluate(`(() => {
      const panel=document.querySelector('.checkin-dialog'), r=panel.getBoundingClientRect();
      return {left:r.left,right:r.right,viewport:innerWidth,client:panel.clientWidth,scroll:panel.scrollWidth,
        overflow:getComputedStyle(panel).overflowY,
        targets:[...panel.querySelectorAll('button,summary,input')].filter(el=>{const r=el.getBoundingClientRect();return r.width>0&&(r.width<43.5||r.height<43.5)}).map(el=>el.textContent.trim())};
    })()`);
    assert.ok(dimensions.left>=0 && dimensions.right<=dimensions.viewport+1,'dialog fits viewport');
    assert.ok(dimensions.scroll<=dimensions.client+1,'dialog has no horizontal overflow');
    assert.equal(dimensions.overflow,'auto'); assert.deepEqual(dimensions.targets,[],'dialog targets at least 44px');
  }
  await showCheckin('initial');
  await evaluate(`document.documentElement.style.fontSize=''`);
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await checkModalLayout(); await screenshot('checkin-initial-desktop');
  for(const width of [320,375,393,430]) { await layout('checkin-initial',width); await checkModalLayout(); }
  await layout('checkin-initial-review',393); await screenshot('checkin-initial-mobile');
  // Keyboard input remains usable. This is a static UI fixture, not a submitted instructor code.
  await fill('#checkin-qr-code','layout-only');
  assert.equal(await evaluate('document.querySelector("#checkin-qr-code").value'),'layout-only');
  for(const name of ['blink','progress','headTurn']) {
    await showCheckin(name);
    for(const width of [320,375,393,430]) { await layout(`checkin-${name}`,width); await checkModalLayout(); }
    await layout(`checkin-${name}-large-text`,320,850,'125%'); await checkModalLayout();
    await layout(`checkin-${name}-landscape`,850,393); await checkModalLayout();
  }
  // A non-biometric asymmetric stream verifies the VISIBLE preview, not recognition.
  await showCheckin('blink'); await layout('checkin-camera-review',393);
  await evaluate(`(async()=>{
    const source=document.createElement('canvas');source.width=640;source.height=480;
    const ctx=source.getContext('2d');ctx.fillStyle='#dc2626';ctx.fillRect(0,0,320,480);
    ctx.fillStyle='#2563eb';ctx.fillRect(320,0,320,480);ctx.fillStyle='white';ctx.font='bold 34px Arial';
    ctx.fillText('A / LEFT',45,240);ctx.fillText('B / RIGHT',365,240);
    const video=document.querySelector('video');video.srcObject=source.captureStream(5);await video.play();
    video.scrollIntoView({block:'center'});
  })()`);
  await new Promise(resolve=>setTimeout(resolve,200));
  const videoBox = await evaluate(`(()=>{const video=document.querySelector('video'),r=video.getBoundingClientRect(),s=getComputedStyle(video);
    return {x:r.x,y:r.y,width:r.width,height:r.height,transform:s.transform,scale:s.scale,rotate:s.rotate,fit:s.objectFit};})()`);
  assert.equal(videoBox.transform,'none'); assert.equal(videoBox.scale,'none'); assert.equal(videoBox.rotate,'none');
  assert.equal(videoBox.fit,'contain','preview shows complete frames without cropping');
  const {data:previewPng} = await call('Page.captureScreenshot',{format:'png',clip:{x:videoBox.x,y:videoBox.y,width:videoBox.width,height:videoBox.height,scale:1}});
  const previewPixels = await evaluate(`(async()=>{const img=new Image();img.src='data:image/png;base64,${previewPng}';await img.decode();
    const c=document.createElement('canvas');c.width=img.width;c.height=img.height;const ctx=c.getContext('2d');ctx.drawImage(img,0,0);
    return [.4,.6].map(x=>[...ctx.getImageData(Math.floor(c.width*x),Math.floor(c.height*.25),1,1).data]);})()`);
  assert.ok(previewPixels[0][0]>previewPixels[0][2],'visible left stays red');
  assert.ok(previewPixels[1][2]>previewPixels[1][0],'visible right stays blue');
  await screenshot('checkin-blink-mobile');
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await evaluate(`document.querySelector('.checkin-dialog').scrollTop=0`); await screenshot('checkin-blink-desktop');
  await evaluate(`document.querySelector('video').srcObject.getTracks().forEach(track=>track.stop())`);
  for(const name of ['progress','headTurn']) {
    await showCheckin(name); await layout(`checkin-${name}-review`,393);
    await evaluate(`document.querySelector('.checkin-challenge-status').scrollIntoView({block:'start'})`);
    await screenshot(`checkin-${name}-mobile`);
  }
  console.log('PASS: check-in steps, native QR keyboard input, challenge layouts, 44px targets and scroll at all four phone widths, landscape and larger text');
  console.log('PASS: asymmetric non-biometric video screenshot preserves left/right; camera pixel submissions were not exercised in this browser fixture');
  await require('./checkin-preview-layout.cjs')({call,evaluate,waitFor,layout,screenshot,html:checkinFixtures.blink,diagnostics});
  assert.ok(diagnostics.mutations.every(request => ['/api/v1/auth/login','/api/v1/auth/logout'].includes(request.path)), 'browser viewing never mutates devices, consent, enrollment or attendance');
  assert.deepEqual(diagnostics.runtimeErrors, []);
  assert.deepEqual(diagnostics.failedRequests, []);
  assert.deepEqual(diagnostics.httpErrors, []);
  assert.deepEqual(diagnostics.consoleErrors, []);
  console.log(`PASS: ${diagnostics.layouts.length} responsive checks; no page overflow or unexpected browser/network errors`);
}

main().catch(error => { console.error(error.message); process.exitCode = 1; }).finally(async () => {
  await fs.mkdir(output, { recursive: true });
  await fs.writeFile(path.join(output, 'results.json'), JSON.stringify(diagnostics, null, 2));
  if (socket?.readyState === WebSocket.OPEN) { try { await call('Browser.close'); } catch {} socket.close(); }
  if (child && child.exitCode === null) child.kill();
});
