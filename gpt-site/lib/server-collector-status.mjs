import {digest,stableJson} from './budget-domain.mjs';
import {kstDate,validDay} from './collector.mjs';
export const STATUS_PROTOCOL='windows-collector-status-v1';
export const STATUS_MAX_BYTES=16384;
const object=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);
const integer=x=>Number.isSafeInteger(x)&&x>=0;
const timestamp=x=>typeof x==='string'&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(x)&&Number.isFinite(Date.parse(x));
const codes=new Set('VERIFIED FINISHED PAGE_LIMIT DEADLINE_MARGIN NO_READY_SCOPES LOCAL_QUOTA_WAIT DAILY_QUOTA_WAIT_FOR_KST_RESET BUDGET_EXHAUSTED TIMEOUT_NO_RETRY MEMORY_LIMIT_REACHED RUN_ENDED_WITHOUT_RESULT RUN_RESULT_PENDING STATUS_UNAVAILABLE ERROR_RECHECK_NEXT_SCHEDULE CONTINUING_WITH_SCOPE_HOLD ORIGINAL_UNCERTAIN_PAGE_HOLD UNCERTAIN_SCOPE_NO_AUTOMATIC_RETRY UNCERTAIN_NO_RETRY REPEATED_OR_OVERLAPPING_PAGE SOURCE_SCHEMA_ERROR SOURCE_NETWORK_OR_TIMEOUT'.split(' '));
codes.add('SOURCE_RESPONSE_ERROR');
function exact(x,keys){if(!object(x)||Object.keys(x).length!==keys.length||keys.some(k=>!Object.hasOwn(x,k)))throw Error('STATUS_INVALID_FIELDS');}
function nullableStamp(x){if(x!==null&&!timestamp(x))throw Error('STATUS_INVALID_TIME');}
const scope=x=>typeof x==='string'&&/^(?:(?:QWGJK|AIDFA):)?(?:\d{4}-\d{2}-\d{2}|\d{4}|ALL)$/.test(x);
export async function prepareCollectorStatus(p,now=Date.now()){
 exact(p,['protocol','version','observed_at','day','reset_at','sources']);
 if(p.protocol!==STATUS_PROTOCOL||p.version!=='5.2.8'||!timestamp(p.observed_at)||Date.parse(p.observed_at)>now+120000||!validDay(p.day)||kstDate(new Date(p.observed_at))!==p.day||!timestamp(p.reset_at)||Date.parse(p.reset_at)!==Date.parse(p.day+'T15:00:00.000Z')||!Array.isArray(p.sources)||p.sources.length!==2)throw Error('STATUS_INVALID_SNAPSHOT');
 if(p.sources.map(s=>s.source).join(',')!=='g2b,lofin')throw Error('STATUS_INVALID_SOURCES');
 for(const s of p.sources){
  exact(s,['source','available','limit','counted','attempted','pending','confirmed','stored','verified','verified_rows','official_remaining','task','last_run','held','progress']);
  if(typeof s.available!=='boolean'||s.official_remaining!==null||s.limit!==null&&(!integer(s.limit)||s.limit<1||s.limit>10000))throw Error('STATUS_INVALID_QUOTA');
  for(const k of ['counted','attempted','pending','confirmed','stored','verified','verified_rows'])if(s.available?!integer(s[k]):s[k]!==null)throw Error('STATUS_INVALID_COUNTS');
  if(s.available&&(s.attempted>s.counted||s.pending!==s.counted-s.attempted||s.confirmed>s.attempted||s.stored>s.confirmed||s.verified>s.stored))throw Error('STATUS_INVALID_COUNTS');
  if(s.task!==null){
   exact(s.task,['state','enabled','last_run_at','next_run_at','last_result']);
   if(!['Unknown','Disabled','Queued','Ready','Running'].includes(s.task.state)||typeof s.task.enabled!=='boolean'||!integer(s.task.last_result))throw Error('STATUS_INVALID_TASK');
   nullableStamp(s.task.last_run_at);nullableStamp(s.task.next_run_at);
  }
  if(s.last_run!==null){
   exact(s.last_run,['run_id','reported_at','finished_at','reason','result_complete','attempted','stored','verified']);
   const r=s.last_run;if(r.run_id!==null&&(typeof r.run_id!=='string'||!/^[A-Za-z0-9_-]{1,80}$/.test(r.run_id))||!codes.has(r.reason)||typeof r.result_complete!=='boolean'||!integer(r.attempted)||!integer(r.stored)||!integer(r.verified)||r.verified>r.stored||r.stored>r.attempted)throw Error('STATUS_INVALID_RUN');
   nullableStamp(r.reported_at);nullableStamp(r.finished_at);
  }
  if(!Array.isArray(s.held)||s.held.length>12||!Array.isArray(s.progress)||s.progress.length>2)throw Error('STATUS_INVALID_SCOPES');
  for(const h of s.held){exact(h,['scope','next_page','saved','reason']);if(!scope(h.scope)||!integer(h.next_page)||h.next_page<1||h.saved!==null&&!integer(h.saved)||!codes.has(h.reason))throw Error('STATUS_INVALID_SCOPES');}
  for(const h of s.progress){exact(h,['scope','next_page','saved']);if(!scope(h.scope)||!integer(h.next_page)||h.next_page<1||!integer(h.saved))throw Error('STATUS_INVALID_SCOPES');}
 }
 const text=stableJson(p);if(new TextEncoder().encode(text).length>STATUS_MAX_BYTES)throw Error('STATUS_TOO_LARGE');
 return {payload:p,text,digest:await digest(text)};
}
export function collectorStatusPlan(v,receivedAt){return {
 sql:"INSERT INTO server_collector_status(id,observed_at,payload_json,digest,received_at) VALUES('windows-primary',?,?,?,?) ON CONFLICT(id) DO UPDATE SET observed_at=excluded.observed_at,payload_json=excluded.payload_json,digest=excluded.digest,received_at=excluded.received_at WHERE excluded.observed_at>server_collector_status.observed_at",
 params:[v.payload.observed_at,v.text,v.digest,receivedAt],
};}
export function collectorStatusView(snapshot,now=Date.now()){
 const p=snapshot?.payload,day=kstDate(new Date(now)),age=p?Math.max(0,now-Date.parse(p.observed_at)):Infinity;
 const fresh=!!p&&p.day===day&&age<=20*60*1000;
 return {day,fresh,ageMinutes:Number.isFinite(age)?Math.floor(age/60000):null,sources:(p?.sources||[]).map(s=>{
  const known=fresh&&s.available&&s.limit!==null;
  return {...s,known,remaining:known?Math.max(0,s.limit-s.counted):null,
   lastRemaining:s.available&&s.limit!==null?Math.max(0,s.limit-s.counted):null,
   quotaState:!known?'UNKNOWN':s.counted>=s.limit?'EXHAUSTED':'AVAILABLE',
   runningObserved:s.task?.state==='Running'&&age<=120000,
  };
 })};
}
const labels={
 TIMEOUT_NO_RETRY:'서버 실행시간 초과',MEMORY_LIMIT_REACHED:'서버 메모리 제한',
 RUN_ENDED_WITHOUT_RESULT:'최근 실행 종료 · 완료 기록 없음',RUN_RESULT_PENDING:'최근 실행 결과 대기',
 STATUS_UNAVAILABLE:'상세 사유 미확인',ERROR_RECHECK_NEXT_SCHEDULE:'최근 회차 오류 · 다음 예약에서 다시 점검',
 CONTINUING_WITH_SCOPE_HOLD:'일부 범위 보류 · 다른 범위 계속 수집',
 ORIGINAL_UNCERTAIN_PAGE_HOLD:'이전 요청 응답 불명 · 해당 페이지 보류',
 UNCERTAIN_SCOPE_NO_AUTOMATIC_RETRY:'응답·저장 확인 필요 · 해당 날짜 보류',
 UNCERTAIN_NO_RETRY:'응답 확인 필요 · 해당 범위 보류',
 REPEATED_OR_OVERLAPPING_PAGE:'원천 페이지 중복·겹침',SOURCE_SCHEMA_ERROR:'원천 응답 형식 오류',
 SOURCE_RESPONSE_ERROR:'원천 응답 검증 오류',
 SOURCE_NETWORK_OR_TIMEOUT:'원천 응답 지연·연결 오류',VERIFIED:'저장·재조회 확인',FINISHED:'회차 종료',
 PAGE_LIMIT:'회차당 페이지 제한 도달',DEADLINE_MARGIN:'다음 요청에 필요한 시간 부족',
 NO_READY_SCOPES:'현재 처리할 범위 없음',LOCAL_QUOTA_WAIT:'설정된 일일 한도 소진',
 DAILY_QUOTA_WAIT_FOR_KST_RESET:'설정된 일일 한도 소진 · 자정 대기',BUDGET_EXHAUSTED:'호출 예산 한도 도달',
};
export const collectorReason=code=>labels[code]||'상세 사유 미확인';
