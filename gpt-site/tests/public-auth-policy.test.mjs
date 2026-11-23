import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {accessDecision,configuredOrigin,cookieValue,oauthCookie,publicAuthReady,stateValid,SESSION_COOKIE,PROVIDERS} from '../lib/public-auth-policy.mjs';
import {oauthStart,verifyState,clearStateCookie} from '../lib/public-oauth.mjs';

const approved={status:'approved',role:'member'};
const admin={status:'approved',role:'admin'};
const pending={status:'pending',role:'member'};
test('owner private remains the default and origin must be explicit trusted HTTPS',()=>{
 assert.equal(publicAuthReady({}),false);
 assert.equal(publicAuthReady({G2B_PUBLIC_AUTH_ENABLED:'enabled',G2B_PUBLIC_AUTH_ORIGIN:'http://localhost:3000'}),false);
 assert.equal(publicAuthReady({G2B_PUBLIC_AUTH_ENABLED:'enabled',G2B_PUBLIC_AUTH_ORIGIN:'https://app.example.kr'}),true);
 for(const origin of ['https://evil.com@site.example.kr','https://a.example.kr/x','javascript:alert(1)','https://a.example.kr/?x=1'])assert.equal(configuredOrigin(origin),null);
});
test('all data are denied to pending, suspended, rejected or guests',()=>{
 for(const principal of [null,pending,{status:'rejected',role:'member'},{status:'suspended',role:'member'}]){
  for(const path of ['/dashboard','/api/deliveries','/api/budget','/api/collection','/api/server-sync','/api/future-ranking','/contract-review']){
   assert.notEqual(accessDecision(path,'GET',principal,false),'allow',path);
  }
 }
});
test('approved members see stored data but cannot manage ingestion or administration',()=>{
 for(const p of ['/dashboard','/contract-review','/api/deliveries','/api/budget','/api/matching'])assert.equal(accessDecision(p,'GET',approved),'allow',p);
 for(const p of ['/api/server-status','/api/server-sync','/api/automation','/api/collection','/api/collection/status','/api/maintenance/synthetic','/api/trial','/api/public-auth/admin','/admin/members','/settings','/storage-inspection','/api/storage-inspection']){
  assert.equal(accessDecision(p,'GET',approved),'forbidden',p);
 }
 for(const p of ['/api/budget','/api/matching','/api/deliveries','/api/future-ranking'])assert.equal(accessDecision(p,'POST',approved),'forbidden');
 assert.equal(accessDecision('/api/public-auth/admin','POST',pending),'forbidden');
 for(const p of ['/api/public-auth/admin','/admin/members','/api/collection','/api/server-sync'])assert.equal(accessDecision(p,'POST',admin),'allow');
});
test('service credential is allowed ONLY for Windows receipt/status transport',()=>{
 for(const p of ['/api/server-sync','/api/server-status'])assert.equal(accessDecision(p,'POST',null,true),'service');
 for(const p of ['/api/collection','/api/budget','/api/automation'])assert.notEqual(accessDecision(p,'POST',null,true),'allow');
});
test('providers are three independent OAuth subjects, with Daum assigned Kakao',()=>{
 assert.deepEqual(Object.keys(PROVIDERS),['kakao','naver','google']);
 assert.match(PROVIDERS.kakao.label,/다음/);
 for(const p of Object.keys(PROVIDERS))assert.match(oauthCookie(p),/^__Host-g2b_oauth_/);
 assert.equal(cookieValue(SESSION_COOKIE+'=abcdef; something=1',SESSION_COOKIE),'abcdef');
 assert.equal(cookieValue('other=1',SESSION_COOKIE),null);
});
test('OAuth start is URL-bound, per-request random and state/time-scoped',async()=>{
 const settings={KAKAO_REST_API_KEY:'kakao-test',KAKAO_CLIENT_SECRET:'secret',NAVER_CLIENT_ID:'naver-test',NAVER_CLIENT_SECRET:'secret',GOOGLE_CLIENT_ID:'google-test',GOOGLE_CLIENT_SECRET:'secret'};
 for(const p of ['kakao','naver','google']){
  const auth=await oauthStart(p,'https://g2b.example.kr',settings,1770000000000);
  const url=new URL(auth.url),state=url.searchParams.get('state');
  assert.equal(url.searchParams.get('client_id'),settings[{kakao:'KAKAO_REST_API_KEY',naver:'NAVER_CLIENT_ID',google:'GOOGLE_CLIENT_ID'}[p]]);
  assert.ok(state.length>=32);
  const rawCookie=auth.cookie.split(';')[0];
  assert.equal(verifyState(p,state,rawCookie,1770000000000).length>=32,true);
  assert.throws(()=>verifyState(p,state.slice(1),rawCookie,1770000000000),/OAUTH_STATE_INVALID/);
  assert.throws(()=>verifyState(p,state,rawCookie,1770000000000+601000),/OAUTH_STATE_INVALID/);
  assert.match(clearStateCookie(p),/Max-Age=0/);
  assert.ok(!url.toString().includes('secret'));
  if(p==='google')assert.equal(url.searchParams.get('code_challenge_method'),'S256');
 }
 assert.throws(()=>oauthStart('daum','https://g2b.example.kr',settings),/INVALID_PROVIDER/);
});
test('new public auth routes are guarded, and deployment stays opt-in',()=>{
 const policy=readFileSync(new URL('../lib/public-auth-service.ts',import.meta.url),'utf8');
 const gate=readFileSync(new URL('../proxy.ts',import.meta.url),'utf8');
 const migration=readFileSync(new URL('../drizzle/0022_public_identity.sql',import.meta.url),'utf8');
 assert.match(policy,/G2B_PUBLIC_AUTH_ENABLED==='enabled'/);
 assert.match(gate,/guardPublicRequest/);
 assert.match(migration,/CREATE TABLE IF NOT EXISTS public_members/);
 assert.match(migration,/CREATE TABLE IF NOT EXISTS public_identities/);
 assert.match(migration,/CREATE TABLE IF NOT EXISTS public_sessions/);
 assert.doesNotMatch(migration,/DROP TABLE|DELETE FROM delivery_rows|UPDATE delivery_rows/i);
});
