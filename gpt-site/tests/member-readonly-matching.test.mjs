import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync,readdirSync,existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {registerHooks} from 'node:module';
import {createHmac} from 'node:crypto';

const origin='https://g2b.example.kr';
const env={DB:null,G2B_PUBLIC_AUTH_ENABLED:'disabled',G2B_PUBLIC_AUTH_ORIGIN:origin,
 G2B_PUBLIC_SESSION_PEPPER:'a'.repeat(64),G2B_PUBLIC_SERVICE_SYNC_TOKEN:'b'.repeat(64)};
globalThis.__g2bReadonlyMatchingEnv=env;
registerHooks({resolve(spec,ctx,next){
 if(spec==='cloudflare:workers')return{url:'data:text/javascript,export const env=globalThis.__g2bReadonlyMatchingEnv',shortCircuit:true};
 if(spec.startsWith('.')&&!/\.(ts|mjs|js)$/.test(spec)&&ctx.parentURL?.startsWith('file:')){
  const u=new URL(spec+'.ts',ctx.parentURL);if(existsSync(fileURLToPath(u)))return next(u.href,ctx);
 }
 return next(spec,ctx);
}});
const service=await import('../lib/matching-service.ts');
const route=await import('../app/api/matching/route.ts');
const liveId='11111111-1111-4111-8111-111111111111';
const syntheticId='22222222-2222-4222-8222-222222222222';
const absentId='33333333-3333-4333-8333-333333333333';
const stamp='2026-11-24T00:00:00Z';
const dataFields=['id','mode','state','phase','engine_version','g2b_epoch','budget_epoch','from_day','to_day',
 'budget_count','request_count','candidate_count','match_count','excluded_count','created_at','updated_at','stale'];
