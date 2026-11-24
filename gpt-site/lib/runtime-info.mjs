import {CODE_SNAPSHOT_SHA,SOURCE_VERSION,STAGES} from './owner-progress.mjs';

export const RUNTIME_INFO_SCHEMA='g2b-runtime-info-v1';
const version=value=>typeof value==='string'&&value.length<=64&&/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/.test(value);
const timestamp=value=>typeof value==='string'&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value)&&Number.isFinite(Date.parse(value))&&new Date(value).toISOString()===value;

/** The package version belongs to this Worker; a reviewed GitHub baseline is not its deployment SHA. */
export function runtimeInfo(appVersion,checkedAt=new Date().toISOString()){
 if(!version(appVersion)||!timestamp(checkedAt))throw Error('RUNTIME_INFO_UNAVAILABLE');
 return {
  schema:RUNTIME_INFO_SCHEMA,
  appVersion,
  checkedAt,
  sourceBaseline:{kind:'github-baseline',version:SOURCE_VERSION,commitSha:CODE_SNAPSHOT_SHA},
  deployedCommitSha:null,
 };
}

/** Accept only the app-version contract, never the independent server-sync protocol version. */
export function parseRuntimeInfo(value){
 const source=value?.sourceBaseline;
 if(!value||value.schema!==RUNTIME_INFO_SCHEMA||!version(value.appVersion)||!timestamp(value.checkedAt)
  ||source?.kind!=='github-baseline'||!version(source.version)||!/^([a-f0-9]{40})$/.test(source.commitSha??'')
  ||value.deployedCommitSha!==null)throw Error('RUNTIME_INFO_INVALID_RESPONSE');
 return {schema:RUNTIME_INFO_SCHEMA,appVersion:value.appVersion,checkedAt:value.checkedAt,
  sourceBaseline:{kind:'github-baseline',version:source.version,commitSha:source.commitSha},deployedCommitSha:null};
}

const cancelled=prefix=>Object.assign(new Error(prefix+'_CANCELLED'),{name:'AbortError'});

/** Each read owns its deadline, so a stalled receipt read cannot block runtime metadata. */
async function fetchBoundedInfo(fetcher,path,parse,signal,timeoutMs,prefix){
 if(signal?.aborted)throw cancelled(prefix);
 const controller=new AbortController();
 let timer,cancel;
 const interrupted=new Promise((_,reject)=>{
  cancel=()=>{reject(cancelled(prefix));controller.abort();};
  signal?.addEventListener('abort',cancel,{once:true});
  timer=setTimeout(()=>{reject(new Error(prefix+'_TIMEOUT'));controller.abort();},timeoutMs);
 });
 const read=async()=>{
  const response=await fetcher(path,{cache:'no-store',credentials:'same-origin',signal:controller.signal,headers:{Accept:'application/json'}});
  if(!response.ok)throw Error(prefix+(response.status===401||response.status===403?'_ACCESS_REQUIRED':'_UNAVAILABLE'));
  return parse(await response.json());
 };
 try{return await Promise.race([read(),interrupted]);}
 finally{clearTimeout(timer);signal?.removeEventListener('abort',cancel);}
}

/** Bound both the request and JSON read; navigation cannot apply a stale version response. */
export function fetchRuntimeInfo(fetcher,signal,timeoutMs=15000){
 return fetchBoundedInfo(fetcher,'/api/runtime-info',parseRuntimeInfo,signal,timeoutMs,'RUNTIME_INFO');
}

/** Keep only receipt fields used by this screen; incomplete metadata remains unknown. */
export function parseOwnerSyncInfo(value){
 if(!value||typeof value!=='object'||Array.isArray(value)||!Array.isArray(value.sources)||value.sources.length>32
  ||value.sources.some(source=>!source||typeof source!=='object'||Array.isArray(source)
   ||typeof source.dataset!=='string'||!source.dataset||source.dataset.length>64)
  ||new Set(value.sources.map(source=>source.dataset)).size!==value.sources.length)throw Error('OWNER_SYNC_INVALID_RESPONSE');
 const text=value=>typeof value==='string'&&value.length<=128?value:null;
 const count=value=>Number.isSafeInteger(value)&&value>=0?value:null;
 return {sources:value.sources.map(source=>({
  dataset:source.dataset,
  latest_scope:text(source.latest_scope),latest_nonempty_scope:text(source.latest_nonempty_scope),
  latest_source_status:text(source.latest_source_status),
  latest_saved_count:count(source.latest_saved_count),latest_source_total:count(source.latest_source_total),
  latest_scope_received_rows:count(source.latest_scope_received_rows),
  latest_scope_received_at:text(source.latest_scope_received_at),
 }))};
}

export function fetchOwnerSyncInfo(fetcher,signal,timeoutMs=15000){
 return fetchBoundedInfo(fetcher,'/api/server-sync',parseOwnerSyncInfo,signal,timeoutMs,'OWNER_SYNC');
}

export function ownerSyncErrorMessage(error){
 if(error?.message==='OWNER_SYNC_ACCESS_REQUIRED')return '운영 동기화 정보는 관리자만 볼 수 있습니다.';
 if(error?.message==='OWNER_SYNC_INVALID_RESPONSE')return '수신 상태 응답 형식을 확인할 수 없습니다.';
 if(error?.message==='OWNER_SYNC_TIMEOUT')return '15초 안에 운영 수신 기록 응답을 받지 못했습니다. 새로고침해 주세요.';
 return '운영 수신 영수증을 확인할 수 없습니다.';
}

export function runtimeErrorMessage(error){
 if(error?.message==='RUNTIME_INFO_ACCESS_REQUIRED')return '실행 버전은 관리자 로그인 후 확인할 수 있습니다.';
 if(error?.message==='RUNTIME_INFO_TIMEOUT')return '15초 안에 실행 버전 응답을 받지 못했습니다. 새로고침해 주세요.';
 return '실행 버전을 확인하지 못했습니다. 수집 자료와는 별도로 다시 조회해 주세요.';
}

/** Live evidence updates only its own stage; a Worker response cannot verify Windows collection. */
export function ownerProgressStages(runtime,sources=[]){
 let verified=null;
 try{verified=parseRuntimeInfo(runtime);}catch{}
 const known=Array.isArray(sources)?sources.filter(source=>['shopping_delivery','budget'].includes(source?.dataset)
  &&typeof source.checkedDay==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(source.checkedDay)
  &&Number.isSafeInteger(source.received)&&source.received>=0
  &&typeof source.receivedAt==='string'&&Number.isFinite(Date.parse(source.receivedAt))):[];
 const receivedDatasets=new Set(known.map(source=>source.dataset));
 return STAGES.map(stage=>{
  if(stage.id==='deploy'&&verified)return {...stage,status:'runtime-verified',detail:`실행 서버에서 v${verified.appVersion} 응답을 확인했습니다. 확인 시각은 위 카드에 표시합니다. GitHub 기준 커밋은 실제 배포 커밋과 별도입니다.`};
  if(stage.id==='source'&&receivedDatasets.size>0)return {...stage,status:receivedDatasets.size===2?'receipt-verified':'receipt-partial',detail:`계약·납품 / 상세 예산 중 ${receivedDatasets.size}개 자료의 수신 기록을 확인했습니다. 날짜·저장행·미수신행은 아래에서 확인하세요. 전체 수집 완료를 의미하지 않습니다.`};
  return stage;
 });
}
