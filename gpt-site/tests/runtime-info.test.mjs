import test from 'node:test';
import assert from 'node:assert/strict';
import {runtimeInfo,parseRuntimeInfo,fetchRuntimeInfo,runtimeErrorMessage,ownerProgressStages} from '../lib/runtime-info.mjs';
import {CODE_SNAPSHOT_SHA} from '../lib/owner-progress.mjs';
import {syncAvailability} from '../lib/sync-source-summary.mjs';

const stamp='2026-11-24T16:30:00.000Z';
const payload=()=>runtimeInfo('5.2.21',stamp);

test('runtime metadata distinguishes the executing app from the reviewed GitHub baseline',()=>{
 const value=payload();
 assert.equal(value.appVersion,'5.2.21');
 assert.equal(value.sourceBaseline.version,'5.2.20');
 assert.equal(value.sourceBaseline.commitSha,CODE_SNAPSHOT_SHA);
 assert.equal(value.deployedCommitSha,null);
 assert.equal(value.checkedAt,stamp);
 assert.deepEqual(Object.keys(value).sort(),['appVersion','checkedAt','deployedCommitSha','schema','sourceBaseline']);
 assert.throws(()=>runtimeInfo('token=do-not-expose',stamp),/RUNTIME_INFO_UNAVAILABLE/);
});

test('runtime response validation rejects protocol versions, invalid timestamps and inferred deployment SHAs',()=>{
 for(const value of [
  {version:'5.2.16',protocol:'windows-verified-pages-v1',sources:[]},
  {...payload(),appVersion:undefined,version:'5.2.16'},
  {...payload(),checkedAt:'not-a-time'},
  {...payload(),checkedAt:'2026-02-30T00:00:00.000Z'},
  {...payload(),sourceBaseline:{...payload().sourceBaseline,commitSha:'not-a-sha'}},
  {...payload(),deployedCommitSha:CODE_SNAPSHOT_SHA},
 ])assert.throws(()=>parseRuntimeInfo(value),/RUNTIME_INFO_INVALID_RESPONSE/);
 assert.deepEqual(parseRuntimeInfo({...payload(),environment:{secret:'private'}}),payload());
});

test('runtime fetch verifies only the dedicated no-store app response',async()=>{
 const calls=[];
 const result=await fetchRuntimeInfo(async(url,options)=>{calls.push({url,options});return Response.json(payload());},new AbortController().signal);
 assert.equal(result.appVersion,'5.2.21');
 assert.equal(calls[0].url,'/api/runtime-info');
 assert.equal(calls[0].options.cache,'no-store');
 assert.equal(calls[0].options.credentials,'same-origin');
 await assert.rejects(fetchRuntimeInfo(async()=>Response.json({version:'5.2.16',sources:[]}),new AbortController().signal),/RUNTIME_INFO_INVALID_RESPONSE/);
});

test('failed runtime responses do not become a known version and a later refresh can recover',async()=>{
 for(const code of [401,403,503]){
  await assert.rejects(fetchRuntimeInfo(async()=>Response.json({secret:'must-not-display'}, {status:code}),new AbortController().signal),code===503?/RUNTIME_INFO_UNAVAILABLE/:/RUNTIME_INFO_ACCESS_REQUIRED/);
 }
 await assert.rejects(fetchRuntimeInfo(async()=>new Response('<html>Login</html>'),new AbortController().signal));
 assert.equal((await fetchRuntimeInfo(async()=>Response.json(payload()),new AbortController().signal)).appVersion,'5.2.21');
 assert.doesNotMatch(runtimeErrorMessage(new Error('private-token=secret')),/private-token|secret/);
});

test('stalled runtime requests and body reads time out and cancellation stops obsolete results',async()=>{
 let innerSignal;
 await assert.rejects(fetchRuntimeInfo((_url,options)=>{innerSignal=options.signal;return new Promise(()=>{});},new AbortController().signal,10),/RUNTIME_INFO_TIMEOUT/);
 assert.equal(innerSignal.aborted,true);
 await assert.rejects(fetchRuntimeInfo(async()=>({ok:true,json:()=>new Promise(()=>{})}),new AbortController().signal,10),/RUNTIME_INFO_TIMEOUT/);
 const aborted=new AbortController();aborted.abort();
 await assert.rejects(fetchRuntimeInfo(()=>{throw Error('MUST_NOT_FETCH');},aborted.signal),{name:'AbortError'});
 const abort=new AbortController();let resolve;
 const pending=fetchRuntimeInfo(()=>new Promise(done=>{resolve=done;}),abort.signal);
 abort.abort();
 await assert.rejects(pending,{name:'AbortError'});
 resolve(Response.json(payload()));
});

test('runtime verification never substitutes for missing Windows receipt evidence',()=>{
 let stages=ownerProgressStages(payload(),syncAvailability(null));
 assert.equal(stages.find(stage=>stage.id==='deploy').status,'runtime-verified');
 assert.equal(stages.find(stage=>stage.id==='source').status,'unverified');
 assert.equal(stages.find(stage=>stage.id==='publish').status,'blocked');
 stages=ownerProgressStages(null,[]);
 assert.equal(stages.find(stage=>stage.id==='deploy').status,'unverified');
 const valid={dataset:'shopping_delivery',checkedDay:'2026-11-24',received:0,receivedAt:stamp};
 assert.equal(ownerProgressStages(null,[valid]).find(stage=>stage.id==='source').status,'receipt-partial');
 assert.equal(ownerProgressStages(null,[valid,valid]).find(stage=>stage.id==='source').status,'receipt-partial');
 assert.equal(ownerProgressStages(null,[valid,{...valid,dataset:'budget'}]).find(stage=>stage.id==='source').status,'receipt-verified');
 assert.equal(ownerProgressStages(null,[{...valid,received:null}]).find(stage=>stage.id==='source').status,'unverified');
});
