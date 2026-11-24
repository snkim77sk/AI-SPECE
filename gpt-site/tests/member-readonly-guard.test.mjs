import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync,existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {registerHooks} from 'node:module';

const origin='https://member-guard.test';
const env={DB:null,G2B_PUBLIC_AUTH_ENABLED:'enabled',G2B_PUBLIC_AUTH_ORIGIN:origin,
 G2B_PUBLIC_SESSION_PEPPER:'p'.repeat(64),G2B_PUBLIC_SERVICE_SYNC_TOKEN:'s'.repeat(64)};
globalThis.__g2bMemberGuardEnv=env;
registerHooks({resolve(spec,ctx,next){
 if(spec==='cloudflare:workers')return {url:'data:text/javascript,export const env=globalThis.__g2bMemberGuardEnv;',shortCircuit:true};
 if(spec.startsWith('.')&&!/\.(ts|mjs|js)$/.test(spec)&&ctx.parentURL?.startsWith('file:')){
  const candidate=new URL(spec+'.ts',ctx.parentURL);if(existsSync(fileURLToPath(candidate)))return next(candidate.href,ctx);
 }
 return next(spec,ctx);
}});
const auth=await import('../lib/public-auth-service.ts');
const paths=['collection','budget','matching','institution-patterns','future-ranking'];
const handlers=await Promise.all(paths.map(path=>import('../app/api/'+path+'/route.ts')));
function setup(){
 const db=new DatabaseSync(':memory:');db.exec(readFileSync(new URL('../drizzle/0023_public_identity.sql',import.meta.url),'utf8'));
 env.G2B_PUBLIC_AUTH_ENABLED='enabled';
 env.DB={prepare(sql){let args=[];const q={bind(...values){args=values;return q;},async first(){return db.prepare(sql).get(...args)||null;},async all(){return {results:db.prepare(sql).all(...args)};},async run(){return q._run();},_run(){return {meta:{changes:Number(db.prepare(sql).run(...args).changes)}};}};return q;},async batch(items){db.exec('BEGIN');try{const output=items.map(item=>item._run());db.exec('COMMIT');return output;}catch(error){db.exec('ROLLBACK');throw error;}}};
 return db;
}
const request=(path,cookie='',method='GET',body)=>new Request(origin+path,{method,headers:{Cookie:cookie,Origin:origin,'Content-Type':'application/json','X-Role':'admin'},...(body?{body:JSON.stringify(body)}:{})});
const changes=db=>db.prepare('SELECT total_changes() n').get().n;
async function session(db,role='member',status='approved'){
 const member=await auth.registerIdentity('naver','subject-'+role+status,'사용자','');
 db.prepare('UPDATE public_members SET status=?,role=? WHERE id=?').run(status,role,member.id);
 return auth.sessionCookie(await auth.createSession(member.id)).split(';')[0];
}

test('real route POST handlers reject member mutations before source storage or providers are touched',async t=>{
 const db=setup();let providerCalls=0;
 t.mock.method(globalThis,'fetch',async()=>{providerCalls++;throw Error('PROVIDER_MUST_NOT_BE_CALLED');});
 try{
  const cookie=await session(db),before=changes(db);
  for(let i=0;i<paths.length;i++){
   const response=await handlers[i].POST(request('/api/'+paths[i],cookie,'POST',{action:'create',mode:'live',role:'admin'}));
   assert.equal(response.status,403,paths[i]);assert.equal((await response.json()).error,'READ_ONLY_ACCESS');
  }
  assert.equal(changes(db),before);assert.equal(providerCalls,0);
 }finally{db.close();env.DB=null;}
});

test('private preview denies management without querying a membership database or changing owner privileges',async()=>{
 env.G2B_PUBLIC_AUTH_ENABLED='disabled';env.DB={prepare(){throw Error('NO_MEMBERSHIP_READ_EXPECTED');}};
 try{
  assert.equal(await auth.guardPublicRequest(request('/api/deliveries?view=history&viewer=member')),null);
  for(const path of ['/api/collection','/api/budget?action=workspace','/api/budget?action=history-plan','/api/runtime-info','/api/release-readiness','/api/new-management']){
   const url=path+(path.includes('?')?'&':'?')+'viewer=member';
   assert.equal((await auth.guardPublicRequest(request(url))).status,403,url);
  }
  for(const path of paths)assert.equal((await auth.guardPublicRequest(request('/api/'+path+'?viewer=member','','POST',{action:'create'}))).status,403,path);
  assert.equal(await auth.guardPublicRequest(request('/api/collection')),null);
  const blocked=await auth.guardPublicRequest(request('/settings?viewer=member'));
  assert.equal(blocked.status,303);assert.equal(blocked.headers.get('location'),origin+'/dashboard?viewer=member');
  assert.equal(auth.readOnlyDataView(request('/api/dashboard?viewer=member')),true);
  assert.equal(auth.readOnlyDataView(request('/api/dashboard')),false);
 }finally{env.DB=null;env.G2B_PUBLIC_AUTH_ENABLED='enabled';}
});

test('preview cannot grant a guest or pending account access and only demotes a real administrator',async()=>{
 const db=setup();
 try{
  assert.equal((await auth.guardPublicRequest(request('/api/deliveries?viewer=member'))).status,401);
  const pending=await session(db,'member','pending');
  assert.equal((await auth.guardPublicRequest(request('/api/deliveries?viewer=member',pending))).status,403);
  const admin=await session(db,'admin'),before=changes(db);
  assert.equal(await auth.guardPublicRequest(request('/api/collection',admin,'POST',{action:'create'})),null);
  assert.equal((await auth.guardPublicRequest(request('/api/collection?viewer=member',admin,'POST',{action:'create'}))).status,403);
  assert.equal(await auth.guardPublicRequest(request('/api/deliveries?viewer=member',admin)),null);
  assert.equal(changes(db),before);
  const service=new Request(origin+'/api/server-status',{method:'POST',headers:{'X-G2B-Service-Token':'s'.repeat(64)}});
  assert.equal(await auth.guardPublicRequest(service),null);
  assert.equal((await auth.guardPublicRequest(new Request(origin+'/api/server-status?viewer=member',{method:'POST',headers:service.headers}))).status,401);
 }finally{db.close();env.DB=null;}
});
