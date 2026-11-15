import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync,readdirSync,existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {registerHooks} from 'node:module';
import {mixedFixture} from '../lib/g2b-domain.mjs';
import {collectionFailureCode,reportCollectionFailure,assertNoPageOverlap,safeStoredError} from '../lib/g2b-diagnostics.mjs';
import {automaticDecision} from '../lib/g2b-automation.mjs';
const env={DB:null};globalThis.__g2bDiagnosticEnv=env;
registerHooks({resolve(spec,ctx,next){if(spec==='cloudflare:workers')return {url:'data:text/javascript,export const env=globalThis.__g2bDiagnosticEnv',shortCircuit:true};if(spec.startsWith('.')&&!/\.(ts|mjs|js)$/.test(spec)&&ctx.parentURL?.startsWith('file:')){const u=new URL(spec+'.ts',ctx.parentURL);if(existsSync(fileURLToPath(u)))return next(u.href,ctx);}return next(spec,ctx);}});
const svc=await import('../lib/g2b-service.ts');
const realFetch=globalThis.fetch,realLog=console.error;
let fetches=0,logs=[],fault=null,change=null,total=1998;
function setup(){
 const db=new DatabaseSync(':memory:');for(const f of readdirSync(new URL('../drizzle/',import.meta.url)).filter(f=>f.endsWith('.sql')).sort())db.exec(readFileSync(new URL('../drizzle/'+f,import.meta.url),'utf8'));
 env.DB={prepare(sql){let args=[];const self={bind(...v){args=v;return self;},async first(){if(fault==='overlap'&&sql.includes('SELECT 1 AS overlap'))throw Error('D1_ERROR: connection reset SECRET');return db.prepare(sql).get(...args)||null;},async all(){return {results:db.prepare(sql).all(...args)};},async run(){return self._run();},_run(){if(fault==='error-record'&&sql.includes("SET state='error'"))throw Error('SECRET_ERROR_RECORD');const r=db.prepare(sql).run(...args);return {meta:{changes:Number(r.changes)}};}};return self;},async batch(qs){db.exec('BEGIN');try{const out=[];for(let i=0;i<qs.length;i++){out.push(qs[i]._run());if(fault===i)throw Error('D1_ERROR: simulated fault SECRET SQL');}db.exec('COMMIT');if(fault==='lost-ack')throw Error('D1_ERROR: connection reset SECRET');return out;}catch(e){if(db.isTransaction)db.exec('ROLLBACK');throw e;}}};
 Object.assign(env,{G2B_SERVICE_KEY:'OFFLINE_FAKE_DO_NOT_TRANSMIT',G2B_LIVE_ENABLED:'approved',G2B_QUOTA_SCOPE_REVIEWED:'allocation-confirmed',G2B_DAILY_BUDGET:'950',G2B_AUTOCOLLECT_ENABLED:'enabled'});
 fetches=0;logs=[];fault=null;change=null;total=1998;console.error=x=>logs.push(x);
 globalThis.fetch=async url=>{fetches++;const u=new URL(url),n=Number(u.searchParams.get('pageNo')),rawDay=u.searchParams.get('inqryBgnDate'),day=rawDay.slice(0,4)+'-'+rawDay.slice(4,6)+'-'+rawDay.slice(6,8);const raw=mixedFixture(day,n,999,total);if(change)change(raw,n);return Response.json(raw);};return db;
}
function done(db){globalThis.fetch=realFetch;console.error=realLog;db.close();}
async function create(){const c=svc.config();return (await svc.createSession('live',c.to,c.to)).session.id;}
const state=(db,id)=>db.prepare('SELECT * FROM collection_sessions WHERE id=?').get(id);
const scalar=(db,sql)=>db.prepare(sql).get();

