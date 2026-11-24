/** Read-only display of the existing Windows report and Site import receipts. */
import {collectorReason,SERVER_REPORT_FRESH_MS} from './server-collector-status.mjs';
import {kstDate} from './collector.mjs';

const count=value=>Number.isSafeInteger(value)&&value>=0?value:null;
const stamp=value=>typeof value==='string'&&Number.isFinite(Date.parse(value))?value:null;
const failures=new Set('TIMEOUT_NO_RETRY MEMORY_LIMIT_REACHED RUN_ENDED_WITHOUT_RESULT ERROR_RECHECK_NEXT_SCHEDULE SOURCE_SCHEMA_ERROR SOURCE_NETWORK_OR_TIMEOUT SOURCE_RESPONSE_ERROR'.split(' '));
const holds=new Set('CONTINUING_WITH_SCOPE_HOLD ORIGINAL_UNCERTAIN_PAGE_HOLD UNCERTAIN_SCOPE_NO_AUTOMATIC_RETRY UNCERTAIN_NO_RETRY REPEATED_OR_OVERLAPPING_PAGE'.split(' '));
const quotaWait=new Set('LOCAL_QUOTA_WAIT DAILY_QUOTA_WAIT_FOR_KST_RESET BUDGET_EXHAUSTED'.split(' '));
const finished=new Set('VERIFIED FINISHED PAGE_LIMIT DEADLINE_MARGIN NO_READY_SCOPES'.split(' '));
const datasetSource={shopping_delivery:'g2b',budget:'lofin',budget_appropriation:'lofin'};
const latest=values=>values.map(stamp).filter(Boolean).sort((a,b)=>Date.parse(a)-Date.parse(b)).at(-1)||null;
const sum=(rows,key)=>{
 const values=rows.map(row=>count(row[key]));
 if(values.some(value=>value===null))return null;
 const total=values.reduce((value,next)=>value+next,0);
 return Number.isSafeInteger(total)?total:null;
};

export function serverReportTiming(snapshot,now=Date.now()){
 const observedAt=stamp(snapshot?.payload?.observed_at),age=observedAt?now-Date.parse(observedAt):NaN;
 const day=Number.isFinite(now)?kstDate(new Date(now)):null;
 const previousDay=typeof snapshot?.payload?.day==='string'&&snapshot.payload.day!==day;
 const kind=!observedAt?'missing':!Number.isFinite(age)||age< -60000?'invalid':previousDay?'previous-day':age>=SERVER_REPORT_FRESH_MS?'stale':'current';
 return {kind,fresh:kind==='current',observedAt,ageMinutes:Number.isFinite(age)?Math.floor(Math.max(0,age)/60000):null};
}

function sourceHealth(source,timing){
 const run=source?.last_run,task=source?.task,rawReason=typeof run?.reason==='string'?run.reason:'UNKNOWN';
 const held=Array.isArray(source?.held)?source.held.length:0;
 const retries=Array.isArray(source?.retries)?source.retries:[];
 const taskResult=count(task?.last_result),verifiedPages=count(run?.verified);
 const running=timing.fresh&&timing.ageMinutes!==null&&timing.ageMinutes<2&&task?.state==='Running';
 const resultIssue=taskResult!==null&&taskResult!==0&&task?.state!=='Running';
 const error=failures.has(rawReason),partial=held>0||retries.length>0||holds.has(rawReason);
 let state='unknown',tone='warning',label='최근 결과 미확인';
 if(!timing.fresh)label='현재 수집 상태 미확인';
 else if(!source||source.available!==true)label='수집 보고 미확인';
 else if(task?.enabled===false||task?.state==='Disabled'){state='paused';label='예약 수집 중지';}
 else if(error){state='error';tone='error';label=running?'실행 관측 · 오류 기록 있음':'최근 회차 오류';}
 else if(resultIssue){state='warning';label='최근 종료 코드 확인';}
 else if(partial){state='partial';label=running?'실행 관측 · 일부 보류':'일부 범위 보류';}
 else if(running){state='running';tone='info';label='보고 당시 수집 실행 중';}
 else if(quotaWait.has(rawReason)){state='waiting';tone='info';label='설정 한도 대기';}
 else if(run?.result_complete===true&&verifiedPages!==null&&verifiedPages>0&&finished.has(rawReason)){state='verified';tone='verified';label='최근 회차 페이지 검증 확인';}
 else if(run?.result_complete===true&&finished.has(rawReason)){state='waiting';tone='info';label='다음 예약 대기';}
 return {state,tone,label,rawReason,reason:collectorReason(rawReason),held,retryCount:retries.length,
  runningObserved:running,taskResult,taskEnabled:typeof task?.enabled==='boolean'?task.enabled:null,
  runReportedAt:stamp(run?.reported_at),runFinishedAt:stamp(run?.finished_at),
  verifiedPages:run?.result_complete===true?verifiedPages:null,
  // No historical last-success timestamp exists in protocol 5.2.9. A report
  // that verified some pages can still have failed later in the same run.
  nextRun:stamp(task?.next_run_at)};
}

export function projectServerHealth(status,sync,now=Date.now(),{reportFetchFailed=false}={}){
 const snapshot=status?.snapshot,reportedTiming=serverReportTiming(snapshot,now);
 const timing=reportFetchFailed?{...reportedTiming,kind:'unavailable',fresh:false}:reportedTiming;
 const reports=Array.isArray(snapshot?.payload?.sources)?snapshot.payload.sources:[];
 const datasets=Array.isArray(sync?.sources)?sync.sources:null;
 const sources=['g2b','lofin'].map(id=>{
  const source=reports.find(item=>item.source===id),receipts=datasets?.filter(item=>datasetSource[item.dataset]===id)||[];
  return {source:id,name:id==='g2b'?'나라장터':'지방재정365',...sourceHealth(source,timing),
   syncedAt:latest(receipts.map(item=>item.received_at)),sourceRows:datasets?sum(receipts,'source_rows'):null};
 });
 return {report:timing,sources,totalSourceRows:datasets?sum(datasets,'source_rows'):null,
  receivedAt:datasets?latest(datasets.map(item=>item.received_at)):null,
  hasSourceErrors:timing.fresh&&sources.some(source=>source.state==='error'),
  needsReview:timing.fresh&&sources.some(source=>['error','warning','partial','paused','unknown'].includes(source.state))};
}
