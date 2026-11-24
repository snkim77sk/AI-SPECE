import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync,readdirSync} from 'node:fs';
import {prepareCollectorStatus,collectorStatusPlan,collectorStatusView,collectorReason,collectorRunReason,STATUS_PROTOCOL} from '../lib/server-collector-status.mjs';
import {readSyncBody} from '../lib/server-sync.mjs';
const now=Date.parse('2026-11-15T08:00:00.000Z');
const source=id=>({source:id,available:true,limit:id==='g2b'?950:100,counted:id==='g2b'?57:67,attempted:id==='g2b'?57:67,pending:0,confirmed:55,stored:55,verified:55,verified_rows:50000,official_remaining:null,task:{state:'Ready',enabled:true,last_run_at:'2026-11-15T07:50:00.000Z',next_run_at:'2026-11-15T08:05:00.000Z',last_result:1},last_run:{run_id:'scheduled-20261115T1645',reported_at:'2026-11-15T07:51:25.000Z',finished_at:'2026-11-15T07:51:25.000Z',reason:'TIMEOUT_NO_RETRY',result_complete:true,attempted:0,stored:0,verified:0},held:[],progress:[]});
const payload=()=>({protocol:STATUS_PROTOCOL,version:'5.2.8',observed_at:'2026-11-15T08:00:00.000Z',day:'2026-11-15',reset_at:'2026-11-15T15:00:00.000Z',sources:[source('g2b'),source('lofin')]});
test('actual remaining is independent from last run failure; zero means exhausted',async()=>{
 const p=payload();p.sources[1].progress=[{scope:'AIDFA:2026',next_page:13,saved:11480}];await prepareCollectorStatus(p,now);let v=collectorStatusView({payload:p},now);
 assert.deepEqual(v.sources.map(s=>s.remaining),[893,33]);assert.ok(v.sources.every(s=>s.quotaState==='AVAILABLE'));
 assert.equal(collectorReason(p.sources[1].last_run.reason),'서버 실행시간 초과');
 p.sources[1].counted=100;p.sources[1].pending=33;v=collectorStatusView({payload:p},now);assert.equal(v.sources[1].remaining,0);assert.equal(v.sources[1].quotaState,'EXHAUSTED');
});
test('stale, next KST day and missing ledger never imply a fresh remaining count',()=>{
 const p=payload();assert.equal(collectorStatusView(null,now).fresh,false);
 for(const clock of [now+21*60000,Date.parse('2026-11-15T15:00:00Z')]){
  const v=collectorStatusView({payload:p},clock);assert.ok(v.sources.every(s=>s.remaining===null&&s.quotaState==='UNKNOWN'));
 }
 p.sources[0].available=false;p.sources[0].counted=null;assert.equal(collectorStatusView({payload:p},now).sources[0].remaining,null);
});
test('RUNNING text alone cannot be displayed as an active Windows process',()=>{
 const p=payload();p.sources[0].last_run.reason='RUN_RESULT_PENDING';
 assert.equal(collectorStatusView({payload:p},now).sources[0].runningObserved,false);
 p.sources[0].task.state='Running';assert.equal(collectorStatusView({payload:p},now).sources[0].runningObserved,true);
 assert.equal(collectorStatusView({payload:p},now+180000).sources[0].runningObserved,false);
});
test('five-minute boundary or failed refresh removes current quota and running claims',()=>{
 const p=payload();p.sources[0].task.state='Running';
 assert.equal(collectorStatusView({payload:p},now+299999).fresh,true);
 assert.equal(collectorStatusView({payload:p},now+300000).fresh,false);
 const v=collectorStatusView({payload:p},now,false);
 assert.equal(v.fresh,false);assert.equal(v.sources[0].remaining,null);assert.equal(v.sources[0].runningObserved,false);
 assert.equal(v.sources[0].lastRemaining,893);
});
test('status validation rejects secrets, impossible counts, provider claims and wrong day',async()=>{
 for(const mutate of [
  p=>p.sources[0].api_key='secret',
  p=>p.sources[0].counted=1,
  p=>p.sources[0].official_remaining=999,
  p=>p.day='2026-11-14',
  p=>p.reset_at='2026-11-16T15:00:00.000Z',
  p=>p.sources[0].last_run.reason='RAW_SECRET',
  p=>p.observed_at='2026-11-15T09:00:00.000Z',
 ]){
  const p=payload();mutate(p);await assert.rejects(prepareCollectorStatus(p,now),/STATUS_/);
 }
});
test('out of order snapshot cannot replace newer status or mutate provider quota tables',async()=>{
 const db=new DatabaseSync(':memory:');
 for(const f of readdirSync(new URL('../drizzle/',import.meta.url)).filter(f=>f.endsWith('.sql')).sort())db.exec(readFileSync(new URL('../drizzle/'+f,import.meta.url),'utf8'));
 const p=payload(),v=await prepareCollectorStatus(p,now),q=collectorStatusPlan(v,p.observed_at);
 db.prepare(q.sql).run(...q.params);db.prepare(q.sql).run(...q.params);
 const older={...p,observed_at:'2026-11-15T07:00:00.000Z'},oq=collectorStatusPlan(await prepareCollectorStatus(older,now),p.observed_at);
 db.prepare(oq.sql).run(...oq.params);
 assert.equal(db.prepare('SELECT digest FROM server_collector_status').get().digest,v.digest);
 for(const t of ['usage','source_usage','server_sync_pages','delivery_rows','budget_rows'])assert.equal(db.prepare('SELECT COUNT(*) n FROM '+t).get().n,0);
 db.close();
});
test('compressed telemetry cannot exceed its separate 16 KiB limit',async()=>{
 const stream=new Blob(['x'.repeat(17000)]).stream().pipeThrough(new CompressionStream('gzip'));
 const r=new Request('https://example.test/api/server-status',{method:'POST',body:stream,duplex:'half',headers:{'Content-Type':'application/json','Content-Encoding':'gzip'}});
 await assert.rejects(readSyncBody(r,16384),/SYNC_BODY_TOO_LARGE/);
});
test('delayed retries expose due times without changing quota or accepting arbitrary data',async()=>{
 const p=payload();p.version='5.2.9';for(const s of p.sources){s.retry_enabled=true;s.retries=[];}
 p.sources[0].retries=[{scope:'2026-11-12',next_page:14,failures:2,next_retry_at:'2026-11-15T08:30:00.000Z',state:'WAITING'}];
 await prepareCollectorStatus(p,now);const v=collectorStatusView({payload:p},now);
 assert.equal(v.sources[0].remaining,893);assert.equal(v.sources[0].retries[0].failures,2);
 p.sources[0].retries[0].secret='invalid';await assert.rejects(prepareCollectorStatus(p,now),/STATUS_/);
 delete p.sources[0].retries[0].secret;p.sources[0].retries[0].next_retry_at='later';await assert.rejects(prepareCollectorStatus(p,now),/STATUS_/);
});

