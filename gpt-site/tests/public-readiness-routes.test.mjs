import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync,existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {registerHooks} from 'node:module';

const origin='https://private-readiness.test';
const env={DB:null,G2B_PUBLIC_AUTH_ENABLED:'enabled',G2B_PUBLIC_AUTH_ORIGIN:origin,
 G2B_PUBLIC_SESSION_PEPPER:'P'.repeat(64),G2B_PUBLIC_SERVICE_SYNC_TOKEN:'S'.repeat(64),
 KAKAO_REST_API_KEY:'test-kakao',KAKAO_CLIENT_SECRET:'test-kakao-secret',
 NAVER_CLIENT_ID:'test-naver',NAVER_CLIENT_SECRET:'test-naver-secret',
 GOOGLE_CLIENT_ID:'test-google',GOOGLE_CLIENT_SECRET:'test-google-secret'};
globalThis.__g2bReadinessRouteEnv=env;
registerHooks({resolve(spec,ctx,next){
 if(spec==='cloudflare:workers')return {url:'data:text/javascript,export const env=globalThis.__g2bReadinessRouteEnv;',shortCircuit:true};
 if(spec.startsWith('.')&&!/\.(ts|mjs|js)$/.test(spec)&&ctx.parentURL?.startsWith('file:')){
  const url=new URL(spec+'.ts',ctx.parentURL);if(existsSync(fileURLToPath(url)))return next(url.href,ctx);
 }
 return next(spec,ctx);
}});
const ownerRoute=await import('../app/api/release-readiness/route.ts');
const visitorRoute=await import('../app/api/public-auth/readiness/route.ts');
const startRoute=await import('../app/api/public-auth/start/[provider]/route.ts');
const auth=await import('../lib/public-auth-service.ts');
const {accessDecision}=await import('../lib/public-auth-policy.mjs');
function setup(migrate=true){
 const db=new DatabaseSync(':memory:');if(migrate)db.exec(readFileSync(new URL('../drizzle/0023_public_identity.sql',import.meta.url),'utf8'));
 env.DB={prepare(sql){let params=[];const q={bind(...args){params=args;return q;},async all(){return {results:db.prepare(sql).all(...params)};},async first(){return db.prepare(sql).get(...params)||null;},async run(){return q._run();},_run(){return {meta:{changes:Number(db.prepare(sql).run(...params).changes)}};}};return q;},async batch(statements){db.exec('BEGIN');try{const result=statements.map(q=>q._run());db.exec('COMMIT');return result;}catch(error){db.exec('ROLLBACK');throw error;}}};
 env.G2B_PUBLIC_AUTH_ENABLED='enabled';
 return db;
}
const req=(path,cookie='',headers={})=>new Request(origin+path,{headers:{...(cookie?{Cookie:cookie}:{}),...headers}});
const changes=db=>db.prepare('SELECT total_changes() n').get().n;

test('owner-private readiness remains readable with authentication disabled and changes no data',async()=>{
 const db=setup();env.G2B_PUBLIC_AUTH_ENABLED='disabled';
 try{
  const before=changes(db),response=await ownerRoute.GET(req('/api/release-readiness')),body=await response.json();
  assert.equal(response.status,200);assert.equal(response.headers.get('cache-control'),'no-store');
  assert.equal(body.enabled,false);assert.deepEqual(body.identityStorage,{schema:'ready',approvedAdminPresent:false});
  assert.ok(body.providers.every(provider=>provider.loginAvailable===false&&provider.realLogin==='unverified'));
  assert.equal(changes(db),before);
 }finally{env.DB=null;db.close();}
});

test('public owner readiness independently rejects guests, ordinary members and service tokens',async()=>{
 const db=setup();
 try{
  assert.equal((await ownerRoute.GET(req('/api/release-readiness'))).status,401);
  assert.equal((await ownerRoute.GET(req('/api/release-readiness','',{'X-G2B-Service-Token':'S'.repeat(64)}))).status,401);
  const member=await auth.registerIdentity('naver','member','PRIVATE_NAME','private@example.test');
  const cookie=auth.sessionCookie(await auth.createSession(member.id)).split(';')[0];
  assert.equal((await ownerRoute.GET(req('/api/release-readiness',cookie))).status,403);
  db.prepare("UPDATE public_members SET status='approved' WHERE id=?").run(member.id);
  assert.equal((await ownerRoute.GET(req('/api/release-readiness',cookie))).status,403);
  db.prepare("UPDATE public_members SET role='admin' WHERE id=?").run(member.id);
  const before=changes(db),response=await ownerRoute.GET(req('/api/release-readiness',cookie)),body=await response.json();
  assert.equal(response.status,200);assert.equal(body.identityStorage.approvedAdminPresent,true);
  const output=JSON.stringify(body);
  for(const value of [member.id,'PRIVATE_NAME','private@example.test',env.G2B_PUBLIC_SESSION_PEPPER,'test-kakao-secret'])assert.equal(output.includes(value),false);
  assert.equal(changes(db),before);
 }finally{env.DB=null;db.close();}
});

test('anonymous availability discloses no administrator or schema evidence and permits the first sign-in',async()=>{
 const db=setup();
 try{
  const before=changes(db),response=await visitorRoute.GET(req('/api/public-auth/readiness')),body=await response.json();
  assert.equal(response.status,200);assert.equal(body.state,'available');
  assert.deepEqual(Object.keys(body).sort(),['checkedAt','providers','schema','state']);
  assert.ok(body.providers.every(provider=>provider.available));
  const initial=await startRoute.GET(req('/api/public-auth/start/google'),{params:Promise.resolve({provider:'google'})});
  assert.equal(initial.status,302);assert.match(initial.headers.get('location'),/^https:\/\/accounts\.google\.com\//);
  assert.equal(db.prepare('SELECT COUNT(*) n FROM public_members').get().n,0,'start must not register or bootstrap a member');
  assert.equal(changes(db),before);
  assert.equal(accessDecision('/api/public-auth/readiness','GET',null),'allow');
  assert.notEqual(accessDecision('/api/public-auth/readiness','POST',null),'allow');
  assert.notEqual(accessDecision('/api/public-auth/readiness/more','GET',null),'allow');
 }finally{env.DB=null;db.close();}
});

test('missing storage stops direct OAuth starts and visitor buttons without creating tables',async()=>{
 const db=setup(false);
 try{
  const response=await visitorRoute.GET(req('/api/public-auth/readiness')),body=await response.json();
  assert.equal(body.state,'setup-required');assert.ok(body.providers.every(provider=>provider.available===false));
  const initial=await startRoute.GET(req('/api/public-auth/start/google'),{params:Promise.resolve({provider:'google'})});
  assert.equal(initial.status,503);assert.equal(initial.headers.get('location'),null);assert.equal(initial.headers.get('set-cookie'),null);
  assert.equal(db.prepare("SELECT COUNT(*) n FROM sqlite_master WHERE type='table'").get().n,0);
 }finally{env.DB=null;db.close();}
});

test('visitor readiness storage errors are retryable failures without internal messages',async()=>{
 env.G2B_PUBLIC_AUTH_ENABLED='enabled';env.DB={prepare(){throw Error('PRIVATE_DSN_AND_TOKEN');}};
 try{
  const response=await visitorRoute.GET(req('/api/public-auth/readiness')),body=await response.json();
  assert.equal(response.status,503);assert.equal(body.state,'unavailable');
  assert.equal(JSON.stringify(body).includes('PRIVATE_DSN_AND_TOKEN'),false);
  assert.ok(body.providers.every(provider=>provider.available===false));
 }finally{env.DB=null;}
});