test('only known codes and safe classifications survive arbitrary exception strings',()=>{
 for(const message of ['SECRET_TOKEN','https://provider/?serviceKey=secret\nSELECT password','SQLITE_CONSTRAINT SECRET','SOURCE_HTTP_999'])for(const phase of ['parse','normalize','prepare-commit'])assert.equal(collectionFailureCode(Error(message),phase),'SOURCE_PROCESSING_FAILED');
 assert.equal(collectionFailureCode(Error('SOURCE_HTTP_503'),'source'),'SOURCE_HTTP_503');
 assert.equal(collectionFailureCode(Error('D1_ERROR: UNIQUE constraint failed: seen_source_keys.session_id, seen_source_keys.day, seen_source_keys.source_key: SQLITE_CONSTRAINT'),'commit'),'SOURCE_PAGE_OVERLAP');
 assert.notEqual(collectionFailureCode(Error('UNIQUE constraint failed: delivery_rows.mode, delivery_rows.source_key'),'commit'),'SOURCE_PAGE_OVERLAP');
 assert.equal(safeStoredError('DATABASE_SECRET_TOKEN'),'COLLECTION_ERROR_UNCLASSIFIED');assert.equal(safeStoredError('STORAGE_OR_SOURCE_FAILED'),'STORAGE_OR_SOURCE_FAILED');
 let captured='';console.error=x=>{captured=x;};try{reportCollectionFailure(Error('TOP_SECRET'),'normalize',{id:'session',day:'2026-11-05',page:11},{responseValidated:true});assert.ok(!captured.includes('TOP_SECRET'));console.error=()=>{throw Error('logger unavailable');};assert.doesNotThrow(()=>reportCollectionFailure(Error('TOP_SECRET'),'normalize',{id:'session',day:'2026-11-05',page:11}));}finally{console.error=realLog;}
});

test('full and single excluded-row cross-page overlap preserve page one and stop all retry paths',async()=>{
 for(const full of [false,true]){const db=setup();try{const id=await create();await svc.sessionStep(id);const before=state(db,id);change=(r,n)=>{if(n===2){const prior=mixedFixture(svc.config().to,1,999,total).response.body.items;if(full)r.response.body.items=prior;else r.response.body.items[0]=prior[4];}};
 await assert.rejects(svc.sessionStep(id),/SOURCE_PAGE_OVERLAP/);const after=state(db,id);assert.equal(after.page,2);assert.equal(after.fetched,before.fetched);assert.equal(after.matched,before.matched);assert.equal(after.calls,2);assert.equal(scalar(db,'SELECT COUNT(*) n FROM collection_receipts').n,1);assert.equal(scalar(db,'SELECT COUNT(*) n FROM seen_source_keys').n,999);
 const count=fetches;await assert.rejects(svc.sessionStep(id),/REVIEW_REQUIRED/);await svc.sessionState(id,'paused');await svc.sessionState(id,'ready');assert.equal(state(db,id).error,'SOURCE_PAGE_OVERLAP');await assert.rejects(svc.sessionStep(id),/REVIEW_REQUIRED/);assert.equal((await svc.automaticTick()).performed,false);assert.equal(fetches,count);assert.ok(logs.every(x=>!x.includes('OFFLINE_FAKE')&&!x.includes('SYNTHETIC-')));
 const diagnostic=await svc.collectionRecovery(id);assert.equal(diagnostic.pageCommitState,'NOT_COMMITTED');assert.equal(diagnostic.resumeAuthorized,false);
 }finally{done(db);}}
});

test('overlap check scopes both session and day and keeps bindings bounded',async()=>{
 const calls=[];const db={prepare(sql){return {bind(...args){calls.push({sql,args});return this;},async first(){return null;}};}};
 const keys=Array.from({length:999},(_,i)=>JSON.stringify(['R'.repeat(1024)+i,'1','S'.repeat(1024)]));await assertNoPageOverlap(db,{id:'S',day:'2026-11-05'},keys);assert.ok(calls.length>1);for(const c of calls){assert.equal(c.args[0],'S');assert.equal(c.args[1],'2026-11-05');assert.ok(Buffer.byteLength(c.args[2])<=1400000);assert.equal(c.args.length,3);}
});

test('in-page duplicates retain a different code and raw row shape failures are safely classified',async()=>{
 for(const kind of ['duplicate','null','unsafe-money']){const db=setup();try{const id=await create();change=r=>{const a=r.response.body.items;if(kind==='duplicate')a[1]={...a[0]};if(kind==='null')a[0]=null;if(kind==='unsafe-money')a[0].prdctAmt=9007199254740992;};const expected={duplicate:'SOURCE_DUPLICATE_ID',null:'SOURCE_PROCESSING_FAILED','unsafe-money':'SOURCE_NUMBER_OUT_OF_RANGE'}[kind];await assert.rejects(svc.sessionStep(id),new RegExp(expected));assert.equal(state(db,id).page,1);assert.equal(scalar(db,'SELECT COUNT(*) n FROM collection_receipts').n,0);assert.ok(logs.every(x=>!x.includes('OFFLINE_FAKE')));}finally{done(db);}}
});

