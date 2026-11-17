// Visual-only tests: asymmetric canvas streams, never real camera evidence or API submissions.
const assert = require('node:assert/strict');

module.exports = async function checkPreviewLayout({ call, evaluate, waitFor, layout, screenshot, html, diagnostics }) {
  await evaluate(`document.body.innerHTML=${JSON.stringify(html)}`);
  await evaluate(`window.__previewSource=document.createElement('canvas');
    window.__drawPreviewSource=(width,height)=>{
      const c=window.__previewSource;c.width=width;c.height=height;const ctx=c.getContext('2d');
      ctx.fillStyle='#dc2626';ctx.fillRect(0,0,width/2,height);
      ctx.fillStyle='#2563eb';ctx.fillRect(width/2,0,width/2,height);
      ctx.fillStyle='#16a34a';ctx.fillRect(0,0,width,height*.1);
      ctx.fillStyle='#eab308';ctx.fillRect(0,height*.9,width,height*.1);
      ctx.strokeStyle='white';ctx.lineWidth=4;ctx.beginPath();ctx.ellipse(width*.5,height*.5,width*.22,height*.31,0,0,Math.PI*2);ctx.stroke();
      ctx.fillStyle='white';ctx.textAlign='center';ctx.font='bold '+Math.round(width*.045)+'px Arial';
      ctx.fillText('A / LEFT',width*.25,height*.46);ctx.fillText('B / RIGHT',width*.75,height*.46);
      ctx.font=Math.round(width*.032)+'px Arial';ctx.fillText('SYNTHETIC FULL FRAME',width*.5,height*.63);
    };`);
  const measurements = diagnostics.previewLayouts = [];
  async function resizeStream(width,height) {
    await evaluate(`window.__drawPreviewSource(${width},${height})`);
    await waitFor(`document.querySelector('video').videoWidth===${width} && document.querySelector('video').videoHeight===${height}`);
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  async function view(name,width,height) {
    await layout(name,width,height);
    if(width>=1000) await call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
    await evaluate(`document.querySelector('video').scrollIntoView({block:'center'})`);
  }
  async function geometry() {
    return evaluate(`(()=>{const v=document.querySelector('video'),r=v.getBoundingClientRect(),s=getComputedStyle(v),p=v.closest('.modal-panel');
      return {x:r.x,y:r.y,width:r.width,height:r.height,
        contentWidth:r.width-parseFloat(s.borderLeftWidth)-parseFloat(s.borderRightWidth),
        contentHeight:r.height-parseFloat(s.borderTopWidth)-parseFloat(s.borderBottomWidth),
        streamWidth:v.videoWidth,streamHeight:v.videoHeight,settings:v.srcObject.getVideoTracks()[0].getSettings(),
        transform:s.transform,scale:s.scale,rotate:s.rotate,fit:s.objectFit,radius:s.borderRadius,
        viewportWidth:innerWidth,viewportHeight:innerHeight,panelWidth:p.clientWidth,panelScrollWidth:p.scrollWidth};})()`);
  }
  async function edgePixels(box) {
    const {data} = await call('Page.captureScreenshot',{format:'png',clip:{x:box.x,y:box.y,width:box.width,height:box.height,scale:1}});
    return evaluate(`(async()=>{const image=new Image();image.src='data:image/png;base64,${data}';await image.decode();
      const c=document.createElement('canvas');c.width=image.width;c.height=image.height;const ctx=c.getContext('2d');ctx.drawImage(image,0,0);
      return [[.03,.5],[.97,.5],[.5,.03],[.5,.97]].map(([x,y])=>[...ctx.getImageData(Math.floor(c.width*x),Math.floor(c.height*y),1,1).data]);})()`);
  }
  async function validate(name) {
    const box=await geometry();
    const relativeError=Math.abs((box.contentWidth/box.contentHeight)/(box.streamWidth/box.streamHeight)-1);
    assert.ok(relativeError<.012,`${name}: element follows stream ratio (error ${relativeError})`);
    assert.ok(box.x>=0 && box.x+box.width<=box.viewportWidth+1,`${name}: preview fits horizontally`);
    assert.ok(box.y>=0 && box.y+box.height<=box.viewportHeight+1,`${name}: preview fits vertically`);
    assert.ok(box.panelScrollWidth<=box.panelWidth+1,`${name}: no dialog overflow`);
    assert.equal(box.fit,'contain'); assert.equal(box.transform,'none'); assert.equal(box.scale,'none'); assert.equal(box.rotate,'none');
    assert.ok(parseFloat(box.radius)>0,'rounded corners preserved');
    const [left,right,top,bottom]=await edgePixels(box);
    assert.ok(left[0]>150 && left[2]<100,`${name}: left edge visible, no black bar or mirror`);
    assert.ok(right[2]>150 && right[0]<100,`${name}: right edge visible, no black bar or mirror`);
    assert.ok(top[1]>100 && top[0]<80,`${name}: entire top edge visible, no forehead-side crop`);
    assert.ok(bottom[0]>150 && bottom[1]>100 && bottom[2]<80,`${name}: entire bottom edge visible, no chin-side crop`);
    measurements.push({name,...box,relativeError});
    return box;
  }
  try {
    await evaluate(`(async()=>{window.__drawPreviewSource(640,480);const v=document.querySelector('video');
      v.srcObject=window.__previewSource.captureStream(15);await v.play();window.__originalPreview=v;})()`);
    await waitFor(`document.querySelector('video').videoWidth===640`);
    // Exact previous sizing, applied only in the test page, for reproducible before screenshots.
    await evaluate(`(()=>{const style=document.createElement('style');style.id='preview-before-style';
      style.textContent='.checkin-camera-preview{width:100%;height:auto;aspect-ratio:4/3;max-height:min(32dvh,260px);object-fit:contain;margin:.65rem 0 0}';
      document.head.append(style);})()`);
    await view('preview-before-desktop',1440,1000);
    const beforeDesktop=await geometry();
    assert.ok(Math.abs(beforeDesktop.contentWidth/beforeDesktop.contentHeight-4/3)>.1,'before desktop reproduces aspect mismatch');
    await screenshot('preview-before-desktop');
    await resizeStream(480,640); await view('preview-before-mobile',393,850);
    const beforeMobile=await geometry();
    assert.ok(beforeMobile.contentWidth/beforeMobile.contentHeight>1,'before portrait feed is boxed into a wide preview');
    await screenshot('preview-before-mobile');
    await evaluate(`document.querySelector('#preview-before-style').remove()`);
    for(const [width,height] of [[640,480],[1280,720],[480,640],[720,1280]]) {
      await resizeStream(width,height);
      for(const [vw,vh] of [[1440,1000],[1280,720],...[320,375,393,430].map(w=>[w,850]),...[320,375,393,430].map(h=>[850,h])]) {
        const name=`preview-${width}x${height}-at-${vw}x${vh}`;
        await view(name,vw,vh); await validate(name);
      }
    }
    await resizeStream(640,480); await view('preview-after-desktop',1440,1000); await screenshot('preview-after-desktop');
    await resizeStream(480,640); await view('preview-after-mobile',393,850); await screenshot('preview-after-mobile');
    assert.equal(await evaluate(`window.__originalPreview===document.querySelector('video')`),true,'metadata and viewport changes never replace the video');
    assert.ok(measurements.find(x=>x.name==='preview-480x640-at-393x850').height>beforeMobile.height*1.5,'portrait preview uses substantially more height');
    console.log(`PASS: ${measurements.length} real Chrome camera-layout cases; four stream ratios, phone/desktop/landscape, correct proportions and all four frame edges visible`);
  } finally {
    await evaluate(`document.querySelector('video')?.srcObject?.getTracks().forEach(track=>track.stop());document.querySelector('#preview-before-style')?.remove()`);
  }
};
