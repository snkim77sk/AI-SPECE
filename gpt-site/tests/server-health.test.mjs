import test from 'node:test';
import assert from 'node:assert/strict';
import {projectServerHealth,serverReportTiming} from '../lib/server-health.mjs';

const now=Date.parse('2026-11-17T16:00:00.000Z');
const source=id=>({source:id,available:true,task:{state:'Ready',enabled:true,last_result:0,next_run_at:'2026-11-17T16:01:00.000Z'},last_run:{reason:'PAGE_LIMIT',result_complete:true,verified:8,reported_at:'2026-11-17T15:59:40.000Z',finished_at:'2026-11-17T15:59:39.000Z'},held:[],retries:[]});
const status=()=>({snapshot:{payload:{observed_at:new Date(now).toISOString(),sources:[source('g2b'),source('lofin')]}}});
const sync={sources:[
 {dataset:'shopping_delivery',source_rows:1000,received_at:'2026-11-17T15:59:50.000Z'},
 {dataset:'budget',source_rows:2000,received_at:'2026-11-17T15:58:10.000Z'},
 {dataset:'budget_appropriation',source_rows:300,received_at:'2026-11-17T15:58:20.000Z'},
]};

test('recent Site transfers never conceal LOFIN failure or G2B held scopes',()=>{
 const report=status(),[g2b,lofin]=report.snapshot.payload.sources;
 g2b.last_run.reason='STATUS_UNAVAILABLE';g2b.last_run.verified=0;g2b.held=[{}, {}, {}];
 lofin.last_run.reason='TIMEOUT_NO_RETRY';lofin.last_run.verified=0;lofin.task.last_result=1;
 const view=projectServerHealth(report,sync,now);
 assert.equal(view.receivedAt,'2026-11-17T15:59:50.000Z');
 assert.equal(view.totalSourceRows,3300);
 assert.deepEqual(view.sources.map(s=>s.state),['partial','error']);
 assert.equal(view.hasSourceErrors,true);
 assert.equal(view.sources[1].syncedAt,'2026-11-17T15:58:20.000Z');
 assert.equal(view.sources[1].sourceRows,2300);
});

test('Windows result zero without a known completed result remains unconfirmed',()=>{
 const report=status();report.snapshot.payload.sources[0].last_run={reason:'STATUS_UNAVAILABLE',result_complete:true,verified:0};
 const view=projectServerHealth(report,sync,now);
 assert.equal(view.sources[0].state,'unknown');
 assert.equal(view.sources[0].tone,'warning');
});

test('five-minute boundary invalidates current health without discarding receipt history',()=>{
 const report=status();
 assert.equal(serverReportTiming(report.snapshot,now+299999).fresh,true);
 const view=projectServerHealth(report,sync,now+300000);
 assert.equal(view.report.kind,'stale');
 assert.ok(view.sources.every(s=>s.state==='unknown'&&s.tone==='warning'));
 assert.equal(view.receivedAt,sync.sources[0].received_at);
 assert.equal(view.sources[0].verifiedPages,8);
});

test('failed status read removes verified display even while Site receipt read succeeds',()=>{
 const view=projectServerHealth(status(),sync,now+10000,{reportFetchFailed:true});
 assert.equal(view.report.kind,'unavailable');
 assert.equal(view.report.observedAt,new Date(now).toISOString());
 assert.ok(view.sources.every(s=>s.state==='unknown'&&s.tone==='warning'));
 assert.equal(view.totalSourceRows,3300);
});

test('crossing Korean midnight requires a new report even within five minutes',()=>{
 const report=status();report.snapshot.payload.observed_at='2026-11-17T14:59:40.000Z';report.snapshot.payload.day='2026-11-17';
 const view=projectServerHealth(report,sync,Date.parse('2026-11-17T15:00:00.000Z'));
 assert.equal(view.report.kind,'previous-day');
 assert.ok(view.sources.every(s=>s.state==='unknown'));
});

test('missing or implausibly future reports never assert that collection is running',()=>{
 for(const report of [null,{snapshot:{payload:{observed_at:'invalid',sources:[]}}}]){
  const view=projectServerHealth(report,sync,now);
  assert.equal(view.report.fresh,false);
  assert.ok(view.sources.every(s=>s.state==='unknown'));
 }
 assert.equal(serverReportTiming(status().snapshot,now-60001).kind,'invalid');
 const report=status();report.snapshot.payload.sources.pop();
 assert.equal(projectServerHealth(report,sync,now).sources[1].state,'unknown');
});

test('configured quota wait is distinct from failure and verified completion',()=>{
 const report=status(),g2b=report.snapshot.payload.sources[0];
 g2b.last_run.reason='DAILY_QUOTA_WAIT_FOR_KST_RESET';g2b.last_run.verified=0;
 const view=projectServerHealth(report,sync,now);
 assert.equal(view.sources[0].state,'waiting');
 assert.equal(view.sources[0].tone,'info');
 assert.match(view.sources[0].reason,/자정/);
});

test('running task status is not treated as a failed Windows exit',()=>{
 const report=status(),g2b=report.snapshot.payload.sources[0];
 g2b.task.state='Running';g2b.task.last_result=267009;
 g2b.last_run={reason:'RUN_RESULT_PENDING',result_complete:false,verified:0};
 const view=projectServerHealth(report,sync,now);
 assert.equal(view.sources[0].state,'running');
 assert.equal(view.sources[0].tone,'info');
 assert.equal(view.sources[0].verifiedPages,null);
});

test('partially verified failed run remains failed and invents no historical success timestamp',()=>{
 const report=status(),lofin=report.snapshot.payload.sources[1];
 lofin.last_run.reason='SOURCE_RESPONSE_ERROR';lofin.last_run.verified=3;
 const sourceView=projectServerHealth(report,sync,now).sources[1];
 assert.equal(sourceView.state,'error');
 assert.equal(sourceView.verifiedPages,3);
 assert.equal(sourceView.runReportedAt,lofin.last_run.reported_at);
 assert.equal(Object.hasOwn(sourceView,'lastSuccessAt'),false);
});

test('disabled schedule and access-review retries cannot be reported as healthy',()=>{
 const report=status();report.snapshot.payload.sources[0].task.enabled=false;
 report.snapshot.payload.sources[1].retries=[{state:'ACCESS_REVIEW'}];
 assert.deepEqual(projectServerHealth(report,sync,now).sources.map(s=>s.state),['paused','partial']);
});

test('missing counts are unknown while a confirmed empty receipt list is zero',()=>{
 assert.equal(projectServerHealth(status(),null,now).totalSourceRows,null);
 assert.equal(projectServerHealth(status(),{sources:[]},now).totalSourceRows,0);
 assert.equal(projectServerHealth(status(),{sources:[{source_rows:-1}]},now).totalSourceRows,null);
});
