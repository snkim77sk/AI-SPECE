import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';
import {baseAuthConfiguration} from '../lib/public-auth-config.mjs';
import {inspectIdentityStorage,signInAvailability,releaseReadiness} from '../lib/public-release-readiness.mjs';

const configured={G2B_PUBLIC_AUTH_ENABLED:'enabled',G2B_PUBLIC_AUTH_ORIGIN:'https://private-readiness.test',
 G2B_PUBLIC_SESSION_PEPPER:'P'.repeat(64),G2B_PUBLIC_SERVICE_SYNC_TOKEN:'S'.repeat(64),
 KAKAO_REST_API_KEY:'test-kakao-id',KAKAO_CLIENT_SECRET:'test-kakao-secret',
 NAVER_CLIENT_ID:'test-naver-id',NAVER_CLIENT_SECRET:'test-naver-secret',
 GOOGLE_CLIENT_ID:'test-google-id',GOOGLE_CLIENT_SECRET:'test-google-secret'};
const migration=readFileSync(new URL('../drizzle/0023_public_identity.sql',import.meta.url),'utf8');
function fixture(migrate=true){
 const db=new DatabaseSync(':memory:');if(migrate)db.exec(migration);
 const calls=[];
 const adapter={prepare(sql){
  calls.push(sql);assert.match(sql,/^SELECT\s/i,'readiness must issue SELECT only');
  return {async all(){return {results:db.prepare(sql).all()};},async first(){return db.prepare(sql).get()||null;}};
 }};
 const add=(id,status='pending',role='member')=>db.prepare("INSERT INTO public_members(id,display_name,email,status,role,created_at,updated_at) VALUES(?,?,'private@example.test',?,?,?,?)").run(id,'PRIVATE_MEMBER_'+id,status,role,'2026-11-25','2026-11-25');
 return {db,adapter,calls,add,changes:()=>db.prepare('SELECT total_changes() n').get().n};
}

test('absent tables are missing without creating them; query failures are unknown',async()=>{
 const f=fixture(false);
 try{
  assert.deepEqual(await inspectIdentityStorage(f.adapter,{includeAdmin:true}),{schema:'missing',approvedAdminPresent:null});
  assert.equal(f.db.prepare("SELECT COUNT(*) n FROM sqlite_master WHERE type='table'").get().n,0);
  assert.equal(f.changes(),0);
  assert.deepEqual(await inspectIdentityStorage(()=>{throw Error('private-database-path');},{includeAdmin:true}),{schema:'unknown',approvedAdminPresent:null});
 }finally{f.db.close();}
});

test('malformed identity storage never passes readiness',async()=>{
 const f=fixture(false);
 try{
  f.db.exec('CREATE TABLE public_members(id TEXT); CREATE TABLE public_identities(id TEXT); CREATE TABLE public_sessions(id TEXT);');
  assert.deepEqual(await inspectIdentityStorage(f.adapter,{includeAdmin:true}),{schema:'unknown',approvedAdminPresent:null});
  assert.equal(f.changes(),0);
 }finally{f.db.close();}
});

test('approved administrator evidence excludes ordinary, pending and suspended accounts',async()=>{
 const f=fixture();
 try{
  f.add('ordinary','approved','member');f.add('waiting','pending','admin');f.add('suspended','suspended','admin');
  const before=f.changes();
  assert.deepEqual(await inspectIdentityStorage(f.adapter,{includeAdmin:true}),{schema:'ready',approvedAdminPresent:false});
  assert.equal(f.changes(),before);
  f.add('owner','approved','admin');
  const report=await releaseReadiness(configured,f.adapter);
  assert.equal(report.identityStorage.approvedAdminPresent,true);
  assert.equal(report.publicRelease,'unverified');assert.equal(report.approvalFlow,'unverified');
  assert.equal(report.windowsServiceCompatibility,'unverified');
  assert.ok(report.providers.every(provider=>provider.realLogin==='unverified'));
  assert.equal(f.changes(),before+1);
  const serialized=JSON.stringify(report);
  for(const value of [...Object.values(configured).filter(value=>value!=='enabled'),'PRIVATE_MEMBER','private@example.test'])assert.equal(serialized.includes(value),false,value);
 }finally{f.db.close();}
});

test('failed administrator query remains unknown even when storage columns were verified',async()=>{
 const f=fixture();
 try{
  const adapter={prepare(sql){if(sql.startsWith('SELECT EXISTS'))throw Error('SECRET_STORAGE_ERROR');return f.adapter.prepare(sql);}};
  assert.deepEqual(await inspectIdentityStorage(adapter,{includeAdmin:true}),{schema:'ready',approvedAdminPresent:null});
 }finally{f.db.close();}
});

test('login availability is provider-specific, excludes whitespace secrets and needs no administrator',async()=>{
 const f=fixture();
 try{
  const googleOnly={...configured,KAKAO_CLIENT_SECRET:'',NAVER_CLIENT_ID:'   '};
  const result=await signInAvailability(googleOnly,f.adapter);
  assert.equal(result.state,'available');
  assert.deepEqual(result.providers,[{id:'kakao',available:false},{id:'naver',available:false},{id:'google',available:true}]);
  assert.equal(f.calls.some(sql=>sql.startsWith('SELECT EXISTS')),false,'anonymous route must not inspect administrator presence');
  assert.deepEqual(Object.keys(result).sort(),['checkedAt','providers','schema','state']);
  assert.equal((await releaseReadiness(googleOnly,f.adapter)).identityStorage.approvedAdminPresent,false);
  assert.equal((await signInAvailability({...googleOnly,G2B_PUBLIC_SESSION_PEPPER:' '.repeat(64)},f.adapter)).state,'setup-required');
  assert.equal(baseAuthConfiguration({...configured,G2B_PUBLIC_SERVICE_SYNC_TOKEN:' '.repeat(64)}).configured,false);
  assert.equal(f.changes(),0);
 }finally{f.db.close();}
});

test('disabled service skips storage; missing and unavailable storage never enables providers',async()=>{
 let calls=0;const unavailable=()=>{calls++;throw Error('PRIVATE');};
 assert.equal((await signInAvailability({...configured,G2B_PUBLIC_AUTH_ENABLED:'disabled'},unavailable)).state,'disabled');
 assert.equal(calls,0);
 const unknown=await signInAvailability(configured,unavailable);
 assert.equal(unknown.state,'unavailable');assert.ok(unknown.providers.every(provider=>provider.available===false));
 const f=fixture(false);
 try{
  const missing=await signInAvailability(configured,f.adapter);
  assert.equal(missing.state,'setup-required');assert.ok(missing.providers.every(provider=>provider.available===false));
 }finally{f.db.close();}
});