const forbiddenFields=['lease','lease_until','cursor','partial','error','audit_internal'];
function insert(db,table,row){const fields=Object.keys(row);db.prepare(`INSERT INTO ${table}(${fields.join(',')}) VALUES(${fields.map(()=>'?')})`).run(...Object.values(row));}
function snapshot(db){return JSON.stringify(db.prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").all().map(({name})=>[name,db.prepare('SELECT * FROM "'+name.replaceAll('"','""')+'"').all()]));}
function setup(t,{publicMode=false,stale=false,rows=3}={}){
 const db=new DatabaseSync(':memory:');db.exec('PRAGMA foreign_keys=ON');
 for(const file of readdirSync(new URL('../drizzle/',import.meta.url)).filter(x=>x.endsWith('.sql')).sort())db.exec(readFileSync(new URL('../drizzle/'+file,import.meta.url),'utf8'));
 // New operational columns must not silently become member-facing fields.
 db.exec('ALTER TABLE matching_jobs ADD COLUMN audit_internal TEXT');
 for(const [id,mode] of [[liveId,'live'],[syntheticId,'synthetic']]){
  insert(db,'matching_jobs',{id,mode,state:'complete',phase:'DONE',engine_version:'v18-source-fields',g2b_epoch:0,budget_epoch:0,
   from_day:'2026-03-01',to_day:'2026-11-24',cursor:'{"checkpoint":"operator-only"}',partial:null,
   budget_count:7,request_count:rows,candidate_count:rows*2,match_count:rows,excluded_count:1,
   lease:'operator-lease-'+mode,lease_until:1795500000000,error:'operator-internal-note',audit_internal:'future-private-field',created_at:stamp,updated_at:stamp});
  for(let n=0;n<rows;n++){
   const key='R'+String(n).padStart(4,'0');
   insert(db,'matching_results',{job_id:id,request_key:key,budget_key:'B'+n,score:85,level:'HIGH',row_json:JSON.stringify({request_key:key,mode,evidence:['stored evidence'],score:85})});
  }
 }
 if(stale)db.prepare("UPDATE budget_epochs SET revision=1 WHERE mode='live'").run();
 const token='c'.repeat(64),memberId='fixture-approved-member';
 insert(db,'public_members',{id:memberId,display_name:'조회 회원',email:'',status:'approved',role:'member',created_at:stamp,updated_at:stamp});
 insert(db,'public_sessions',{token_hash:createHmac('sha256',env.G2B_PUBLIC_SESSION_PEPPER).update(token).digest('hex'),member_id:memberId,created_at:Date.now(),expires_at:Date.now()+60000});
 const before=snapshot(db),reads=[],trace={writeAttempts:0};
 db.exec('PRAGMA query_only=ON');
 env.G2B_PUBLIC_AUTH_ENABLED=publicMode?'enabled':'disabled';
 env.DB={prepare(sql){
  assert.match(sql,/^\s*(SELECT|WITH)\b/,'matching data reads must not execute DDL or writes');
  let args=[];const q={bind(...values){args=values;return q;},async first(){reads.push({sql,args});return db.prepare(sql).get(...args)||null;},async all(){reads.push({sql,args});return{results:db.prepare(sql).all(...args)};},async run(){trace.writeAttempts++;throw Error('MATCHING_READ_MUST_NOT_WRITE');}};return q;
 },async batch(){trace.writeAttempts++;throw Error('MATCHING_READ_MUST_NOT_WRITE');}};
 const originalFetch=globalThis.fetch;globalThis.fetch=()=>{throw Error('MATCHING_READ_MUST_NOT_CALL_PROVIDER');};
 t.after(()=>{try{assert.equal(trace.writeAttempts,0);assert.equal(snapshot(db),before,'all source rows, job checkpoints, leases and session data stay unchanged');}finally{env.DB=null;env.G2B_PUBLIC_AUTH_ENABLED='disabled';globalThis.fetch=originalFetch;db.close();}});
 return{db,reads,cookie:'__Host-g2b_public_session='+token};
}
const get=(params={},cookie='')=>route.GET(new Request(origin+'/api/matching?'+new URLSearchParams(params),{headers:cookie?{Cookie:cookie}:{}}));
function assertDataJob(job,{stale=false}={}){
 assert.equal(job.id,liveId);assert.equal(job.mode,'live');assert.equal(job.state,'complete');assert.equal(job.phase,'DONE');
 assert.equal(job.from_day,'2026-03-01');assert.equal(job.to_day,'2026-11-24');assert.equal(job.budget_count,7);
 assert.equal(job.excluded_count,1);assert.equal(job.created_at,stamp);assert.equal(job.updated_at,stamp);
 assert.ok(Object.keys(job).every(key=>dataFields.includes(key)));
 for(const key of forbiddenFields)assert.equal(Object.hasOwn(job,key),false,key+' must not leave the member view');
 if(Object.hasOwn(job,'stale'))assert.equal(job.stale,stale);
}

test('required mode constrains lists and explicit IDs before any foreign-mode result read',async t=>{
 const{reads}=setup(t);
 const list=await service.matchingWorkspace(null,true,'live');assert.deepEqual(list.jobs.map(j=>j.id),[liveId]);
 const missing=await service.matchingWorkspace(syntheticId,true,'live');assert.equal(missing.job,null);assert.deepEqual(missing.jobs.map(j=>j.mode),['live']);
 for(const mode of ['live','synthetic'])await assert.rejects(service.matchingResults(new URLSearchParams({id:syntheticId,mode}),'live'),/MATCH_JOB_NOT_FOUND/);
 assert.equal(reads.some(({sql})=>/FROM matching_results\b/.test(sql)),false);
 assert.ok(reads.filter(({sql})=>sql.includes('matching_jobs WHERE id=')).every(({sql,args})=>sql.includes('AND mode=?')&&args[1]==='live'));
 await assert.rejects(service.matchingWorkspace(liveId,true,'invalid'),/INVALID_MODE/);
});

test('unconstrained direct service calls preserve offline synthetic analysis reads',async t=>{
 setup(t);
 const workspace=await service.matchingWorkspace(syntheticId);assert.equal(workspace.job.mode,'synthetic');assert.equal(workspace.job.lease,'operator-lease-synthetic');
 const result=await service.matchingResults(new URLSearchParams({id:syntheticId}));assert.equal(result.job.mode,'synthetic');assert.equal(result.rows.length,3);assert.ok(result.rows.every(row=>row.mode==='synthetic'));
});

test('GET always selects live data despite query mode or a direct synthetic job ID',async t=>{
 setup(t);
 for(const mode of ['synthetic','live','invalid']){
  const response=await get({mode});assert.equal(response.status,200);assert.equal(response.headers.get('Cache-Control'),'no-store');
  const body=await response.json();assert.deepEqual(body.jobs.map(job=>job.mode),['live']);assert.equal(body.job.id,liveId);
 }
 const workspace=await get({id:syntheticId,mode:'live'});assert.equal(workspace.status,200);assert.equal((await workspace.json()).job,null);
 for(const id of [syntheticId,absentId]){
  const response=await get({action:'results',id,mode:'live'});assert.equal(response.status,404);assert.deepEqual(await response.json(),{error:'MATCH_JOB_NOT_FOUND'});
 }
 const response=await get({action:'results',id:liveId,mode:'synthetic'});assert.equal(response.status,200);const body=await response.json();assert.ok(body.rows.every(row=>row.mode==='live'));
 assert.equal(body.job.lease,'operator-lease-live','owner-private operational view remains available');
 const required=await get({action:'results'});assert.equal(required.status,400);assert.deepEqual(await required.json(),{error:'MATCH_JOB_REQUIRED'});
});

test('member preview projects workspace and result jobs without changing exact data or pagination',async t=>{
 setup(t,{rows:101,stale:true});
 const workspace=await get({id:liveId,viewer:'member'});assert.equal(workspace.status,200);const body=await workspace.json();
 assertDataJob(body.job,{stale:true});for(const job of body.jobs)assertDataJob(job);assert.ok(body.window.from&&body.window.to);
 const first=await get({action:'results',id:liveId,viewer:'member'});assert.equal(first.status,200);const page=await first.json();
 assertDataJob(page.job,{stale:true});assert.equal(page.provisional,true);assert.equal(page.rows.length,100);assert.equal(page.next,'R0099');
 assert.deepEqual(page.rows[0],{request_key:'R0000',mode:'live',evidence:['stored evidence'],score:85});
 const next=await get({action:'results',id:liveId,after:page.next,viewer:'member'});const tail=await next.json();
 assert.deepEqual(tail.rows.map(row=>row.request_key),['R0100']);assert.equal(tail.next,null);assertDataJob(tail.job,{stale:true});
});

test('authenticated approved member gets projected live reads while anonymous and mutation calls fail closed',async t=>{
 const{cookie}=setup(t,{publicMode:true});
 const anonymous=await get({action:'results',id:liveId});assert.equal(anonymous.status,401);
 const workspace=await get({id:liveId},cookie);assert.equal(workspace.status,200);const body=await workspace.json();assertDataJob(body.job);for(const job of body.jobs)assertDataJob(job);
 const result=await get({action:'results',id:liveId},cookie);assert.equal(result.status,200);const data=await result.json();assertDataJob(data.job);assert.equal(data.provisional,false);assert.equal(data.rows.length,3);
 const crossMode=await get({action:'results',id:syntheticId},cookie);assert.equal(crossMode.status,404);assert.deepEqual(await crossMode.json(),{error:'MATCH_JOB_NOT_FOUND'});
 const mutation=await route.POST(new Request(origin+'/api/matching',{method:'POST',headers:{Cookie:cookie,Origin:origin,'Content-Type':'application/json'},body:JSON.stringify({action:'step',id:liveId})}));assert.equal(mutation.status,403);
});
