const { test } = require('node:test');
const assert = require('node:assert/strict');
const { resolvePublicConfiguration } = require('../lib/public-config.ts');
const { getPublicConfiguration } = require('../lib/client-config.ts');

test('direct API deployments retain their configured URL; explicit proxy emits only a public capability flag',()=>{
  assert.equal(resolvePublicConfiguration({}).apiViaFrontend,undefined);
  const config=resolvePublicConfiguration({SAIV_BACKEND_INTERNAL_URL:'http://backend:8000',NEXT_PUBLIC_API_URL:'https://private.devtunnels.ms'});
  assert.equal(config.apiViaFrontend,true);
  assert.ok(!JSON.stringify(config).includes('http://backend:8000'));
});
test('browser uses its current origin only when the server explicitly advertises API proxy support',async()=>{
  global.window={location:{origin:'http://localhost:8080'}};
  global.fetch=async()=>Response.json({apiUrl:'https://private.devtunnels.ms/api/v1',staffPortalUrl:'https://private.devtunnels.ms/portal/',apiViaFrontend:true});
  const config=await getPublicConfiguration();
  assert.equal(config.apiUrl,'http://localhost:8080/api/v1');
});