test('every page batch statement failure rolls back durable page evidence',async()=>{
 for(let i=0;i<7;i++){const db=setup();try{const id=await create();fault=i;await assert.rejects(svc.sessionStep(id),/DATABASE_D1_ERROR/);assert.equal(state(db,id).page,1);assert.equal(state(db,id).fetched,0);for(const t of ['delivery_rows','seen_source_keys','collection_receipts','day_checkpoints'])assert.equal(scalar(db,'SELECT COUNT(*) n FROM '+t).n,0);assert.equal(scalar(db,"SELECT revision FROM dataset_epochs WHERE mode='live'").revision,0);assert.equal((await svc.collectionRecovery(id)).pageCommitState,'NOT_COMMITTED');}finally{done(db);}}
});

test('overlap-read failure, diagnostic write failure and lost acknowledgement never imply rollback',async()=>{
 for(const kind of ['overlap','error-record','lost-ack']){const db=setup();try{const id=await create();fault=kind;if(kind==='error-record')change=r=>{r.response.body.items[0]=null;};await assert.rejects(svc.sessionStep(id),new RegExp(kind==='error-record'?'SOURCE_PROCESSING_FAILED':'DATABASE_CONNECTION'));const s=state(db,id);if(kind==='lost-ack'){assert.equal(s.page,2);assert.equal(s.state,'ready');assert.equal(scalar(db,'SELECT COUNT(*) n FROM collection_receipts').n,1);}else assert.equal(s.page,1);assert.ok(logs.every(x=>!x.includes('SECRET')));if(kind==='error-record')assert.equal((await svc.collectionRecovery(id)).pageCommitState,'IN_FLIGHT');}finally{done(db);}}
});

test('single-statement recovery is read-only, handles more than eight receipts and flags corruption',async()=>{
 const db=setup();try{total=11988;const id=await create();for(let i=0;i<10;i++)await svc.sessionStep(id);const before=scalar(db,'SELECT total_changes() n').n,count=fetches;const d=await svc.collectionRecovery(id);assert.equal(d.receiptCount,10);assert.equal(d.page,11);assert.equal(d.pageCommitState,'NOT_COMMITTED');assert.equal(d.resumeAuthorized,false);assert.equal(scalar(db,'SELECT total_changes() n').n,before);assert.equal(fetches,count);
 db.prepare('DELETE FROM collection_receipts WHERE session_id=? AND page=2').run(id);assert.equal((await svc.collectionRecovery(id)).pageCommitState,'REVIEW_REQUIRED');
 }finally{done(db);}
});

test('source errors remain stopped and cooldown/daily reset guard cannot be bypassed by pause/resume',async()=>{
 for(const error of ['STORAGE_OR_SOURCE_FAILED','SOURCE_PAGE_OVERLAP','SOURCE_PROCESSING_FAILED','DATABASE_BUSY','SOURCE_NETWORK_OR_TIMEOUT','LOCAL_DAILY_BUDGET_REACHED']){const db=setup();try{const id=await create();db.prepare("UPDATE collection_sessions SET state='error',error=?,updated_at=? WHERE id=?").run(error,new Date().toISOString(),id);await assert.rejects(svc.sessionStep(id),/REVIEW_REQUIRED/);await svc.sessionState(id,'paused');await svc.sessionState(id,'ready');await assert.rejects(svc.sessionStep(id),/REVIEW_REQUIRED/);assert.equal(fetches,0);if(!['SOURCE_NETWORK_OR_TIMEOUT','LOCAL_DAILY_BUDGET_REACHED'].includes(error))assert.equal(automaticDecision(state(db,id),svc.config()).action,'stop');}finally{done(db);}}
});

test('permitted cooled network retry and healthy completion still work without a new session',async()=>{
 const db=setup();try{total=1;const id=await create();db.prepare("UPDATE collection_sessions SET state='error',error='SOURCE_NETWORK_OR_TIMEOUT',updated_at=? WHERE id=?").run(new Date(Date.now()-901000).toISOString(),id);await svc.sessionStep(id);const d=await svc.collectionRecovery(id);assert.equal(d.pageCommitState,'SESSION_COMPLETE');assert.equal(d.resumeAuthorized,false);assert.equal(fetches,1);}finally{done(db);}
});

