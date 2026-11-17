// Browser layout fixtures from the actual modal, advanced with in-memory APIs.
// No HTTP requests, real camera/GPS evidence or attendance submissions occur.
module.exports = async function checkinVisualFixtures() {
  const React = require('react');
  const { create, act } = require('react-test-renderer');
  const { renderToStaticMarkup } = require('react-dom/server');
  const assert = require('node:assert/strict');
  require('axios'); // Initialize its Node adapter before installing DOM test doubles.
  const names = ['window','document','navigator','sessionStorage','HTMLMediaElement','fetch'];
  const originals = names.map(name => [name, Object.getOwnPropertyDescriptor(global, name)]);
  let renderer, api, adapter, encoded = 0;
  const storage = new Map();
  const memoryStorage = { getItem: key => storage.get(key) ?? null, setItem: (key,value) => storage.set(key,value), removeItem: key => storage.delete(key) };
  const { identifier, existingBrowserFixture } = require('./device-fixture.cjs');
  const video = { readyState: 2, videoWidth: 640, videoHeight: 480, srcObject: null, async play() {} };
  const user = { id:'checkin-visual-fixture', role:'student', is_active:true, camera_consent:true, geolocation_consent:true, face_enrolled:true };
  const session = { id:'checkin-visual-fixture', name:'Check-in preview · Presentation fixture only', status:'active', require_liveness_check:true,
    require_face_match:true, checkin_opens_at:'2020-01-01T00:00:00Z', checkin_closes_at:'2099-01-01T00:00:00Z' };
  function toElement(node) {
    if (node == null || typeof node === 'string') return node;
    return React.createElement(node.type, node.props, ...(node.children || []).map(toElement));
  }
  const snapshot = () => renderToStaticMarkup(React.createElement('div', { className:'student-app' }, toElement(renderer.toJSON())));
  const click = async label => {
    const button = renderer.root.findAllByType('button').find(node => node.children.join('') === label);
    assert.ok(button && !button.props.disabled, `fixture action ${label}: ${snapshot().replace(/<[^>]+>/g,' ')}`);
    await act(async () => button.props.onClick());
  };
  try {
    Object.defineProperty(global, 'navigator', { configurable:true, value:{
      mediaDevices:{ async getUserMedia() { return { getTracks:() => [{ stop(){}, addEventListener(){} }] }; } },
      geolocation:{ getCurrentPosition(done) { done({ timestamp:Date.now(), coords:{ latitude:1.3, longitude:103.7, accuracy:25 } }); } },
    } });
    global.window = { location:{href:'http://fixture.invalid/',origin:'http://fixture.invalid'}, crypto:{ randomUUID:() => identifier }, localStorage:{...memoryStorage, getItem:() => identifier} };
    global.sessionStorage = memoryStorage;
    global.fetch = async () => Response.json({ apiUrl:'http://fixture.invalid/api/v1',staffPortalUrl:'http://fixture.invalid/portal/' });
    global.HTMLMediaElement = { HAVE_CURRENT_DATA:2 };
    global.document = { visibilityState:'visible', addEventListener(){}, removeEventListener(){},
      createElement:() => ({ width:0,height:0,getContext:() => ({drawImage(){},clearRect(){}}),
        toDataURL:() => `data:image/jpeg;base64,${Buffer.from(`non-image-fixture-frame-${++encoded}`).toString('base64')}` }) };
    ({api} = require('../lib/api.ts')); adapter = api.defaults.adapter;
    const {saveTokens,saveUser} = require('../lib/auth.ts');
    saveTokens('in-memory-fixture-access','in-memory-fixture-refresh'); saveUser(user);
    let type = 'blink';
    api.defaults.adapter = async config => {
      let data;
      if (config.url === '/devices/my-devices') data = [existingBrowserFixture(user.id)];
      else if (config.url === '/checkins/liveness-challenge') data = {
        challenge_id:'in-memory-fixture', challenge_type:type, min_frames:type === 'blink' ? 3 : 2, max_frames:30, recommended_frames:8,
        instruction:type === 'blink' ? 'Look at the camera, blink once, then open your eyes.' : 'Look ahead, then slowly turn your head to one side.',
        expires_at:new Date(Date.now()+60000).toISOString(),
      };
      else throw Error(`Fixture blocked unexpected API call: ${config.url}`);
      return {config,data,status:200,statusText:'OK',headers:{}};
    };
    const Modal = require('../components/checkin/CheckInModal.tsx').default;
    await act(async () => { renderer = create(React.createElement(Modal, {user,session,policy:{imageRequired:true,faceMatchRequired:true},
      onClose(){},onCompleted(){throw Error('Fixture must never submit attendance');},onUnauthorized(){throw Error('Unexpected fixture auth failure');}}),
      { createNodeMock: element => element.type === 'video' ? video : null }); });
    const initial = snapshot();
    await click('Get Current Location'); await click('Open Camera'); await click('Request liveness challenge');
    const blink = snapshot();
    await click('Capture challenge');
    await act(async () => new Promise(resolve => setTimeout(resolve, 1100)));
    assert.ok(renderer.root.findByType('progress').props.value > 0);
    const progress = snapshot();
    await click('Cancel challenge');
    type = 'head_turn'; await click('Open Camera'); await click('Request liveness challenge');
    return {initial,blink,progress,headTurn:snapshot()};
  } finally {
    if (renderer) await act(async () => renderer.unmount());
    if (api) api.defaults.adapter = adapter;
    for (const [name,descriptor] of originals) {
      if (descriptor) Object.defineProperty(global,name,descriptor); else delete global[name];
    }
  }
};
