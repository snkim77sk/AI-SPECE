/** Read-only projection of existing server-sync APIs. No model/provider calls. */
import {projectServerHealth} from './server-health.mjs';
const count=v=>Number.isSafeInteger(v)&&v>=0?v:null;
export function projectProgramLink(status,sync,now=Date.now(),options={}){
 const snap=status?.snapshot;
 if(!snap?.payload||!Array.isArray(snap.payload.sources))throw Error('STATUS_UNAVAILABLE');
 const health=projectServerHealth(status,sync,now,options);
 return {kind:'rules',modelConnected:false,modelCalls:0,observedAt:snap.payload.observed_at,receivedAt:snap.received_at,digest:snap.digest,
 stale:!health.report.fresh,reportKind:health.report.kind,
 sources:health.sources.map(h=>{const x=snap.payload.sources.find(x=>x.source===h.source);return {source:h.source,available:x?.available===true,
 attempted:x?.available===true?count(x.attempted):null,limit:count(x?.limit),verifiedRows:x?.available===true?count(x.verified_rows):null,
 state:typeof x?.task?.state==='string'?x.task.state:'미확인',nextRun:h.nextRun,
 reason:h.reason,rawReason:h.rawReason,held:Array.isArray(x?.held)?x.held.length:null,
 statusLabel:h.label,statusTone:h.tone,taskEnabled:h.taskEnabled,taskResult:h.taskResult,retryCount:h.retryCount,
 runReportedAt:h.runReportedAt,runFinishedAt:h.runFinishedAt,verifiedPages:h.verifiedPages,syncedAt:h.syncedAt};}),
 datasets:Array.isArray(sync?.sources)?sync.sources.map(x=>({dataset:x.dataset,pages:count(x.pages),sourceRows:count(x.source_rows),matchedRows:count(x.matched_rows),receivedAt:x.received_at})):null};
}
