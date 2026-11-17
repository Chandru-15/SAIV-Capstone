const { test, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const React = require('react');
const { create, act } = require('react-test-renderer');
const { safePortalDestination } = require('../lib/portal-navigation.ts');
const { resolvePublicConfiguration } = require('../lib/public-config.ts');
let configuration = {};
const configId = require.resolve('../lib/client-config.ts');
require.cache[configId] = { id:configId, filename:configId, loaded:true, exports:{ getPublicConfiguration:async()=>configuration } };
const Portal = require('../components/student/AttendancePortalAction.tsx').default;
let renderer;
afterEach(async()=>{ if(renderer) await act(async()=>renderer.unmount()); renderer=undefined; configuration={}; });

test('student portal remains disabled without explicit deployment approval even when a URL exists', ()=>{
  const env={ NEXT_PUBLIC_STUDENT_PORTAL_URL:'/portal/' };
  assert.equal(resolvePublicConfiguration(env).studentPortalUrl,undefined);
  assert.equal(resolvePublicConfiguration({...env,SAIV_STUDENT_PORTAL_ENABLED:'true'}).studentPortalUrl,'/portal/');
});
test('invalid student portal configuration disables only that link, preserving API access', ()=>{
  const config=resolvePublicConfiguration({SAIV_STUDENT_PORTAL_ENABLED:'true',NEXT_PUBLIC_STUDENT_PORTAL_URL:'https://portal.example/?s=secret'});
  assert.equal(config.studentPortalUrl,undefined); assert.equal(config.apiUrl,'http://localhost:8000/api/v1');
});

test('local-only mode still requires the existing feature flag and configured URL',()=>{
  const env={SAIV_STUDENT_PORTAL_LOCAL_ONLY:'true',NEXT_PUBLIC_STUDENT_PORTAL_URL:'http://localhost:8501/portal/'};
  assert.equal(resolvePublicConfiguration(env).studentPortalUrl,undefined);
  const config=resolvePublicConfiguration({...env,SAIV_STUDENT_PORTAL_ENABLED:'true'});
  assert.equal(config.studentPortalUrl,env.NEXT_PUBLIC_STUDENT_PORTAL_URL);
  assert.equal(config.studentPortalLocalOnly,true);
  assert.equal(resolvePublicConfiguration({SAIV_STUDENT_PORTAL_ENABLED:'true',SAIV_STUDENT_PORTAL_LOCAL_ONLY:'true'}).studentPortalUrl,undefined);
});
for(const origin of ['http://localhost:8080','http://127.0.0.1:8080']) {
  test(`explicit local-only portal is enabled on ${origin} without passing session data`,async()=>{
    global.window={location:{origin}};
    configuration={studentPortalUrl:'http://localhost:8501/portal/',studentPortalLocalOnly:true};
    await act(async()=>{renderer=create(React.createElement(Portal));});
    const link=renderer.root.findByType('a');
    assert.equal(link.props.href,'http://localhost:8501/portal/'); assert.equal(link.props.target,'_blank');
    assert.equal(link.props.rel,'noopener noreferrer'); assert.equal(link.props.referrerPolicy,'no-referrer');
    assert.equal(new URL(link.props.href).search,''); assert.equal(new URL(link.props.href).username,'');
  });
}
for(const origin of ['https://gxcs7n6m-8080.asse.devtunnels.ms','http://campus.example','https://campus.example',
  'http://192.168.1.20:8080','http://localhost.example:8080','http://[::1]:8080']) {
  test(`local-development portal remains unavailable on ${origin}`,async()=>{
    global.window={location:{origin}};
    configuration={studentPortalUrl:'http://localhost:8501/portal/',studentPortalLocalOnly:true};
    await act(async()=>{renderer=create(React.createElement(Portal));});
    assert.equal(renderer.root.findByType('button').props.disabled,true);
    assert.equal(renderer.root.findAllByType('a').length,0);
  });
}
test('local-only mode rejects non-local destinations, relative gateway routes and absent browser origin',()=>{
  for(const target of ['https://portal.example/','/portal/','http://[::1]:8501/portal/']) {
    assert.equal(safePortalDestination(target,'http://localhost:8080',true),null);
  }
  assert.equal(safePortalDestination('http://localhost:8501/portal/',undefined,true),null);
});
for(const value of ['//evil.example/portal/','javascript:alert(1)','/portal/?s=secret','https://u:p@portal.example/',
  'https://portal.example/?token=secret','https://portal.example/#secret','/portal/\\evil','http://public.example/portal/','/dashboard']){
  test(`unsafe portal destination rejected: ${value.split('?')[0]}`,()=>assert.equal(safePortalDestination(value,'https://saiv.example'),null));
}
test('HTTPS mobile origin cannot navigate to loopback or downgrade HTTP destinations',()=>{
  assert.equal(safePortalDestination('http://localhost:8501/portal/','https://mobile.devtunnels.ms'),null);
  assert.equal(safePortalDestination('https://localhost:8501/portal/','https://mobile.devtunnels.ms'),null);
  assert.equal(safePortalDestination('/portal/','https://mobile.devtunnels.ms'),'/portal/');
});
test('unavailable portal renders a disabled action without a broken link',async()=>{
  global.window={location:{origin:'http://localhost:8080'}};
  await act(async()=>{renderer=create(React.createElement(Portal));});
  assert.equal(renderer.root.findByType('button').props.disabled,true);
  assert.equal(renderer.root.findAllByType('a').length,0);
  assert.equal(renderer.root.findAllByType('p').length,0);
  assert.equal(renderer.root.findByType('button').props['aria-describedby'],undefined);
});
test('approved configured portal uses full navigation without session credentials',async()=>{
  global.window={location:{origin:'https://saiv.example'}};configuration={studentPortalUrl:'/portal/'};
  await act(async()=>{renderer=create(React.createElement(Portal));});
  const link=renderer.root.findByType('a');assert.equal(link.props.href,'/portal/');
  assert.equal(link.props.rel,'noopener noreferrer');assert.equal(link.props.referrerPolicy,'no-referrer');
  assert.equal(renderer.root.findAllByType('p').length,0);
});
test('service worker leaves portal, custom portal paths and API navigations to the network',()=>{
  const handlers={};let fetched=0;
  const context={URL,Response,self:{location:{origin:'https://saiv.example'},addEventListener:(name,fn)=>{handlers[name]=fn;}},fetch:async()=>{fetched++;return new Response('ok');}};
  vm.runInNewContext(fs.readFileSync(require.resolve('../public/sw.js'),'utf8'),context);
  for(const path of ['/portal/','/portal/static/app.js','/attendance-portal/','/api/config','/api/v1/checkins/me']){
    handlers.fetch({request:{url:`https://saiv.example${path}`,method:'GET',mode:'navigate'},respondWith(){throw Error(`Intercepted ${path}`);}});
  }
  let handled=false;
  handlers.fetch({request:{url:'https://saiv.example/profile',method:'GET',mode:'navigate'},respondWith(){handled=true;}});
  assert.equal(handled,true);assert.equal(fetched,1);
});
