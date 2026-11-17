const { test, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { create, act } = require('react-test-renderer');
const axios = require('axios');
function mock(name, exports) { const id = require.resolve(name); require.cache[id] = {id, filename:id, loaded:true, exports}; }
mock('next/link', {__esModule:true, default:({children,...props})=>React.createElement('a',props,children)});
mock('next/navigation', {useRouter:()=>({push(){throw Error('Recovery must not sign in/redirect');}})});
global.window = {};
global.fetch = async () => Response.json({apiUrl:'http://localhost:8000/api/v1',staffPortalUrl:'https://staff.example.org'});
const {api} = require('../lib/api.ts');
const auth = require('../lib/auth.ts');
const recovery = require('../lib/password-reset.ts');
const Page = require('../app/forgot-password/page.tsx').default;
const Login = require('../app/login/page.tsx').default;
let renderer, calls, respond, writes;
const ok = (config,data={message:'Generic confirmation'})=>({config,data,status:200,statusText:'OK',headers:{}});
const fail = (config,status,data={message:'synthetic-secret-123456'},headers={})=>{throw new axios.AxiosError('synthetic',undefined,config,undefined,{...ok(config,data),status,headers});};
const text=()=>JSON.stringify(renderer.toJSON());
const mount=async(Component=Page)=>{await act(async()=>{renderer=create(React.createElement(Component));});};
const input=async(id,value)=>{await act(async()=>renderer.root.findByProps({id}).props.onChange({target:{value}}));};
const submit=async()=>{await act(async()=>renderer.root.findByType('form').props.onSubmit({preventDefault(){}}));};
const button=label=>renderer.root.findAllByType('button').find(node=>node.children.join('')===label);
const click=async label=>{await act(async()=>button(label).props.onClick());};
async function request() { await input('recovery-email',' Student@Example.edu '); await submit(); }
async function fill() { await input('reset-code','123456'); await input('new-password','new-password1'); await input('confirm-password','new-password1'); }
beforeEach(()=>{
  writes=[];const storage=new Map();global.sessionStorage={getItem:key=>storage.get(key)??null,setItem:(k,v)=>{storage.set(k,v);writes.push(k);},removeItem:k=>{storage.delete(k);writes.push(k);}};
  calls=[];respond=async config=>ok(config);
  api.defaults.adapter=async config=>{calls.push(config);return respond(config);};
});
afterEach(async()=>{if(renderer)await act(async()=>renderer.unmount());renderer=undefined;});

test('login exposes a recovery link and retains registration navigation',async()=>{
  await mount(Login);assert.equal(renderer.root.findAllByType('a').find(n=>n.props.href==='/forgot-password').children.join(''),'Forgot Password?');
  assert.ok(renderer.root.findByProps({href:'/register'}));assert.equal(calls.length,0);
});
test('recovery initial state is public, labelled and responsive without requests',async()=>{
  await mount();assert.equal(calls.length,0);assert.match(text(),/auth-layout/);assert.match(text(),/auth-form/);
  assert.equal(renderer.root.findByProps({id:'recovery-email'}).props.type,'email');assert.match(text(),/student or staff/);
});
test('known/unknown emails get identical non-enumerating frontend confirmations',async()=>{
  await mount();await request();assert.match(text(),/eligible account/);assert.match(text(),/10/);
  assert.deepEqual(JSON.parse(calls[0].data),{email:'student@example.edu'});
  await click('Use a different email');await input('recovery-email','unknown@example.edu');await submit();
  assert.match(text(),/eligible account/);assert.equal(calls.length,2);assert.deepEqual(writes,[]);
});
test('OTP input supports phone keyboard/autofill and numeric paste',async()=>{
  await mount();await request();const otp=renderer.root.findByProps({id:'reset-code'});
  assert.equal(otp.props.inputMode,'numeric');assert.equal(otp.props.autoComplete,'one-time-code');assert.equal(otp.props.maxLength,6);
  await input('reset-code','123 456');assert.equal(renderer.root.findByProps({id:'reset-code'}).props.value,'123456');
});
test('password mismatch is rejected without reset POST',async()=>{
  await mount();await request();await fill();await input('confirm-password','different-password');await submit();
  assert.match(text(),/do not match/);assert.equal(calls.length,1);
});
test('show/hide is an accessible toggle for both password fields',async()=>{
  await mount();await request();await click('Show passwords');assert.equal(button('Hide passwords').props['aria-pressed'],true);
  assert.equal(renderer.root.findByProps({id:'new-password'}).props.type,'text');await click('Hide passwords');
  assert.equal(renderer.root.findByProps({id:'confirm-password'}).props.type,'password');
});
test('successful combined reset submits actual schema, clears fields and never signs in',async()=>{
  await mount();await request();await fill();await submit();
  assert.equal(calls[1].url,'/auth/reset-password');assert.deepEqual(JSON.parse(calls[1].data),{email:'student@example.edu',code:'123456',new_password:'new-password1'});
  assert.match(text(),/Password changed/);assert.ok(renderer.root.findByProps({href:'/login'}));
  assert.doesNotMatch(text(),/new-password1|123456/);assert.deepEqual(writes,[]);
  assert.ok(calls.every(c=>!c.url.includes('/login')));
});
test('resend cooldown is 60 seconds, does not issue early, and resets after resend',async t=>{
  t.mock.timers.enable({apis:['Date','setInterval'],now:100000});
  await mount();await request();assert.equal(button('Resend Code in 60s').props.disabled,true);
  await click('Resend Code in 60s');assert.equal(calls.length,1);
  await act(async()=>t.mock.timers.tick(61000));await click('Resend Code');assert.equal(calls.length,2);
  assert.equal(button('Resend Code in 60s').props.disabled,true);
});
test('expiry feedback remains advisory; backend decides actual validity',async t=>{
  t.mock.timers.enable({apis:['Date','setInterval'],now:100000});await mount();await request();
  await act(async()=>t.mock.timers.tick(601000));assert.match(text(),/server checks actual expiry/);
  await fill();await submit();assert.equal(calls.length,2);
});
test('duplicate send submits only once while pending',async()=>{
  let resolve;respond=config=>new Promise(done=>{resolve=()=>done(ok(config));});await mount();await input('recovery-email','student@example.edu');
  let pending;await act(async()=>{pending=renderer.root.findByType('form').props.onSubmit({preventDefault(){}});await new Promise(r=>setImmediate(r));});
  await submit();assert.equal(calls.length,1);await act(async()=>{resolve();await pending;});
});
test('duplicate reset submits only once while pending',async()=>{
  await mount();await request();await fill();let resolve;respond=config=>new Promise(done=>{resolve=()=>done(ok(config));});
  let pending;await act(async()=>{pending=renderer.root.findByType('form').props.onSubmit({preventDefault(){}});await new Promise(r=>setImmediate(r));});
  await submit();assert.equal(calls.length,2);await act(async()=>{resolve();await pending;});
});
test('invalid/expired/reused code messages never echo input and clear secrets',async()=>{
  await mount();await request();await fill();respond=config=>fail(config,422,{message:'123456 new-password1'});await submit();
  assert.match(text(),/invalid, expired or already used/);assert.doesNotMatch(text(),/123456|new-password1/);
});
test('network interruption does not replay an uncertain reset',async()=>{
  await mount();await request();await fill();respond=config=>{throw new axios.AxiosError('network','ERR_NETWORK',config);};await submit();
  assert.equal(calls.length,2);assert.match(text(),/Try signing in with the new password/);assert.deepEqual(writes,[]);
});
test('429 imposes backend retry delay without exposing backend bodies',async t=>{
  t.mock.timers.enable({apis:['Date','setInterval'],now:100000});respond=config=>fail(config,429,{}, {'retry-after':'120'});
  await mount();await input('recovery-email','student@example.edu');await submit();assert.match(renderer.root.findByProps({role:'timer'}).children.join(''),/120 seconds/);
  await submit();assert.equal(calls.length,1);
});
test('unmount aborts pending recovery without session writes',async()=>{
  let signal;respond=config=>{signal=config.signal;return new Promise(()=>{});};await mount();await input('recovery-email','student@example.edu');
  await act(async()=>{void renderer.root.findByType('form').props.onSubmit({preventDefault(){}});await new Promise(r=>setImmediate(r));});
  await act(async()=>renderer.unmount());renderer=undefined;assert.equal(signal.aborted,true);assert.deepEqual(writes,[]);
});
test('reset routes are anonymous even with stored tokens; 401 does not refresh or clear another session',async()=>{
  auth.saveTokens('synthetic-access','synthetic-refresh');writes.length=0;
  respond=config=>fail(config,401);await assert.rejects(recovery.requestPasswordCode('test@example.edu'));
  await assert.rejects(recovery.resetPassword('test@example.edu','123456','password123'));
  assert.equal(calls.length,2);assert.ok(calls.every(c=>c.headers.Authorization===undefined));
  assert.equal(auth.getAccessToken(),'synthetic-access');assert.deepEqual(writes,[]);
});
for(const [code,password,confirmation,expected] of [
  ['12345','password123','password123','six-digit'],['123456','short','short','8 characters'],
  ['123456','a'.repeat(73),'a'.repeat(73),'72 UTF-8'],['123456','😀'.repeat(19),'😀'.repeat(19),'72 UTF-8'],
  ['123456','😀'.repeat(4),'😀'.repeat(4),'8 characters'],['123456','password123','otherpass123','do not match']]) {
  test(`validation: ${expected} (${password.length})`,()=>assert.match(recovery.resetPasswordValidation(code,password,confirmation),new RegExp(expected)));
}
test('valid unicode and ASCII passwords follow backend code-point/byte policy',()=>{
  assert.equal(recovery.resetPasswordValidation('123456','😀'.repeat(8),'😀'.repeat(8)),null);
  assert.equal(recovery.resetPasswordValidation('123456','a'.repeat(72),'a'.repeat(72)),null);
});
test('unexpected response text is never rendered',async()=>{
  await mount();respond=config=>ok(config,{message:'123456 password: synthetic-secret'});await request();
  assert.match(text(),/eligible account/);assert.doesNotMatch(text(),/synthetic-secret|123456/);
});
