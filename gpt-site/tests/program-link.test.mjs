import test from 'node:test';import assert from 'node:assert/strict';import{projectProgramLink}from'../lib/program-link.mjs';
const now=Date.parse('2026-11-17T03:00:00Z');const payload={observed_at:new Date(now).toISOString(),sources:[{source:'g2b',available:true,attempted:950,limit:950,verified_rows:42,last_run:{reason:'DAILY_QUOTA_WAIT_FOR_KST_RESET'},task:{state:'Ready'},held:[]}]};
const status={snapshot:{payload,digest:'hash',received_at:new Date(now).toISOString()}};
test('actual status mapping stays non-AI',()=>{const v=projectProgramLink(status,{sources:[]},now);assert.equal(v.sources[0].attempted,950);assert.equal(v.modelConnected,false);assert.equal(v.kind,'rules');assert.match(v.sources[0].reason,/자정/);});
test('stale and future marked',()=>{assert.equal(projectProgramLink(status,{},now+300001).stale,true);assert.equal(projectProgramLink(status,{},now-60001).stale,true);});
test('unavailable is not zero',()=>{const v=projectProgramLink({snapshot:{payload:{...payload,sources:[{...payload.sources[0],available:false}]}}},{},now);assert.equal(v.sources[0].attempted,null);});
test('unknown reason and counts not fabricated',()=>{const v=projectProgramLink({snapshot:{payload:{...payload,sources:[{source:'g2b',available:true,attempted:-1,last_run:{reason:'NEW'}}]}}},{},now);assert.equal(v.sources[0].attempted,null);assert.match(v.sources[0].reason,/확인/);});
test('missing snapshot fails closed',()=>assert.throws(()=>projectProgramLink({},{}),/STATUS_UNAVAILABLE/));
test('source-specific error and last run evidence are distinct from Site receipt time',()=>{
 const p=structuredClone(payload);p.sources[0].last_run={reason:'TIMEOUT_NO_RETRY',result_complete:true,verified:0,reported_at:'2026-11-17T02:58:00.000Z'};p.sources[0].task={state:'Ready',enabled:true,last_result:1};
 const receiptTime='2026-11-17T02:59:00.000Z';
 const v=projectProgramLink({snapshot:{...status.snapshot,payload:p}},{sources:[{dataset:'shopping_delivery',pages:1,source_rows:10,matched_rows:1,received_at:receiptTime}]},now);
 assert.equal(v.sources[0].statusTone,'error');assert.equal(v.sources[0].verifiedPages,0);
 assert.equal(v.sources[0].runReportedAt,p.sources[0].last_run.reported_at);assert.equal(v.sources[0].syncedAt,receiptTime);
});
test('failed refresh keeps report evidence but removes current verified health',()=>{
 const v=projectProgramLink(status,{sources:[]},now,{reportFetchFailed:true});
 assert.equal(v.stale,true);assert.equal(v.reportKind,'unavailable');
 assert.equal(v.sources[0].statusTone,'warning');assert.equal(v.sources[0].attempted,950);
});
