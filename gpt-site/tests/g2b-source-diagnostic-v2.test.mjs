import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync,readdirSync,existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {registerHooks} from 'node:module';
import {mixedFixture} from '../lib/g2b-domain.mjs';
const env={};globalThis.__g2bProbeEnv=env;
registerHooks({resolve(spec,ctx,next){if(spec==='cloudflare:workers')return {url:'data:text/javascript,export const env=globalThis.__g2bProbeEnv',shortCircuit:true};if(spec.startsWith('.')&&!/\.(ts|mjs|js)$/.test(spec)&&ctx.parentURL?.startsWith('file:')){const u=new URL(spec+'.ts',ctx.parentURL);if(existsSync(fileURLToPath(u)))return next(u.href,ctx);}return next(spec,ctx);}});
const svc=await import('../lib/g2b-service.ts'),oldProbe=await import('../lib/g2b-source-probe.ts'),probe=await import('../lib/g2b-source-diagnostic-v2.ts');
const realFetch=globalThis.fetch;
let calls=0,change=null,fault=null,total=11988,beforeRead=null;
const tables=['collection_sessions','collection_receipts','day_checkpoints','seen_source_keys','delivery_rows','dataset_epochs','collection_source_probes'];
const saved=db=>tables.map(t=>JSON.stringify(db.prepare('SELECT * FROM '+t+' ORDER BY rowid').all()));
function setup(){
 const db=new DatabaseSync(':memory:');db.exec('PRAGMA foreign_keys=ON');for(const f of readdirSync(new URL('../drizzle/',import.meta.url)).filter(f=>f.endsWith('.sql')).sort())db.exec(readFileSync(new URL('../drizzle/'+f,import.meta.url),'utf8'));
 env.DB={prepare(sql){let args=[];const q={bind(...v){args=v;return q;},async first(){if(beforeRead)beforeRead(sql);return db.prepare(sql).get(...args)||null;},async all(){return {results:db.prepare(sql).all(...args)};},async run(){if(fault==='result'&&sql.includes("UPDATE collection_source_diagnostics_v2 SET status='complete'"))throw Error('SECRET result persistence failure');const r=q._run();if(fault==='result-ack'&&sql.includes("UPDATE collection_source_diagnostics_v2 SET status='complete'"))throw Error('SECRET lost result ACK');return r;},_run(){const r=db.prepare(sql).run(...args);return {meta:{changes:Number(r.changes)}};}};return q;},async batch(qs){db.exec('BEGIN');try{const result=qs.map((q,i)=>{if(fault==='reserve'&&i===1)throw Error('SECRET reservation failure');return q._run();});db.exec('COMMIT');if(fault==='ack')throw Error('SECRET lost acknowledgement');return result;}catch(e){if(db.isTransaction)db.exec('ROLLBACK');throw e;}}};
 Object.assign(env,{G2B_SERVICE_KEY:'OFFLINE_FAKE_DO_NOT_TRANSMIT',G2B_LIVE_ENABLED:'approved',G2B_QUOTA_SCOPE_REVIEWED:'allocation-confirmed',G2B_DAILY_BUDGET:'950',G2B_AUTOCOLLECT_ENABLED:'enabled',G2B_DIAGNOSTIC_PROBE_ENABLED:'approved-once'});
 calls=0;change=null;fault=null;total=11988;beforeRead=null;globalThis.fetch=async(url,opts)=>{calls++;assert.equal(new URL(url).origin,'https://apis.data.go.kr');if(opts?.redirect)assert.equal(opts.redirect,'manual');const u=new URL(url),n=Number(u.searchParams.get('pageNo')),raw=u.searchParams.get('inqryBgnDate'),day=raw.slice(0,4)+'-'+raw.slice(4,6)+'-'+raw.slice(6,8);const data=mixedFixture(day,n,999,total);if(change)change(data,n);return Response.json(data);};return db;
}
function done(db){db.close();globalThis.fetch=realFetch;}
async function failed(db,pages=0){const c=svc.config(),id=(await svc.createSession('live',c.to,c.to)).session.id;for(let n=0;n<pages;n++)await svc.sessionStep(id);db.prepare("UPDATE collection_sessions SET state='error',error='STORAGE_OR_SOURCE_FAILED',updated_at=? WHERE id=?").run(new Date().toISOString(),id);return id;}
const n=(db,table)=>Number(db.prepare('SELECT COUNT(*) n FROM '+table).get().n);
const usage=db=>Number(db.prepare('SELECT COALESCE(SUM(calls),0) n FROM usage').get().n);

