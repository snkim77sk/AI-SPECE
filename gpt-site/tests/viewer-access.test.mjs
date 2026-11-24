import test from 'node:test';
import assert from 'node:assert/strict';
import {registerHooks} from 'node:module';
import {existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
registerHooks({resolve(spec,ctx,next){
 if(spec.startsWith('.')&&!/\.(ts|mjs|js)$/.test(spec)&&ctx.parentURL?.startsWith('file:')){
  const candidate=new URL(spec+'.ts',ctx.parentURL);if(existsSync(fileURLToPath(candidate)))return next(candidate.href,ctx);
 }
 return next(spec,ctx);
}});
const {viewerCapabilities,viewerHref,fetchViewerAccount}=await import('../lib/viewer-access.ts');
const member=(status='approved',role='member')=>({authenticated:true,member:{displayName:'회원',status,role}});

test('only an approved administrator may manage; preview only removes capabilities',()=>{
 for(const account of [null,{authenticated:false,member:null},member('pending'),member('suspended'),member('rejected'),member('pending','admin')]){
  assert.deepEqual(viewerCapabilities(true,account),{canRead:false,canManage:false,isAdministrator:false,preview:false});
  assert.deepEqual(viewerCapabilities(true,account,true),{canRead:false,canManage:false,isAdministrator:false,preview:false});
 }
 assert.deepEqual(viewerCapabilities(true,member()),{canRead:true,canManage:false,isAdministrator:false,preview:false});
 assert.deepEqual(viewerCapabilities(true,member('approved','admin'),true),{canRead:true,canManage:false,isAdministrator:true,preview:true});
 assert.equal(viewerCapabilities(true,member('approved','admin')).canManage,true);
 assert.equal(viewerCapabilities(false,null).canManage,true);
 assert.equal(viewerCapabilities(false,null,true).canManage,false);
 assert.equal(viewerCapabilities(false,null,false,true).canManage,false);
});

test('preview survives internal navigation and API queries without rewriting external links',()=>{
 assert.equal(viewerHref('/shopping?category=POLE#results',true),'/shopping?category=POLE&viewer=member#results');
 assert.equal(viewerHref('/api/budget?viewer=member&service=QWGJK',true),'/api/budget?viewer=member&service=QWGJK');
 assert.equal(viewerHref('/api/deliveries?view=history&category=POLE',true),'/api/deliveries?view=history&category=POLE&viewer=member');
 for(const value of ['https://example.test/a','//example.test/a','/\\example.test/a','#rows'])assert.equal(viewerHref(value,true),value);
 assert.equal(viewerHref('/dashboard',false),'/dashboard');
});

test('account boundary validates actual membership JSON and rejects HTTP failures',async()=>{
 const controller=new AbortController();
 let options;
 const account=await fetchViewerAccount(async(url,init)=>{assert.equal(url,'/api/public-auth/me');options=init;return Response.json(member());},controller.signal);
 assert.equal(account.member.role,'member');assert.equal(options.cache,'no-store');assert.equal(options.credentials,'same-origin');
 await assert.rejects(fetchViewerAccount(async()=>Response.json({error:'PRIVATE'},{status:503}),controller.signal),/VIEWER_ACCESS_UNAVAILABLE/);
 await assert.rejects(fetchViewerAccount(async()=>Response.json({authenticated:true,member:{role:'admin'}}),controller.signal),/회원 상태/);
});

test('both request and body stalls expire and aborted responses cannot restore privileges',async()=>{
 const controller=new AbortController();
 await assert.rejects(fetchViewerAccount(()=>new Promise(()=>{}),controller.signal,10),/VIEWER_ACCESS_TIMEOUT/);
 await assert.rejects(fetchViewerAccount(async()=>({ok:true,json:()=>new Promise(()=>{})}),controller.signal,10),/VIEWER_ACCESS_TIMEOUT/);
 let resolve;
 const cancel=new AbortController();
 const pending=fetchViewerAccount(()=>new Promise(r=>{resolve=r;}),cancel.signal,1000);
 cancel.abort();resolve(Response.json(member('approved','admin')));
 await assert.rejects(pending,{name:'AbortError'});
});
