import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {existsSync,readFileSync,readdirSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {registerHooks} from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
import {liveOnlyRequest} from '../lib/live-only.mjs';

const origin='https://g2b.example.test';
const env={DB:null,G2B_PUBLIC_AUTH_ENABLED:'enabled',G2B_PUBLIC_AUTH_ORIGIN:origin,
 G2B_PUBLIC_SESSION_PEPPER:'a'.repeat(64),G2B_PUBLIC_SERVICE_SYNC_TOKEN:'b'.repeat(64)};
globalThis.__g2bLogoutWorkerEnv=env;
registerHooks({resolve(spec,ctx,next){
 if(spec==='cloudflare:workers')return {url:'data:text/javascript,export const env=globalThis.__g2bLogoutWorkerEnv',shortCircuit:true};
 if(spec.startsWith('.')&&!/\.(ts|mjs|js)$/.test(spec)&&ctx.parentURL?.startsWith('file:')){
  const candidate=new URL(spec+'.ts',ctx.parentURL);if(existsSync(fileURLToPath(candidate)))return next(candidate.href,ctx);
 }
 return next(spec,ctx);
}});
const auth=await import('../lib/public-auth-service.ts');
const logout=await import('../app/api/public-auth/logout/route.ts');
const connector=await import('../lib/connector-context.ts');

function setup(){
 const db=new DatabaseSync(':memory:');db.exec('PRAGMA foreign_keys=ON');
 for(const name of readdirSync(new URL('../drizzle/',import.meta.url)).filter(name=>name.endsWith('.sql')).sort())db.exec(readFileSync(new URL('../drizzle/'+name,import.meta.url),'utf8'));
 env.DB={prepare(sql){let values=[];const statement={bind(...next){values=next;return statement;},async first(){return db.prepare(sql).get(...values)||null;},async all(){return {results:db.prepare(sql).all(...values)};},async run(){return statement.runSync();},runSync(){return {meta:{changes:Number(db.prepare(sql).run(...values).changes)}};}};return statement;},async batch(statements){db.exec('BEGIN');try{const result=statements.map(statement=>statement.runSync());db.exec('COMMIT');return result;}catch(error){db.exec('ROLLBACK');throw error;}}};
 return db;
}
function snapshotExceptSessions(db){
 const names=db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='public_sessions' ORDER BY name").all().map(row=>row.name);
 return JSON.stringify(names.map(name=>[name,db.prepare('SELECT * FROM "'+name.replaceAll('"','""')+'"').all()]));
}
function workerHost(trace){
 // Compile the real worker with the same production DEV substitution. Only
 // the framework dispatcher is replaced; both guards and logout are real.
 const source=readFileSync(new URL('../worker/index.ts',import.meta.url),'utf8').replaceAll('import.meta.env.DEV','false');
 const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const imports={
  'vinext/server/fetch-handler':{default:{fetch(request){trace.push('logout');assert.equal(new URL(request.url).pathname,'/api/public-auth/logout');return logout.POST(request);}}},
  '../lib/connector-context':connector,
  '../lib/public-auth-service':{async guardPublicRequest(request){trace.push('auth');return auth.guardPublicRequest(request);}},
  '../lib/live-only.mjs':{async liveOnlyRequest(request,db){trace.push('live-only');return liveOnlyRequest(request,db);}},
 };
 const exports={};vm.runInNewContext(code,{exports,Response,require:name=>{assert.ok(name in imports,'unexpected worker import '+name);return imports[name];}});
 return exports.default;
}
const formRequest=(cookie,body,requestOrigin=origin)=>new Request(origin+'/api/public-auth/logout',{method:'POST',headers:{Origin:requestOrigin,Cookie:cookie,'Content-Type':'application/x-www-form-urlencoded'},...(body===undefined?{}:{body})});

for(const [description,body] of [['no body',undefined],['empty HTML form','']]){
 test('production worker accepts logout '+description+' and revokes only the current session',async()=>{
  const db=setup(),trace=[];
  try{
   const member=await auth.registerIdentity('kakao','logout-worker-user','테스트 회원','fixture@example.test');
   const cookie=auth.sessionCookie(await auth.createSession(member.id)).split(';')[0];
   const otherCookie=auth.sessionCookie(await auth.createSession(member.id)).split(';')[0];
   const before=snapshotExceptSessions(db);
   const response=await workerHost(trace).fetch(formRequest(cookie,body),env,{});
   assert.deepEqual(trace,['auth','live-only','logout']);assert.equal(response.status,303);
   assert.equal(response.headers.get('location'),origin+'/login');
   assert.match(response.headers.get('set-cookie'),/^__Host-g2b_public_session=;.*Max-Age=0/);
   assert.equal(await auth.memberForRequest(new Request(origin+'/api/deliveries',{headers:{Cookie:cookie}})),null);
   assert.equal((await auth.memberForRequest(new Request(origin+'/api/deliveries',{headers:{Cookie:otherCookie}}))).id,member.id);
   assert.equal(db.prepare('SELECT COUNT(*) AS n FROM public_sessions').get().n,1);
   assert.equal(snapshotExceptSessions(db),before,'all source data, identities, members, and other tables stay unchanged');
  }finally{db.close();env.DB=null;}
 });
}

test('logout body exception preserves the real route Origin check and its session on rejection',async()=>{
 const db=setup(),trace=[];
 try{
  const member=await auth.registerIdentity('naver','logout-origin-user','테스트 회원','fixture@example.test');
  const cookie=auth.sessionCookie(await auth.createSession(member.id)).split(';')[0];
  const before=snapshotExceptSessions(db);
  const response=await workerHost(trace).fetch(formRequest(cookie,'','https://other.example.test'),env,{});
  assert.deepEqual(trace,['auth','live-only','logout']);assert.equal(response.status,403);
  assert.equal((await response.json()).error,'ORIGIN_NOT_ALLOWED');assert.equal(response.headers.get('set-cookie'),null);
  assert.equal((await auth.memberForRequest(new Request(origin+'/api/deliveries',{headers:{Cookie:cookie}}))).id,member.id);
  assert.equal(snapshotExceptSessions(db),before);
 }finally{db.close();env.DB=null;}
});

test('logout exception does not relax other auth routes, source mutations, or synthetic query blocking',async()=>{
 for(const path of ['/api/public-auth/admin','/api/public-auth/start/kakao','/api/public-auth/logout/extra','/api/collection']){
  const response=await liveOnlyRequest(new Request(origin+path,{method:'POST',body:'',headers:{'Content-Type':'application/x-www-form-urlencoded'}}),null);
  assert.equal(response.status,400,path);assert.equal((await response.json()).error,'INVALID_REQUEST');
 }
 const synthetic=await liveOnlyRequest(new Request(origin+'/api/public-auth/logout?mode=synthetic',{method:'POST'}),null);
 assert.equal(synthetic.status,410);
});
