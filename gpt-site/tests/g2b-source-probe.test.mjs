import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync,readdirSync,existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {registerHooks} from 'node:module';
import {mixedFixture} from '../lib/g2b-domain.mjs';
const env={};globalThis.__g2bProbeEnv=env;
registerHooks({resolve(spec,ctx,next){if(spec==='cloudflare:workers')return {url:'data:text/javascript,export const env=globalThis.__g2bProbeEnv',shortCircuit:true};if(spec.startsWith('.')&&!/\.(ts|mjs|js)$/.test(spec)&&ctx.parentURL?.startsWith('file:')){const u=new URL(spec+'.ts',ctx.parentURL);if(existsSync(fileURLToPath(u)))return next(u.href,ctx);}return next(spec,ctx);}});
const svc=await import('../lib/g2b-service.ts'),probe=await import('../lib/g2b-source-probe.ts');
const route=await import('../app/api/collection/source-probe/route.ts');
const realFetch=globalThis.fetch;
let calls=0,change=null,fault=null,total=11988,beforeRead=null;
const tables=['collection_sessions','collection_receipts','day_checkpoints','seen_source_keys','delivery_rows','dataset_epochs'];
const saved=db=>tables.map(t=>JSON.stringify(db.prepare('SELECT * FROM '+t+' ORDER BY rowid').all()));
function setup(){
 const db=new DatabaseSync(':memory:');db.exec('PRAGMA foreign_keys=ON');for(const f of readdirSync(new URL('../drizzle/',import.meta.url)).filter(f=>f.endsWith('.sql')).sort())db.exec(readFileSync(new URL('../drizzle/'+f,import.meta.url),'utf8'));
 env.DB={prepare(sql){let args=[];const q={bind(...v){args=v;return q;},async first(){if(beforeRead)beforeRead(sql);return db.prepare(sql).get(...args)||null;},async all(){return {results:db.prepare(sql).all(...args)};},async run(){if(fault==='result'&&sql.includes("SET status='complete'"))throw Error('SECRET result persistence failure');return q._run();},_run(){const r=db.prepare(sql).run(...args);return {meta:{changes:Number(r.changes)}};}};return q;},async batch(qs){db.exec('BEGIN');try{const result=qs.map((q,i)=>{if(fault==='reserve'&&i===1)throw Error('SECRET reservation failure');return q._run();});db.exec('COMMIT');if(fault==='ack')throw Error('SECRET lost acknowledgement');return result;}catch(e){if(db.isTransaction)db.exec('ROLLBACK');throw e;}}};
 Object.assign(env,{G2B_SERVICE_KEY:'OFFLINE_FAKE_DO_NOT_TRANSMIT',G2B_LIVE_ENABLED:'approved',G2B_QUOTA_SCOPE_REVIEWED:'allocation-confirmed',G2B_DAILY_BUDGET:'950',G2B_AUTOCOLLECT_ENABLED:'enabled',G2B_DIAGNOSTIC_PROBE_ENABLED:'approved-once'});
 calls=0;change=null;fault=null;total=11988;beforeRead=null;globalThis.fetch=async(url,opts)=>{calls++;assert.equal(new URL(url).origin,'https://apis.data.go.kr');if(opts?.redirect)assert.equal(opts.redirect,'error');const u=new URL(url),n=Number(u.searchParams.get('pageNo')),raw=u.searchParams.get('inqryBgnDate'),day=raw.slice(0,4)+'-'+raw.slice(4,6)+'-'+raw.slice(6,8);const data=mixedFixture(day,n,999,total);if(change)change(data,n);return Response.json(data);};return db;
}
function done(db){db.close();globalThis.fetch=realFetch;}
async function failed(db,pages=0){const c=svc.config(),id=(await svc.createSession('live',c.to,c.to)).session.id;for(let n=0;n<pages;n++)await svc.sessionStep(id);db.prepare("UPDATE collection_sessions SET state='error',error='STORAGE_OR_SOURCE_FAILED',updated_at=? WHERE id=?").run(new Date().toISOString(),id);return id;}
const n=(db,table)=>Number(db.prepare('SELECT COUNT(*) n FROM '+table).get().n);
const usage=db=>Number(db.prepare('SELECT COALESCE(SUM(calls),0) n FROM usage').get().n);
test('one p11 source diagnostic validates without committing source data or clearing the error',async()=>{
 const db=setup();try{const id=await failed(db,10),before=saved(db),used=usage(db),requestId=crypto.randomUUID();calls=0;const r=await probe.sourceProbe(requestId,id);
 assert.equal(r.result.code,'SOURCE_VALIDATION_PASSED_STORAGE_NOT_TESTED');assert.equal(r.page,11);assert.equal(r.result.sourceRows,999);assert.equal(r.result.sourceRowsWritten,0);assert.equal(r.result.collectionSnapshotUnchanged,true);assert.equal(r.collectionResumed,false);assert.deepEqual(saved(db),before);assert.equal(usage(db),used+1);assert.equal(calls,1);assert.equal(n(db,'collection_source_probes'),1);
 assert.deepEqual(await probe.sourceProbe(requestId,id),r);assert.deepEqual(await probe.sourceProbe(crypto.randomUUID(),id),r);assert.equal(calls,1);assert.equal((await svc.automaticTick()).performed,false);await assert.rejects(svc.sessionStep(id),/REVIEW_REQUIRED/);assert.equal(calls,1);
 assert.ok(!JSON.stringify(r).includes('OFFLINE_FAKE'));assert.ok(!JSON.stringify(r).includes('SYNTHETIC-'));assert.ok(!JSON.stringify(r).includes('attempt_owner'));
 }finally{done(db);}
});
test('default-disabled and unreviewed quota gates perform no source call or database write',async()=>{
 for(const property of ['G2B_DIAGNOSTIC_PROBE_ENABLED','G2B_QUOTA_SCOPE_REVIEWED','G2B_SERVICE_KEY']){const db=setup();try{const id=await failed(db);env[property]='';const before=db.prepare('SELECT total_changes() n').get().n;await assert.rejects(probe.sourceProbe(crypto.randomUUID(),id));assert.equal(db.prepare('SELECT total_changes() n').get().n,before);assert.equal(calls,0);}finally{done(db);}}
});
test('paused/cancelled/running/known-error sessions cannot use the diagnostic lane',async()=>{
 for(const [state,error] of [['paused','STORAGE_OR_SOURCE_FAILED'],['cancelled','STORAGE_OR_SOURCE_FAILED'],['running','STORAGE_OR_SOURCE_FAILED'],['error','SOURCE_PAGE_OVERLAP'],['error','DATABASE_UNKNOWN']]){const db=setup();try{const id=await failed(db);db.prepare('UPDATE collection_sessions SET state=?,error=? WHERE id=?').run(state,error,id);await assert.rejects(probe.sourceProbe(crypto.randomUUID(),id),/NOT_ELIGIBLE/);assert.equal(calls,0);assert.equal(n(db,'collection_source_probes'),0);}finally{done(db);}}
});
test('metadata inconsistency and quota exhaustion fail closed',async()=>{
 for(const kind of ['metadata','quota']){const db=setup();try{const id=await failed(db);if(kind==='metadata')db.prepare('UPDATE collection_sessions SET fetched=1 WHERE id=?').run(id);else{env.G2B_DAILY_BUDGET='1';await db.exec("INSERT INTO usage(day,calls) VALUES ('"+new Date(Date.now()+9*3600000).toISOString().slice(0,10)+"',1)");}await assert.rejects(probe.sourceProbe(crypto.randomUUID(),id));assert.equal(calls,0);assert.equal(n(db,'collection_source_probes'),0);}finally{done(db);}}
});
test('cross-page overlap is diagnosed without source row mutations',async()=>{
 const db=setup();try{const id=await failed(db,1),before=saved(db);calls=0;change=(raw,page)=>{assert.equal(page,2);raw.response.body.items[0]=mixedFixture(svc.config().to,1,999,total).response.body.items[0];};const r=await probe.sourceProbe(crypto.randomUUID(),id);assert.equal(r.result.code,'SOURCE_PAGE_OVERLAP');assert.equal(r.result.phase,'overlap-check');assert.deepEqual(saved(db),before);assert.equal(calls,1);}finally{done(db);}
});
test('source malformed data and validation errors report only allowlisted codes and phases',async()=>{
 for(const [kind,expected,phase] of [['null','SOURCE_PROCESSING_FAILED','parse'],['duplicate','SOURCE_DUPLICATE_ID','parse'],['total','SOURCE_TOTAL_CHANGED','parse'],['number','SOURCE_NUMBER_OUT_OF_RANGE','normalize']]){const db=setup();try{const id=await failed(db,1),before=saved(db);calls=0;change=r=>{if(kind==='null')r.response.body.items[0]=null;if(kind==='duplicate')r.response.body.items[1]=r.response.body.items[0];if(kind==='total')r.response.body.totalCount++;if(kind==='number')r.response.body.items[1].prdctAmt=Number.MAX_VALUE;};const r=await probe.sourceProbe(crypto.randomUUID(),id);assert.equal(r.result.code,expected);assert.equal(r.result.phase,phase);assert.equal(calls,1);assert.deepEqual(saved(db),before);}finally{done(db);}}
});
test('network failure consumes one permit and cannot automatically or manually repeat the probe',async()=>{
 const db=setup();try{const id=await failed(db);globalThis.fetch=async()=>{calls++;throw Error('https://secret.invalid/?serviceKey=SECRET');};const r=await probe.sourceProbe(crypto.randomUUID(),id);assert.equal(r.result.code,'SOURCE_NETWORK_OR_TIMEOUT');await probe.sourceProbe(crypto.randomUUID(),id);assert.equal(calls,1);assert.equal(usage(db),1);assert.ok(!JSON.stringify(r).includes('SECRET'));}finally{done(db);}
});
test('concurrent distinct request IDs reserve exactly one quota and perform at most one source call',async()=>{
 const db=setup();try{const id=await failed(db);const out=await Promise.all([probe.sourceProbe(crypto.randomUUID(),id),probe.sourceProbe(crypto.randomUUID(),id)]);assert.equal(out[0].requestId,out[1].requestId);assert.equal(calls,1);assert.equal(usage(db),1);assert.equal(n(db,'collection_source_probes'),1);}finally{done(db);}
});
test('lost reservation acknowledgement leaves a durable non-retryable uncertain attempt and no source call',async()=>{
 const db=setup();try{const id=await failed(db),requestId=crypto.randomUUID();fault='ack';await assert.rejects(probe.sourceProbe(requestId,id),/RESERVATION_OUTCOME_UNCERTAIN/);fault=null;const r=await probe.sourceProbe(requestId,id);assert.equal(r.status,'reserved');assert.equal(r.result,null);assert.equal(calls,0);assert.equal(usage(db),1);await probe.sourceProbe(crypto.randomUUID(),id);assert.equal(calls,0);}finally{done(db);}
});
test('transaction failure rolls back both audit latch and quota before any source call',async()=>{
 const db=setup();try{const id=await failed(db);fault='reserve';await assert.rejects(probe.sourceProbe(crypto.randomUUID(),id),/RESERVATION_OUTCOME_UNCERTAIN/);assert.equal(calls,0);assert.equal(usage(db),0);assert.equal(n(db,'collection_source_probes'),0);}finally{done(db);}
});
test('lost diagnostic result persistence cannot cause another source call',async()=>{
 const db=setup();try{const id=await failed(db),requestId=crypto.randomUUID();fault='result';await assert.rejects(probe.sourceProbe(requestId,id),/RESULT_OUTCOME_UNCERTAIN/);fault=null;const r=await probe.sourceProbe(requestId,id);assert.equal(r.status,'reserved');assert.equal(calls,1);assert.equal(usage(db),1);await probe.sourceProbe(crypto.randomUUID(),id);assert.equal(calls,1);}finally{done(db);}
});
test('operator cancellation during the issued request is preserved and clearly reported',async()=>{
 const db=setup();try{const id=await failed(db);change=()=>db.prepare("UPDATE collection_sessions SET state='cancelled' WHERE id=?").run(id);const r=await probe.sourceProbe(crypto.randomUUID(),id);assert.equal(r.result.collectionSnapshotUnchanged,false);assert.equal(db.prepare('SELECT state FROM collection_sessions WHERE id=?').get(id).state,'cancelled');assert.equal(n(db,'collection_receipts'),0);assert.equal(calls,1);}finally{done(db);}
});
test('request ID is bound to its original session and invalid identifiers are rejected',async()=>{
 const db=setup();try{const id=await failed(db),requestId=crypto.randomUUID();await probe.sourceProbe(requestId,id);await assert.rejects(probe.sourceProbe(requestId,crypto.randomUUID()),/REQUEST_CONFLICT/);await assert.rejects(probe.sourceProbe('bad',id),/INVALID/);assert.equal(calls,1);}finally{done(db);}
});
test('KST midnight after reservation consumes the latch but never dispatches using yesterday quota',async()=>{
 const RealDate=Date;let now=RealDate.parse('2026-11-08T14:59:59.000Z');
 globalThis.Date=class extends RealDate{constructor(...args){super(...(args.length?args:[now]));}static now(){return now;}};
 const db=setup();try{const id=await failed(db);beforeRead=sql=>{if(sql.startsWith('SELECT s.id FROM collection_sessions s WHERE '))now=RealDate.parse('2026-11-08T15:00:01.000Z');};const r=await probe.sourceProbe(crypto.randomUUID(),id);assert.equal(r.result.code,'DIAGNOSTIC_QUOTA_DAY_CHANGED');assert.equal(calls,0);assert.equal(usage(db),1);assert.equal(db.prepare('SELECT day FROM usage').get().day,'2026-11-08');await probe.sourceProbe(crypto.randomUUID(),id);assert.equal(calls,0);}finally{done(db);globalThis.Date=RealDate;}
});
test('HTTP route rejects cross-origin, non-JSON and bad actions before a source attempt',async()=>{
 const db=setup();try{await failed(db);for(const [headers,body,status] of [[{'content-type':'application/json',origin:'https://other.invalid'},'{}',403],[{},'{}',415],[{'content-type':'application/json'},'{"action":"resume"}',409]]){const r=await route.POST(new Request('https://private.invalid/api/collection/source-probe',{method:'POST',headers,body}));assert.equal(r.status,status);}assert.equal(calls,0);assert.equal(n(db,'collection_source_probes'),0);}finally{done(db);}
});
test('HTTP route requires the separate gate and reads the same bounded diagnostic result without a repeat',async()=>{
 const db=setup();try{const id=await failed(db),requestId=crypto.randomUUID(),url='https://private.invalid/api/collection/source-probe';
 const request=()=>new Request(url,{method:'POST',headers:{'content-type':'application/json',origin:'https://private.invalid'},body:JSON.stringify({action:'diagnose-once',requestId,sessionId:id})});
 env.G2B_DIAGNOSTIC_PROBE_ENABLED='';const blocked=await route.POST(request());assert.equal(blocked.status,409);assert.equal(calls,0);
 env.G2B_DIAGNOSTIC_PROBE_ENABLED='approved-once';const result=await route.POST(request());assert.equal(result.status,200);const json=await result.json();assert.equal(json.result.sourceRowsWritten,0);assert.equal(calls,1);
 const read=await route.GET(new Request(url+'?id='+requestId));assert.equal(read.status,200);assert.equal(read.headers.get('cache-control'),'no-store');assert.deepEqual(await read.json(),json);await route.POST(request());assert.equal(calls,1);
 }finally{done(db);}
});
