import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync,readdirSync,existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {registerHooks} from 'node:module';
import {generateKeyPairSync,sign} from 'node:crypto';

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
const start=await import('../app/api/public-auth/start/[provider]/route.ts');
const callback=await import('../app/api/public-auth/callback/[provider]/route.ts');
const adminRoute=await import('../app/api/public-auth/admin/route.ts');
const deliveriesRoute=await import('../app/api/deliveries/route.ts');
const dashboardRoute=await import('../app/api/dashboard/route.ts');
function setup(){
 const db=new DatabaseSync(':memory:');db.exec('PRAGMA foreign_keys=ON');
 for(const name of readdirSync(new URL('../drizzle/',import.meta.url)).filter(s=>s.endsWith('.sql')).sort())db.exec(readFileSync(new URL('../drizzle/'+name,import.meta.url),'utf8'));
 env.DB={prepare(sql){let args=[];const q={bind(...values){args=values;return q;},async first(){return db.prepare(sql).get(...args)||null;},async all(){return {results:db.prepare(sql).all(...args)};},async run(){return q._run();},_run(){return{meta:{changes:Number(db.prepare(sql).run(...args).changes)}};}};return q;},async batch(statements){db.exec('BEGIN');try{const results=statements.map(q=>q._run());db.exec('COMMIT');return results;}catch(e){db.exec('ROLLBACK');throw e;}}};
 return db;
}
const request=(path,cookie='',method='GET',headers={})=>new Request('https://g2b.example.kr'+path,{method,headers:{...(cookie?{Cookie:cookie}:{}),...headers}});
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
  await assert.rejects(auth.createSession(kakao.id),/MEMBER_SESSION_NOT_ALLOWED/);
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