function authorize(id,sessionId,priorProbeId){Object.assign(env,{G2B_DIAGNOSTIC_V2_ENABLED:'approved-once',G2B_DIAGNOSTIC_V2_REQUEST_ID:id,G2B_DIAGNOSTIC_V2_SESSION_ID:sessionId,G2B_DIAGNOSTIC_V2_PRIOR_PROBE_ID:priorProbeId});}
async function eligible(db,pages=0){
 const sessionId=await failed(db,pages),priorProbeId=crypto.randomUUID(),id=crypto.randomUUID(),mock=globalThis.fetch;
 globalThis.fetch=async()=>{throw new TypeError('SECRET old network failure');};
 const old=await oldProbe.sourceProbe(priorProbeId,sessionId);assert.equal(old.result.code,'SOURCE_NETWORK_OR_TIMEOUT');
 globalThis.fetch=mock;calls=0;authorize(id,sessionId,priorProbeId);
 return {id,sessionId,priorProbeId,run:()=>probe.sourceDiagnosticV2(id,sessionId,priorProbeId)};
}
test('v2 success preserves collection and original audit latch, and never resumes collection',async()=>{
 const db=setup();try{const x=await eligible(db,10),before=saved(db),used=usage(db);const r=await x.run();
 assert.equal(r.result.code,'SOURCE_VALIDATION_PASSED_STORAGE_NOT_TESTED');assert.equal(r.page,11);assert.equal(r.result.sourceRows,999);assert.equal(r.result.sourceRowsWritten,0);assert.equal(r.result.collectionSnapshotUnchanged,true);assert.equal(r.result.automaticRetryAllowed,false);assert.equal(r.collectionResumed,false);assert.deepEqual(saved(db),before);assert.equal(usage(db),used+1);assert.equal(calls,1);assert.equal(n(db,'collection_source_diagnostics_v2'),1);
 assert.deepEqual(await x.run(),r);assert.deepEqual(await probe.readSourceDiagnosticV2(x.id),r);const second=crypto.randomUUID();authorize(second,x.sessionId,x.priorProbeId);assert.deepEqual(await probe.sourceDiagnosticV2(second,x.sessionId,x.priorProbeId),r);assert.equal(calls,1);assert.equal((await svc.automaticTick()).performed,false);await assert.rejects(svc.sessionStep(x.sessionId),/REVIEW_REQUIRED/);
 for(const secret of ['OFFLINE_FAKE','SYNTHETIC-','attempt_owner','serviceKey'])assert.ok(!JSON.stringify(r).includes(secret));
 }finally{done(db);}
});
test('each exact gate and readiness setting is required before any write or fetch',async()=>{
 for(const property of ['G2B_DIAGNOSTIC_V2_ENABLED','G2B_DIAGNOSTIC_V2_REQUEST_ID','G2B_DIAGNOSTIC_V2_SESSION_ID','G2B_DIAGNOSTIC_V2_PRIOR_PROBE_ID','G2B_QUOTA_SCOPE_REVIEWED','G2B_SERVICE_KEY','G2B_LIVE_ENABLED']){const db=setup();try{const x=await eligible(db);env[property]='';const before=db.prepare('SELECT total_changes() n').get().n;await assert.rejects(x.run());assert.equal(db.prepare('SELECT total_changes() n').get().n,before);assert.equal(calls,0);}finally{done(db);}}
});
test('paused, cancelled, running, wrong error, busy lease and inconsistent metadata fail closed',async()=>{
 for(const sql of ["state='paused'","state='cancelled'","state='running'","error='SOURCE_PAGE_OVERLAP'","lease='other',lease_until=9999999999999","fetched=1"]){const db=setup();try{const x=await eligible(db);db.prepare('UPDATE collection_sessions SET '+sql+' WHERE id=?').run(x.sessionId);await assert.rejects(x.run());assert.equal(calls,0);assert.equal(n(db,'collection_source_diagnostics_v2'),0);}finally{done(db);}}
});
test('prior probe must match completed exact snapshot and all six eligibility result fields',async()=>{
 const changes=[['code','SOURCE_VALIDATION_PASSED_STORAGE_NOT_TESTED'],['phase','parse'],['collectionSnapshotUnchanged',false],['sourceRowsWritten',1],['collectionResumed',true],['automaticRetryAllowed',true]];
 for(const change of [...changes,['status','reserved'],['completed_at',null],['failed_at','wrong'],['session_id',crypto.randomUUID()],['result_json','invalid']]){const db=setup();try{const x=await eligible(db);const [key,value]=change;
 if(['status','completed_at','failed_at','result_json'].includes(key))db.prepare('UPDATE collection_source_probes SET '+key+'=? WHERE id=?').run(value,x.priorProbeId);
 else if(key==='session_id'){authorize(x.id,x.sessionId,value);await assert.rejects(probe.sourceDiagnosticV2(x.id,x.sessionId,value));continue;}
 else {const r=JSON.parse(db.prepare('SELECT result_json FROM collection_source_probes WHERE id=?').get(x.priorProbeId).result_json);r[key]=value;db.prepare('UPDATE collection_source_probes SET result_json=? WHERE id=?').run(JSON.stringify(r),x.priorProbeId);}
 await assert.rejects(x.run());assert.equal(calls,0);assert.equal(n(db,'collection_source_diagnostics_v2'),0);
 }finally{done(db);}}
});
test('950 daily cap cannot be exceeded even when configured higher',async()=>{
 const db=setup();try{const x=await eligible(db);env.G2B_DAILY_BUDGET='999999';db.prepare('UPDATE usage SET calls=950').run();await assert.rejects(x.run());assert.equal(calls,0);assert.equal(usage(db),950);assert.equal(n(db,'collection_source_diagnostics_v2'),0);}finally{done(db);}
});
test('concurrent same request reserves exactly one attempt',async()=>{
 const db=setup();try{const x=await eligible(db),used=usage(db);await Promise.all([x.run(),x.run()]);assert.equal(calls,1);assert.equal(usage(db),used+1);assert.equal(n(db,'collection_source_diagnostics_v2'),1);}finally{done(db);}
});
test('concurrent distinct IDs and changed gate cannot evade permanent snapshot/prior fence',async()=>{
 const db=setup();try{const x=await eligible(db),used=usage(db),other=crypto.randomUUID();const a=x.run();authorize(other,x.sessionId,x.priorProbeId);await Promise.allSettled([a,probe.sourceDiagnosticV2(other,x.sessionId,x.priorProbeId)]);assert.ok(calls<=1);assert.equal(usage(db),used+1);assert.equal(n(db,'collection_source_diagnostics_v2'),1);
 authorize(x.id,x.sessionId,x.priorProbeId);await x.run();assert.ok(calls<=1);
 }finally{done(db);}
});
test('reservation rollback, lost reservation ACK and result ACK never dispatch a repeat',async()=>{
 for(const kind of ['reserve','ack','result','result-ack']){const db=setup();try{const x=await eligible(db),used=usage(db);fault=kind;await assert.rejects(x.run(),/OUTCOME_UNCERTAIN/);fault=null;
 assert.equal(calls,kind.startsWith('result')?1:0);assert.equal(usage(db),used+(kind==='reserve'?0:1));
 if(kind!=='reserve'){const r=await x.run();assert.equal(r.status,kind==='result-ack'?'complete':'reserved');const other=crypto.randomUUID();authorize(other,x.sessionId,x.priorProbeId);await probe.sourceDiagnosticV2(other,x.sessionId,x.priorProbeId);assert.equal(calls,kind.startsWith('result')?1:0);}
 }finally{done(db);}}
});
test('redirect statuses are reported once without following Location or exposing it',async()=>{
 for(const status of [301,302,303,307,308]){const db=setup();try{const x=await eligible(db),before=saved(db);globalThis.fetch=async(url,opts)=>{calls++;assert.equal(opts.redirect,'manual');return new Response(null,{status,headers:{Location:'https://SECRET.invalid/?serviceKey=SECRET'}});};const r=await x.run();assert.equal(r.result.code,'SOURCE_REDIRECT');assert.equal(r.result.httpStatus,status);assert.equal(calls,1);assert.ok(!JSON.stringify(r).includes('SECRET'));assert.deepEqual(saved(db),before);}finally{done(db);}}
});
test('TypeError and hostile thrown values are not misclassified as observed timeouts or leaked',async()=>{
 const hostile=new Proxy({}, {get(){throw Error('SECRET hostile getter');}});
 for(const error of [new TypeError('SECRET fetch failed'),Object.assign(new Error('SECRET'),{code:'ETIMEDOUT'}),hostile,'SECRET',null]){const db=setup();try{const x=await eligible(db);globalThis.fetch=async()=>{calls++;throw error;};const r=await x.run();assert.equal(r.result.code,'SOURCE_FETCH_FAILED');assert.equal(r.result.timeoutObserved,false);assert.ok(!JSON.stringify(r).includes('SECRET'));assert.equal(calls,1);}finally{done(db);}}
});
test('actual timeout has its own observed flag and aborts the single request',async()=>{
 const db=setup(),timer=globalThis.setTimeout;try{const x=await eligible(db);let signal;globalThis.setTimeout=(fn,ms,...args)=>timer(fn,Math.min(ms,5),...args);globalThis.fetch=async(url,opts)=>{calls++;signal=opts.signal;return new Promise(()=>{});};const r=await x.run();assert.equal(r.result.code,'DIAGNOSTIC_TIMEOUT');assert.equal(r.result.timeoutObserved,true);assert.equal(signal.aborted,true);assert.equal(calls,1);}finally{globalThis.setTimeout=timer;done(db);}
});
test('HTTP failure, invalid JSON and oversized body remain bounded and sanitized',async()=>{
 for(const kind of ['http','json','size']){const db=setup();try{const x=await eligible(db),before=saved(db);globalThis.fetch=async()=>{calls++;return kind==='http'?new Response('SECRET',{status:503}):kind==='json'?new Response('SECRET invalid JSON'):new Response('x'.repeat(9*1024*1024));};const r=await x.run();assert.notEqual(r.result.code,'SOURCE_VALIDATION_PASSED_STORAGE_NOT_TESTED');assert.ok(!JSON.stringify(r).includes('SECRET'));assert.ok(JSON.stringify(r).length<2000);assert.equal(calls,1);assert.deepEqual(saved(db),before);}finally{done(db);}}
});
test('cross-page overlap is diagnosed without mutation',async()=>{
 const db=setup();try{const x=await eligible(db,1),before=saved(db);change=raw=>{raw.response.body.items[0]=mixedFixture(svc.config().to,1,999,total).response.body.items[0];};const r=await x.run();assert.equal(r.result.code,'SOURCE_PAGE_OVERLAP');assert.equal(r.result.phase,'overlap-check');assert.deepEqual(saved(db),before);}finally{done(db);}
});
test('KST midnight after reservation burns latch without dispatching on yesterday quota',async()=>{
 const RealDate=Date;let now=RealDate.parse('2026-11-08T14:59:59.000Z');globalThis.Date=class extends RealDate{constructor(...args){super(...(args.length?args:[now]));}static now(){return now;}};
 const db=setup();try{const x=await eligible(db),used=usage(db);beforeRead=sql=>{if(sql.startsWith('SELECT s.id FROM collection_sessions s WHERE '))now=RealDate.parse('2026-11-08T15:00:01.000Z');};const r=await x.run();assert.equal(r.result.code,'DIAGNOSTIC_QUOTA_DAY_CHANGED');assert.equal(calls,0);assert.equal(usage(db),used+1);assert.equal(db.prepare('SELECT day FROM usage').get().day,'2026-11-08');await x.run();assert.equal(calls,0);}finally{done(db);globalThis.Date=RealDate;}
});
test('cancellation during request is retained and reported',async()=>{
 const db=setup();try{const x=await eligible(db);change=()=>db.prepare("UPDATE collection_sessions SET state='cancelled' WHERE id=?").run(x.sessionId);const r=await x.run();assert.equal(r.result.collectionSnapshotUnchanged,false);assert.equal(db.prepare('SELECT state FROM collection_sessions WHERE id=?').get(x.sessionId).state,'cancelled');assert.equal(calls,1);}finally{done(db);}
});
test('stalled response body is covered by same observed timeout',async()=>{
 const db=setup(),timer=globalThis.setTimeout;try{const x=await eligible(db);globalThis.setTimeout=(fn,ms,...args)=>timer(fn,Math.min(ms,5),...args);globalThis.fetch=async()=>{calls++;return new Response(new ReadableStream({start(){}}));};const r=await x.run();assert.equal(r.result.code,'DIAGNOSTIC_TIMEOUT');assert.equal(r.result.phase,'body');assert.equal(r.result.timeoutObserved,true);assert.equal(calls,1);}finally{globalThis.setTimeout=timer;done(db);}
});
test('route rejects cross-origin, wrong type, oversized JSON, malformed JSON and unsupported action before dispatch',async()=>{
 const route=await import('../app/api/collection/source-diagnostic-v2/route.ts');const db=setup();try{await eligible(db);for(const [headers,body,status] of [[{'content-type':'application/json',origin:'https://other.invalid'},'{}',403],[{},'{}',415],[{'content-type':'application/json'},'{"action":"resume"}',409],[{'content-type':'application/json'},'{',409],[{'content-type':'application/json'},JSON.stringify({action:'diagnose-once',padding:'x'.repeat(2048)}),409]]){const r=await route.POST(new Request('https://private.invalid/api/collection/source-diagnostic-v2',{method:'POST',headers,body}));assert.equal(r.status,status);}assert.equal(calls,0);assert.equal(n(db,'collection_source_diagnostics_v2'),0);}finally{done(db);}
});
test('route POST and GET return same bounded result and GET remains read only with gate disabled',async()=>{
 const route=await import('../app/api/collection/source-diagnostic-v2/route.ts');const db=setup();try{const x=await eligible(db),url='https://private.invalid/api/collection/source-diagnostic-v2',req=()=>new Request(url,{method:'POST',headers:{'content-type':'application/json',origin:'https://private.invalid'},body:JSON.stringify({action:'diagnose-once',requestId:x.id,sessionId:x.sessionId,priorProbeId:x.priorProbeId})});const response=await route.POST(req());assert.equal(response.status,200);const r=await response.json();await route.POST(req());assert.equal(calls,1);env.G2B_DIAGNOSTIC_V2_ENABLED='';const before=db.prepare('SELECT total_changes() n').get().n;const read=await route.GET(new Request(url+'?id='+x.id));assert.equal(read.status,200);assert.equal(read.headers.get('cache-control'),'no-store');assert.deepEqual(await read.json(),r);assert.equal(db.prepare('SELECT total_changes() n').get().n,before);assert.equal((await route.POST(req())).status,409);assert.equal(calls,1);await assert.rejects(probe.readSourceDiagnosticV2('bad'),/INVALID/);}finally{done(db);}
});
test('network error getters are read once and cannot swap allowlisted values for secrets',async()=>{
 const {safeNetworkError}=await import('../lib/g2b-network-diagnostic.mjs');let names=0,codes=0;
 const changing={get name(){return ++names===1?'TypeError':'SECRET_NAME';},get code(){return ++codes===1?'ECONNRESET':'SECRET_CODE';}};
 assert.deepEqual(safeNetworkError(changing),{errorName:'TypeError',errorCategory:'ECONNRESET'});assert.equal(names,1);assert.equal(codes,1);
 for(const key of ['name','code']){const value={name:'TypeError',code:'ECONNRESET'};Object.defineProperty(value,key,{get(){throw Error('SECRET getter');}});const result=safeNetworkError(value);assert.ok(!JSON.stringify(result).includes('SECRET'));assert.ok(['Other','TypeError'].includes(result.errorName));assert.ok(['OTHER','ECONNRESET'].includes(result.errorCategory));}
});
test('late fetch resolution or rejection after timeout cannot rewrite result or issue another request',async()=>{
 for(const outcome of ['resolve','reject']){const db=setup(),timer=globalThis.setTimeout;try{const x=await eligible(db);let settle;globalThis.setTimeout=(fn,ms,...args)=>timer(fn,Math.min(ms,5),...args);globalThis.fetch=async()=>{calls++;return new Promise((resolve,reject)=>{settle=outcome==='resolve'?resolve:reject;});};const r=await x.run();assert.equal(r.result.code,'DIAGNOSTIC_TIMEOUT');assert.equal(r.result.timeoutObserved,true);const used=usage(db);
 settle(outcome==='resolve'?Response.json(mixedFixture(svc.config().to,1,999,total)):new TypeError('SECRET late failure'));
 await new Promise(resolve=>timer(resolve,15));assert.deepEqual(await probe.readSourceDiagnosticV2(x.id),r);assert.deepEqual(await x.run(),r);assert.equal(calls,1);assert.equal(usage(db),used);assert.equal(n(db,'collection_source_diagnostics_v2'),1);
 }finally{globalThis.setTimeout=timer;done(db);}}
});
test('prior audit mutation after eligibility read blocks reservation or prefetch dispatch',async()=>{
 for(const stage of ['reservation','prefetch'])for(const [column,value] of [['day','2000-01-01'],['page',777],['failed_at','changed'],['status','reserved'],['completed_at',null],['result_json','{}']]){const db=setup();try{const x=await eligible(db),used=usage(db);let changed=false;
 beforeRead=sql=>{const match=stage==='reservation'?sql.startsWith('SELECT * FROM collection_source_diagnostics_v2 WHERE prior_probe_id='):sql.startsWith('SELECT s.id FROM collection_sessions s WHERE ')&&sql.includes('AND EXISTS(SELECT 1 FROM collection_source_probes p WHERE');if(match&&!changed){changed=true;db.prepare('UPDATE collection_source_probes SET '+column+'=? WHERE id=?').run(value,x.priorProbeId);}};
 if(stage==='reservation')await assert.rejects(x.run(),/QUOTA_OR_SNAPSHOT_BLOCKED/);else{const r=await x.run();assert.equal(r.result.code,'DIAGNOSTIC_PRIOR_PROBE_NOT_ELIGIBLE');assert.equal(r.result.phase,'validate');}
 assert.equal(changed,true);assert.equal(calls,0);assert.equal(usage(db),used+(stage==='prefetch'?1:0));assert.equal(n(db,'collection_source_diagnostics_v2'),stage==='prefetch'?1:0);
 }finally{done(db);}}
});
test('timeout cancels a stalled body reader and cancels a response arriving after deadline',async()=>{
 for(const stage of ['body','late-response']){const db=setup(),timer=globalThis.setTimeout;try{const x=await eligible(db);let cancelled=0,resolveFetch;const response=new Response(new ReadableStream({start(){},cancel(){cancelled++;}}));globalThis.setTimeout=(fn,ms,...args)=>timer(fn,Math.min(ms,5),...args);
 globalThis.fetch=async()=>{calls++;return stage==='body'?response:new Promise(resolve=>{resolveFetch=resolve;});};const r=await x.run();assert.equal(r.result.code,'DIAGNOSTIC_TIMEOUT');if(stage==='late-response')resolveFetch(response);await new Promise(resolve=>timer(resolve,10));assert.equal(cancelled,1);assert.equal(calls,1);assert.deepEqual(await probe.readSourceDiagnosticV2(x.id),r);
 }finally{globalThis.setTimeout=timer;done(db);}}
});
test('hostile body read exceptions cannot escape bounded sanitized diagnostic persistence',async()=>{
 for(const kind of ['throwing-message','changing-message','throwing-cause']){const db=setup();try{const x=await eligible(db);let reads=0;const hostile={name:'TypeError',code:'ECONNRESET'};
 Object.defineProperty(hostile,'message',{get(){reads++;if(kind==='throwing-message')throw Error('SECRET message');return kind==='changing-message'&&reads>1?'SECRET changed message':'unrecognized failure';}});
 Object.defineProperty(hostile,'cause',{get(){throw Error('SECRET cause');}});
 globalThis.fetch=async()=>{calls++;return new Response(new ReadableStream({start(controller){controller.error(hostile);}}));};const r=await x.run();assert.equal(r.status,'complete');assert.equal(r.result.phase,'body');assert.equal(r.result.sourceRowsWritten,0);assert.equal(r.result.automaticRetryAllowed,false);assert.ok(!JSON.stringify(r).includes('SECRET'));assert.ok(JSON.stringify(r).length<2000);assert.deepEqual(await probe.readSourceDiagnosticV2(x.id),r);assert.equal(calls,1);
 }finally{done(db);}}
});
test('stalled route request body is cancelled without quota or provider use',async()=>{
 const route=await import('../app/api/collection/source-diagnostic-v2/route.ts'),db=setup(),timer=globalThis.setTimeout;try{await eligible(db);const used=usage(db);let cancelled=0;globalThis.setTimeout=(fn,ms,...args)=>timer(fn,Math.min(ms,5),...args);const body=new ReadableStream({start(){},cancel(){cancelled++;}});const response=await route.POST(new Request('https://private.invalid/api/collection/source-diagnostic-v2',{method:'POST',headers:{'content-type':'application/json'},body,duplex:'half'}));assert.equal(response.status,409);assert.equal(cancelled,1);assert.equal(calls,0);assert.equal(usage(db),used);assert.equal(n(db,'collection_source_diagnostics_v2'),0);}finally{globalThis.setTimeout=timer;done(db);}
});
test('HTTP failure response body is cancelled even before reader acquisition',async()=>{
 const db=setup();try{const x=await eligible(db);let cancelled=0;globalThis.fetch=async()=>{calls++;return new Response(new ReadableStream({start(){},cancel(){cancelled++;}}),{status:503});};const r=await x.run();assert.equal(r.result.httpStatus,503);assert.notEqual(r.result.code,'SOURCE_VALIDATION_PASSED_STORAGE_NOT_TESTED');assert.equal(cancelled,1);assert.equal(calls,1);}finally{done(db);}
});
