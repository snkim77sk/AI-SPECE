import test from 'node:test';
import assert from 'node:assert/strict';
import {fetchSignInAvailability,fetchReleaseReadiness,parseSignInAvailability,parseReleaseReadiness,readinessErrorMessage} from '../lib/public-readiness-response.ts';

const stamp='2026-11-25T01:00:00.000Z';
const signin=()=>({schema:'g2b-signin-availability-v1',checkedAt:stamp,state:'available',providers:[{id:'kakao',available:false},{id:'naver',available:false},{id:'google',available:true}]});
const owner=()=>({schema:'g2b-release-readiness-v1',checkedAt:stamp,enabled:true,
 configuration:{httpsOrigin:true,sessionSecret:true,serviceSyncToken:true},identityStorage:{schema:'ready',approvedAdminPresent:false},
 providers:['kakao','naver','google'].map(id=>({id,configured:true,loginAvailable:true,realLogin:'unverified'})),
 approvalFlow:'unverified',windowsServiceCompatibility:'unverified',publicRelease:'unverified'});

test('readiness parsers reject missing, contradictory or falsely verified responses',()=>{
 for(const value of [null,{authenticated:false,member:null},{...signin(),providers:[signin().providers[0],signin().providers[0],signin().providers[2]]},{...signin(),state:'disabled'}])assert.throws(()=>parseSignInAvailability(value),/INVALID_RESPONSE/);
 for(const value of [{...owner(),publicRelease:'verified'},{...owner(),approvalFlow:true},{...owner(),identityStorage:{schema:'unknown',approvedAdminPresent:false}},{...owner(),configuration:{...owner().configuration,sessionSecret:false}}])assert.throws(()=>parseReleaseReadiness(value),/INVALID_RESPONSE/);
 assert.equal(parseReleaseReadiness(owner()).providers.every(provider=>provider.loginAvailable),true,'no administrator is required for the first sign-in');
 assert.deepEqual(parseSignInAvailability({...signin(),privateToken:'SECRET'}),signin());
});

test('readiness fetch uses separate endpoints, no-store and same-origin credentials',async()=>{
 const calls=[];const fetcher=async(url,options)=>{calls.push({url,options});return Response.json(url==='/api/release-readiness'?owner():signin());};
 await fetchSignInAvailability(fetcher,new AbortController().signal);
 await fetchReleaseReadiness(fetcher,new AbortController().signal);
 assert.deepEqual(calls.map(call=>call.url),['/api/public-auth/readiness','/api/release-readiness']);
 assert.ok(calls.every(call=>call.options.cache==='no-store'&&call.options.credentials==='same-origin'));
});

test('request and response-body deadlines remain errors rather than disabled state',async()=>{
 let inner;
 await assert.rejects(fetchSignInAvailability((_url,options)=>{inner=options.signal;return new Promise(()=>{});},new AbortController().signal,10),/READINESS_TIMEOUT/);
 assert.equal(inner.aborted,true);
 await assert.rejects(fetchReleaseReadiness(async()=>({ok:true,json:()=>new Promise(()=>{})}),new AbortController().signal,10),/READINESS_TIMEOUT/);
 for(const status of [401,403,503])await assert.rejects(fetchReleaseReadiness(async()=>Response.json({secret:'NOT_FOR_UI'},{status}),new AbortController().signal),status===503?/READINESS_UNAVAILABLE/:/READINESS_ACCESS_REQUIRED/);
 await assert.rejects(fetchSignInAvailability(async()=>new Response('<html>sign in</html>'),new AbortController().signal));
 assert.doesNotMatch(readinessErrorMessage(Error('secret=TOKEN')),/TOKEN/);
});

test('cancelled reads do not fetch or deliver late successful readiness',async()=>{
 const before=new AbortController();before.abort();
 await assert.rejects(fetchSignInAvailability(()=>{throw Error('MUST_NOT_FETCH');},before.signal),{name:'AbortError'});
 const active=new AbortController();let resolve;
 const waiting=fetchReleaseReadiness(()=>new Promise(done=>{resolve=done;}),active.signal);
 active.abort();await assert.rejects(waiting,{name:'AbortError'});resolve(Response.json(owner()));
 assert.equal((await fetchReleaseReadiness(async()=>Response.json(owner()),new AbortController().signal)).identityStorage.approvedAdminPresent,false);
});