test('provider callbacks enroll pending members and admin approval controls data access',async t=>{
 const db=setup(),origin='https://g2b.example.kr';
 const clients={KAKAO_REST_API_KEY:'mock-kakao',KAKAO_CLIENT_SECRET:'mock-secret',NAVER_CLIENT_ID:'mock-naver',
  NAVER_CLIENT_SECRET:'mock-secret',GOOGLE_CLIENT_ID:'mock-google',GOOGLE_CLIENT_SECRET:'mock-secret'};
 Object.assign(env,clients);
 const {privateKey,publicKey}=generateKeyPairSync('rsa',{modulusLength:2048});
 const key={...publicKey.export({format:'jwk'}),kid:'test-key',alg:'RS256',use:'sig'};
 const owner=await auth.registerIdentity('naver','mock-admin','관리자','admin@example.test');
 db.prepare("UPDATE public_members SET status='approved',role='admin' WHERE id=?").run(owner.id);
 const ownerCookie=auth.sessionCookie(await auth.createSession(owner.id)).split(';')[0];
 let nonce,providerId,codeVerifier;
 const mockedFetch=t.mock.method(globalThis,'fetch',async (value,options)=>{
  const url=new URL(value);
  assert.equal(options.redirect,'error');
  if(url.hostname==='www.googleapis.com')return Response.json({keys:[key]});
  if(url.pathname.endsWith('/token')){
   const form=new URLSearchParams(options.body);
   assert.match(form.get('redirect_uri'),new RegExp('^'+origin+'/api/public-auth/callback/'));
   if(url.hostname==='oauth2.googleapis.com'){
    assert.equal(form.get('code_verifier'),codeVerifier);
    const now=Math.floor(Date.now()/1000);
    const payload={iss:'https://accounts.google.com',aud:'mock-google',sub:providerId,nonce,
     iat:now,exp:now+300,name:'Mock Google',email:'same@example.test',email_verified:true};
    const input=[{alg:'RS256',kid:'test-key'},payload].map(value=>Buffer.from(JSON.stringify(value)).toString('base64url')).join('.');
    const id_token=input+'.'+sign('RSA-SHA256',Buffer.from(input),privateKey).toString('base64url');
    return Response.json({access_token:'mock-google-access',id_token});
   }
   return Response.json({access_token:'mock-provider-access'});
  }
  assert.equal(options.headers.Authorization,'Bearer mock-provider-access');
  if(url.hostname==='kapi.kakao.com')return Response.json({id:Number(providerId),properties:{nickname:'Mock Kakao'},kakao_account:{is_email_verified:true,email:'same@example.test'}});
  if(url.hostname==='openapi.naver.com')return Response.json({resultcode:'00',response:{id:providerId,name:'Mock NAVER',email:'same@example.test'}});
  throw Error('Unexpected provider endpoint');
 });
 try{
  const ids=new Set();
  for(const [provider,subject] of [['kakao','201'],['naver','naver-202'],['kakao','203'],['google','google-204']]){
   providerId=subject;
   const ctx={params:Promise.resolve({provider})};
   const initial=await start.GET(request('/api/public-auth/start/'+provider),ctx);
   assert.equal(initial.status,302);
   const url=new URL(initial.headers.get('Location'));
   const state=url.searchParams.get('state');
   const stateCookie=initial.headers.get('Set-Cookie').split(';')[0];
   nonce=url.searchParams.get('nonce');
   codeVerifier=stateCookie.split('.')[1];
   const path='/api/public-auth/callback/'+provider+'?code=mock-authorization-code&state='+state;
   const callsBefore=mockedFetch.mock.callCount();
   const declined=await callback.GET(request(path+'&error=access_denied',stateCookie),ctx);
   assert.equal(new URL(declined.headers.get('Location')).pathname,'/login');
   assert.equal(mockedFetch.mock.callCount(),callsBefore);
   const response=await callback.GET(request(path,stateCookie),ctx);
   assert.equal(response.status,303);
   assert.equal(new URL(response.headers.get('Location')).pathname,'/account/pending');
   const cookie=response.headers.getSetCookie().find(item=>item.startsWith('__Host-g2b_public_session=')).split(';')[0];
   const member=await auth.memberForRequest(request('/contract-review',cookie));
   assert.equal(member.status,'pending');
   assert.equal((await auth.guardPublicRequest(request('/api/budget',cookie))).status,403);
   ids.add(member.id);
   const manage=status=>adminRoute.POST(new Request(origin+'/api/public-auth/admin',{method:'POST',
    headers:{Cookie:ownerCookie,Origin:origin,'Content-Type':'application/json'},body:JSON.stringify({id:member.id,status})}));
   assert.equal((await manage('approved')).status,200);
   assert.equal(await auth.guardPublicRequest(request('/api/deliveries',cookie)),null);
   assert.equal((await auth.guardPublicRequest(request('/api/collection',cookie))).status,403);
   if(subject==='201'){
    const delivery=await deliveriesRoute.GET(request('/api/deliveries',cookie));
    assert.equal(delivery.status,200);
    assert.deepEqual(Object.keys((await delivery.json()).window).sort(),['from','retentionCutoff','to']);
    const dashboard=await dashboardRoute.GET(request('/api/dashboard',cookie));
    assert.equal(dashboard.status,200);
    assert.equal(Object.hasOwn(await dashboard.json(),'readiness'),false);
   }
   assert.equal((await manage('suspended')).status,200);
   assert.equal((await auth.guardPublicRequest(request('/api/deliveries',cookie))).status,401);
   await assert.rejects(auth.createSession(member.id),/MEMBER_SESSION_NOT_ALLOWED/);
   assert.equal((await manage('rejected')).status,200);
   assert.equal((await auth.guardPublicRequest(request('/api/budget',cookie))).status,401);
   assert.equal((await manage('approved')).status,200);
   assert.equal((await auth.guardPublicRequest(request('/api/deliveries',cookie))).status,401,
    'Reapproval must never reactivate an old suspended or rejected session');
   const newCookie=auth.sessionCookie(await auth.createSession(member.id)).split(';')[0];
   assert.equal(await auth.guardPublicRequest(request('/api/deliveries',newCookie)),null,
    'Reapproved members can sign in again with a new session');
  }
  assert.equal(ids.size,4);
  const list=await adminRoute.GET(request('/api/public-auth/admin',ownerCookie));
  assert.equal((await list.json()).members.length,4);
 }finally{
  for(const name of Object.keys(clients))delete env[name];
  db.close();env.DB=null;
 }
});