test('pause and resume preserve original failure time for network and next-KST-day quota recovery',async()=>{
 for(const error of ['SOURCE_NETWORK_OR_TIMEOUT','LOCAL_DAILY_BUDGET_REACHED']){const db=setup();try{total=1;const id=await create(),stamp=new Date(Date.now()-2*86400000).toISOString();db.prepare("UPDATE collection_sessions SET state='error',error=?,updated_at=? WHERE id=?").run(error,stamp,id);await svc.sessionState(id,'paused');await svc.sessionState(id,'ready');assert.equal(state(db,id).updated_at,stamp);await svc.sessionStep(id);assert.equal(state(db,id).state,'complete');assert.equal(fetches,1);}finally{done(db);}}
});

test('completed session with corrupt last-day checkpoint cannot be certified',async()=>{
 for(const field of ['next_page=99',"state='incomplete'",'fetched=999','matched=999']){const db=setup();try{total=1;const id=await create();await svc.sessionStep(id);db.exec('UPDATE day_checkpoints SET '+field);const d=await svc.collectionRecovery(id);assert.equal(d.pageCommitState,'REVIEW_REQUIRED');assert.notEqual(d.nextAction,'NO_RECOVERY_NEEDED');assert.equal(d.metadataConsistent,false);}finally{done(db);}}
});

test('failed error persistence and expired running leases cannot retry via direct step, automation or UI state changes',async()=>{
 const db=setup();try{const id=await create();fault='error-record';change=r=>{r.response.body.items[0]=null;};await assert.rejects(svc.sessionStep(id),/SOURCE_PROCESSING_FAILED/);fault=null;db.prepare('UPDATE collection_sessions SET lease_until=0 WHERE id=?').run(id);const d=await svc.collectionRecovery(id);assert.equal(d.pageCommitState,'EXECUTION_OUTCOME_UNCERTAIN');assert.equal(d.nextAction,'REVIEW_EXPIRED_REQUEST_BEFORE_ACTION');const n=fetches;await assert.rejects(svc.sessionStep(id),/REVIEW_REQUIRED/);assert.equal((await svc.automaticTick()).blocking,'EXPIRED_REQUEST_REVIEW_REQUIRED');await svc.sessionState(id,'paused');await svc.sessionState(id,'ready');assert.equal(state(db,id).error,'EXPIRED_REQUEST_REVIEW_REQUIRED');await assert.rejects(svc.sessionStep(id),/REVIEW_REQUIRED/);assert.equal((await svc.automaticTick()).performed,false);assert.equal(fetches,n);}finally{done(db);}
});

test('each final-page statement fault rolls back terminal reconciliation and source changes',async()=>{
 for(let i=0;i<10;i++){const db=setup();try{total=1;const id=await create();fault=i;await assert.rejects(svc.sessionStep(id),/DATABASE_D1_ERROR/);assert.equal(state(db,id).page,1);assert.equal(state(db,id).completed_days,0);for(const t of ['delivery_rows','seen_source_keys','collection_receipts','day_checkpoints'])assert.equal(scalar(db,'SELECT COUNT(*) n FROM '+t).n,0);assert.equal(scalar(db,"SELECT revision FROM dataset_epochs WHERE mode='live'").revision,0);}finally{done(db);}}
});

test('missing error codes stay blocked through pause/resume and automatic dispatch',async()=>{
 for(const error of [null,'']){const db=setup();try{const id=await create();db.prepare("UPDATE collection_sessions SET state='error',error=? WHERE id=?").run(error,id);await assert.rejects(svc.sessionStep(id),/REVIEW_REQUIRED/);await svc.sessionState(id,'paused');await svc.sessionState(id,'ready');assert.equal(state(db,id).error,'COLLECTION_ERROR_UNCLASSIFIED');await assert.rejects(svc.sessionStep(id),/REVIEW_REQUIRED/);assert.equal((await svc.automaticTick()).performed,false);assert.equal(fetches,0);}finally{done(db);}}
});

test('multi-day completed snapshot also checks an earlier completed day',async()=>{
 const db=setup();try{total=1;const c=svc.config(),from=new Date(Date.parse(c.to+'T00:00:00Z')-86400000).toISOString().slice(0,10);const id=(await svc.createSession('live',from,c.to)).session.id;await svc.sessionStep(id);await svc.sessionStep(id);assert.equal((await svc.collectionRecovery(id)).pageCommitState,'SESSION_COMPLETE');db.prepare('UPDATE day_checkpoints SET fetched=99 WHERE session_id=? AND day=?').run(id,c.to);const r=await svc.collectionRecovery(id);assert.equal(r.pageCommitState,'REVIEW_REQUIRED');assert.equal(r.metadataConsistent,false);}finally{done(db);}
});
