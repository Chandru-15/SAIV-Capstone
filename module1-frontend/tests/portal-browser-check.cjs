// Real dashboard click -> real unauthenticated Streamlit page. No portal sign-in.
const assert = require('node:assert/strict');

module.exports = async function verifyPortalNavigation({call,evaluate,port,url}) {
  const link=await evaluate(`(()=>{const a=[...document.querySelectorAll('a')].find(el=>el.textContent.includes('Attendance & Appeals'));
    if(!a)return null;const r=a.getBoundingClientRect();return {href:a.href,target:a.target,rel:a.rel,referrer:a.referrerPolicy,x:r.x+r.width/2,y:r.y+r.height/2};})()`);
  assert.ok(link,'dashboard portal link exists'); assert.equal(link.href,url); assert.equal(link.target,'_blank');
  assert.equal(link.rel,'noopener noreferrer'); assert.equal(link.referrer,'no-referrer');
  assert.equal(new URL(link.href).search,''); assert.equal(new URL(link.href).hash,'');
  const initial=new Set((await call('Target.getTargets')).targetInfos.map(target=>target.targetId));
  await call('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,x:link.x,y:link.y});
  await call('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,x:link.x,y:link.y});
  let page,ws;
  try {
    for(let attempt=0;attempt<100;attempt++) {
      const pages=await(await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      page=pages.find(target=>target.type==='page'&&!initial.has(target.id)&&target.url.startsWith(url));
      if(page)break;
      await new Promise(resolve=>setTimeout(resolve,100));
    }
    assert.ok(page,'click opens a separate Streamlit tab');
    ws=new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
    let id=0; const pending=new Map();
    ws.onmessage=event=>{const message=JSON.parse(event.data),request=pending.get(message.id);if(!request)return;
      pending.delete(message.id);clearTimeout(request.timer);message.error?request.reject(Error(message.error.message)):request.resolve(message.result);};
    const inspect=()=>new Promise((resolve,reject)=>{const key=++id;const timer=setTimeout(()=>{pending.delete(key);reject(Error('Portal browser inspection timed out'));},10000);
      pending.set(key,{resolve,reject,timer});ws.send(JSON.stringify({id:key,method:'Runtime.evaluate',params:{returnByValue:true,
        expression:`({url:location.href,password:!!document.querySelector('input[type=password]'),
          login:[...document.querySelectorAll('button')].some(el=>el.innerText==='Log in'),opener:window.opener===null,
          referrer:document.referrer,nextToken:sessionStorage.getItem('saiv_access_token')!==null})`}}));});
    let state;
    for(let attempt=0;attempt<150;attempt++) {
      const result=await inspect();state=result.result.value;
      if(state?.password&&state?.login)break;
      await new Promise(resolve=>setTimeout(resolve,100));
    }
    assert.equal(state.url,url);assert.equal(state.password,true);assert.equal(state.login,true);
    assert.equal(state.opener,true);assert.equal(state.referrer,'');assert.equal(state.nextToken,false);
    console.log('PASS: real dashboard click opens Streamlit email/password login in a separate tab; clean URL, no opener/referrer or Next.js token');
  } finally {
    if(ws)ws.close(); if(page)await call('Target.closeTarget',{targetId:page.id});
  }
};