test('explicit repeated-source review survives status ingestion without inventing retries',async()=>{
 const p=payload();p.version='5.2.9';for(const s of p.sources){s.retry_enabled=true;s.retries=[];}
 p.sources[0].last_run.reason='NO_READY_SCOPES';
 p.sources[0].held=[
  {scope:'2026-11-12',next_page:14,saved:12987,reason:'SOURCE_OVERLAP_REVIEW_REQUIRED'},
  {scope:'2026-11-06',next_page:11,saved:9990,reason:'REPEATED_OR_OVERLAPPING_PAGE'},
 ];
 const original=JSON.stringify(p);
 const prepared=await prepareCollectorStatus(p,now);
 const v=collectorStatusView({payload:JSON.parse(prepared.text)},now);
 assert.equal(v.sources[0].sourceReviewCount,1);
 assert.deepEqual(v.sources[0].retries,[]);
 assert.equal(v.sources[0].remaining,893);
 assert.match(collectorReason(v.sources[0].held[0].reason),/자동 재시도 중지.*원천 확인 필요/);
 assert.match(collectorRunReason(v.sources[0]),/보류 2개/);
 assert.equal(JSON.stringify(p),original);
});

test('older overlap reports and completed empty runs do not imply a retry cap',async()=>{
 const p=payload();p.sources[0].last_run.reason='NO_READY_SCOPES';
 p.sources[0].held=[{scope:'2026-11-12',next_page:14,saved:12987,reason:'REPEATED_OR_OVERLAPPING_PAGE'}];
 await prepareCollectorStatus(p,now);
 assert.equal(collectorStatusView({payload:p},now).sources[0].sourceReviewCount,0);
 assert.doesNotMatch(collectorReason(p.sources[0].held[0].reason),/자동 재시도 중지/);
 p.sources[0].held=[];
 assert.equal(collectorRunReason(p.sources[0]),collectorReason('NO_READY_SCOPES'));
 assert.doesNotMatch(collectorRunReason(p.sources[0]),/보류|완료/);
});
