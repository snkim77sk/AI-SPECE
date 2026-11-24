import test from 'node:test';
import assert from 'node:assert/strict';
import {registerHooks} from 'node:module';
import packageInfo from '../package.json' with {type:'json'};

let block=null,enabled=false,adminError=null,guardError=null,adminChecks=0;
globalThis.__g2bRuntimeAuth={
 guard:async()=>{if(guardError)throw guardError;return block;},
 enabled:()=>enabled,
 admin:async()=>{adminChecks++;if(adminError)throw adminError;return {role:'admin',status:'approved'};},
};
registerHooks({resolve(spec,ctx,next){
 if(spec.endsWith('/lib/public-auth-service'))return {shortCircuit:true,url:'data:text/javascript,export const guardPublicRequest=(...args)=>globalThis.__g2bRuntimeAuth.guard(...args);export const publicEnabled=()=>globalThis.__g2bRuntimeAuth.enabled();export const requireAdmin=(...args)=>globalThis.__g2bRuntimeAuth.admin(...args);'};
 return next(spec,ctx);
}});
const route=await import('../app/api/runtime-info/route.ts');
const request=()=>new Request('https://private.invalid/api/runtime-info?version=0.0.0');
const reset=()=>{block=null;enabled=false;adminError=null;guardError=null;adminChecks=0;};

test('runtime endpoint returns its packaged version with no-store metadata and no inferred deployed commit',async()=>{
 reset();
 const response=await route.GET(request()),body=await response.json();
 assert.equal(response.status,200);
 assert.equal(response.headers.get('cache-control'),'no-store');
 assert.equal(response.headers.get('x-content-type-options'),'nosniff');
 assert.equal(body.appVersion,packageInfo.version);
 assert.equal(body.deployedCommitSha,null);
 assert.ok(Math.abs(Date.now()-Date.parse(body.checkedAt))<10000);
 assert.equal(adminChecks,0);
});

test('runtime endpoint honors the normal guard before returning any application metadata',async()=>{
 reset();block=Response.json({error:'MEMBER_LOGIN_REQUIRED'},{status:401});
 const response=await route.GET(request());
 assert.equal(response,block);
 assert.deepEqual(await response.json(),{error:'MEMBER_LOGIN_REQUIRED'});
 assert.equal(adminChecks,0);
});

test('public mode runtime metadata is restricted to approved administrators',async()=>{
 reset();enabled=true;adminError=Error('ADMIN_REQUIRED');
 let response=await route.GET(request());
 assert.equal(response.status,403);
 assert.deepEqual(await response.json(),{error:'ADMIN_REQUIRED'});
 assert.equal(adminChecks,1);
 adminError=null;
 response=await route.GET(request());
 assert.equal(response.status,200);
 assert.equal((await response.json()).appVersion,packageInfo.version);
});

test('runtime endpoint fails closed and does not expose thrown authentication or storage details',async()=>{
 reset();guardError=Error('private-token=secret');
 let response=await route.GET(request());
 assert.equal(response.status,503);
 assert.deepEqual(await response.json(),{error:'RUNTIME_INFO_UNAVAILABLE'});
 reset();enabled=true;adminError=Error('database=private');
 response=await route.GET(request());
 assert.equal(response.status,503);
 assert.deepEqual(await response.json(),{error:'RUNTIME_INFO_UNAVAILABLE'});
});
