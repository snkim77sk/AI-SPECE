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

const cancelled=()=>Object.assign(new Error('RUNTIME_INFO_CANCELLED'),{name:'AbortError'});

/** Bound both the request and JSON read; navigation cannot apply a stale version response. */
export async function fetchRuntimeInfo(fetcher,signal,timeoutMs=15000){
 if(signal?.aborted)throw cancelled();
 const controller=new AbortController();
 let timer,cancel;
 const interrupted=new Promise((_,reject)=>{
  cancel=()=>{controller.abort();reject(cancelled());};
  signal?.addEventListener('abort',cancel,{once:true});
  timer=setTimeout(()=>{controller.abort();reject(new Error('RUNTIME_INFO_TIMEOUT'));},timeoutMs);
 });
 const read=async()=>{
  const response=await fetcher('/api/runtime-info',{cache:'no-store',credentials:'same-origin',signal:controller.signal,headers:{Accept:'application/json'}});
  if(!response.ok)throw Error(response.status===401||response.status===403?'RUNTIME_INFO_ACCESS_REQUIRED':'RUNTIME_INFO_UNAVAILABLE');
  return parseRuntimeInfo(await response.json());
 };
 try{return await Promise.race([read(),interrupted]);}
 finally{clearTimeout(timer);signal?.removeEventListener('abort',cancel);}
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
