import test from 'node:test';
import assert from 'node:assert/strict';
import {registerHooks} from 'node:module';
let calls=[];globalThis.__generationRouteTestService={read:async(...args)=>{calls.push(['read',...args]);return {canPromote:false};},create:async()=>{throw Error('SECRET');},run:async()=>{throw Error('SECRET');},control:async()=>{throw Error('SECRET');}};
registerHooks({resolve(spec,ctx,next){if(spec.endsWith('/lib/g2b-generation-service'))return {shortCircuit:true,url:'data:text/javascript,export const recoveryGeneration=()=>globalThis.__generationRouteTestService;'};if(spec.endsWith('/lib/public-auth-service'))return {shortCircuit:true,url:'data:text/javascript,export const guardPublicRequest=async()=>null;'};return next(spec,ctx);}});
const route=await import('../app/api/collection/generation-recovery/route.ts');
const url='https://private.invalid/api/collection/generation-recovery';
test('generation route rejects cross-origin/type/oversize/malformed/unrequested action without dispatch',async()=>{
 for(const [headers,body,status] of [[{'content-type':'application/json',origin:'https://other.invalid'},'{}',403],[{},'{}',415],[{'content-type':'application/json'},'{',409],[{'content-type':'application/json'},JSON.stringify({action:'promote'}),409],[{'content-type':'application/json'},JSON.stringify({padding:'x'.repeat(2049)}),409]]){const r=await route.POST(new Request(url,{method:'POST',headers,body}));assert.equal(r.status,status);assert.ok(!(await r.text()).includes('SECRET'));}assert.equal(calls.length,0);
});
test('generation read is no-store and mutations never expose thrown secrets',async()=>{
 const r=await route.GET(new Request(url+'?id=G&runId=R'));assert.equal(r.status,200);assert.equal(r.headers.get('cache-control'),'no-store');assert.deepEqual(calls,[['read','G','R']]);
 for(const action of ['create-staging','run-staging','paused','cancelled']){const r=await route.POST(new Request(url,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({action})}));assert.equal(r.status,409);assert.equal((await r.json()).error,'GENERATION_FAILED_READ_SAME_ID');}
});
