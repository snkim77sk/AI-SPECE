import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync,readdirSync,existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {registerHooks} from 'node:module';

const env={DB:null,G2B_PUBLIC_AUTH_ENABLED:'enabled',G2B_PUBLIC_AUTH_ORIGIN:'https://g2b.example.kr',G2B_PUBLIC_SESSION_PEPPER:'a'.repeat(64),G2B_PUBLIC_SERVICE_SYNC_TOKEN:'b'.repeat(64)};
globalThis.__g2bPublicAuthEnv=env;
registerHooks({resolve(spec,ctx,next){
 if(spec==='cloudflare:workers')return{url:'data:text/javascript,export const env=globalThis.__g2bPublicAuthEnv',shortCircuit:true};
 if(spec.startsWith('.')&&!/\.(ts|mjs|js)$/.test(spec)&&ctx.parentURL?.startsWith('file:')){
  const u=new URL(spec+'.ts',ctx.parentURL);
  if(existsSync(fileURLToPath(u)))return next(u.href,ctx);
 }
 return next(spec,ctx);
}});
const auth=await import('../lib/public-auth-service.ts');
function setup(){
 const db=new DatabaseSync(':memory:');db.exec('PRAGMA foreign_keys=ON');
 for(const name of readdirSync(new URL('../drizzle/',import.meta.url)).filter(s=>s.endsWith('.sql')).sort())db.exec(readFileSync(new URL('../drizzle/'+name,import.meta.url),'utf8'));
 env.DB={prepare(sql){let args=[];const q={bind(...values){args=values;return q;},async first(){return db.prepare(sql).get(...args)||null;},async all(){return {results:db.prepare(sql).all(...args)};},async run(){return q._run();},_run(){return{meta:{changes:Number(db.prepare(sql).run(...args).changes)}};}};return q;},async batch(statements){db.exec('BEGIN');try{const results=statements.map(q=>q._run());db.exec('COMMIT');return results;}catch(e){db.exec('ROLLBACK');throw e;}}};
 return db;
}
const request=(path,cookie='',method='GET',headers={})=>new Request('https://g2b.example.kr'+path,{method,headers:{...cookie?{Cookie:cookie}:{},...headers}});
test('first social sign-in is pending; approval and suspension apply immediately to existing cookie',async()=>{
 const db=setup();
 try{
  const kakao=await auth.registerIdentity('kakao','kakao-user-1','회원','same@sample.kr');
  assert.equal(kakao.status,'pending');assert.equal(kakao.role,'member');
  const google=await auth.registerIdentity('google','google-user-1','회원','same@sample.kr');
  assert.notEqual(kakao.id,google.id,'same email must never join providers');
  const again=await auth.registerIdentity('kakao','kakao-user-1','회원','same@sample.kr');
  assert.equal(again.id,kakao.id);
  const token=await auth.createSession(kakao.id),cookie=auth.sessionCookie(token).split(';')[0];
  assert.equal((await auth.guardPublicRequest(request('/api/deliveries',cookie))).status,403);
  db.prepare("UPDATE public_members SET status='approved' WHERE id=?").run(kakao.id);
  assert.equal(await auth.guardPublicRequest(request('/api/deliveries',cookie)),null);
  assert.equal((await auth.guardPublicRequest(request('/api/collection',cookie,'POST'))).status,403);
  db.prepare("UPDATE public_members SET status='suspended' WHERE id=?").run(kakao.id);
  assert.equal((await auth.guardPublicRequest(request('/api/deliveries',cookie))).status,403);
  await auth.revokeSession(request('/api/public-auth/logout',cookie));
  assert.equal((await auth.guardPublicRequest(request('/api/deliveries',cookie))).status,401);
 }finally{db.close();env.DB=null;}
});
test('an approved owner explicitly marked admin may access collection; service token is route scoped',async()=>{
 const db=setup();
 try{
  const owner=await auth.registerIdentity('naver','owner-1','관리자','owner@sample.kr');
  db.prepare("UPDATE public_members SET status='approved',role='admin' WHERE id=?").run(owner.id);
  const cookie=auth.sessionCookie(await auth.createSession(owner.id)).split(';')[0];
  assert.equal(await auth.guardPublicRequest(request('/api/automation',cookie,'POST')),null);
  const serviceHeaders={'X-G2B-Service-Token':'b'.repeat(64)};
  assert.equal(await auth.guardPublicRequest(request('/api/server-sync','','POST',serviceHeaders)),null);
  assert.equal((await auth.guardPublicRequest(request('/api/budget','','POST',serviceHeaders))).status,401);
  assert.equal((await auth.guardPublicRequest(request('/api/budget','','GET'))).status,401);
  assert.equal(await auth.guardPublicRequest(request('/api/budget?action=workspace',cookie)),null);
 }finally{db.close();env.DB=null;}
});
test('missing configuration fails closed, disabled flag preserves original owner-private control',async()=>{
 const db=setup();
 try{
  env.G2B_PUBLIC_AUTH_ENABLED='disabled';
  assert.equal(await auth.guardPublicRequest(request('/api/deliveries')),null);
  env.G2B_PUBLIC_AUTH_ENABLED='enabled';env.G2B_PUBLIC_SERVICE_SYNC_TOKEN='';
  assert.equal((await auth.guardPublicRequest(request('/api/deliveries'))).status,503);
 }finally{env.G2B_PUBLIC_SERVICE_SYNC_TOKEN='b'.repeat(64);env.G2B_PUBLIC_AUTH_ENABLED='enabled';db.close();env.DB=null;}
});
