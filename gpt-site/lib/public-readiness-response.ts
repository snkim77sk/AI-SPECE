export type ProviderId='kakao'|'naver'|'google';
export type SignInAvailability={
 schema:'g2b-signin-availability-v1';checkedAt:string;
 state:'disabled'|'available'|'setup-required'|'unavailable';
 providers:{id:ProviderId;available:boolean}[];
};
export type ReleaseReadiness={
 schema:'g2b-release-readiness-v1';checkedAt:string;enabled:boolean;
 configuration:{httpsOrigin:boolean;sessionSecret:boolean;serviceSyncToken:boolean};
 identityStorage:{schema:'ready'|'missing'|'unknown';approvedAdminPresent:boolean|null};
 providers:{id:ProviderId;configured:boolean;loginAvailable:boolean;realLogin:'unverified'}[];
 approvalFlow:'unverified';windowsServiceCompatibility:'unverified';publicRelease:'unverified';
};
const ids:ProviderId[]=['kakao','naver','google'];
const record=(value:unknown):value is Record<string,unknown>=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const timestamp=(value:unknown):value is string=>typeof value==='string'&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value)&&Number.isFinite(Date.parse(value))&&new Date(value).toISOString()===value;
const providerId=(value:unknown):value is ProviderId=>typeof value==='string'&&ids.includes(value as ProviderId);
function providerRows(value:unknown):Record<string,unknown>[] {
 if(!Array.isArray(value)||value.length!==ids.length||value.some(row=>!record(row)||!providerId(row.id))
  ||new Set(value.map(row=>row.id)).size!==ids.length)throw Error('READINESS_INVALID_RESPONSE');
 return value;
}

export function parseSignInAvailability(value:unknown):SignInAvailability{
 if(!record(value)||value.schema!=='g2b-signin-availability-v1'||!timestamp(value.checkedAt)
  ||!['disabled','available','setup-required','unavailable'].includes(String(value.state)))throw Error('READINESS_INVALID_RESPONSE');
 const providers=providerRows(value.providers).map(row=>{
  if(typeof row.available!=='boolean')throw Error('READINESS_INVALID_RESPONSE');
  return {id:row.id as ProviderId,available:row.available};
 });
 if((value.state==='available')!==providers.some(provider=>provider.available))throw Error('READINESS_INVALID_RESPONSE');
 return {schema:'g2b-signin-availability-v1',checkedAt:value.checkedAt,state:value.state as SignInAvailability['state'],providers};
}

export function parseReleaseReadiness(value:unknown):ReleaseReadiness{
 if(!record(value)||value.schema!=='g2b-release-readiness-v1'||!timestamp(value.checkedAt)||typeof value.enabled!=='boolean'
  ||!record(value.configuration)||!record(value.identityStorage)
  ||value.approvalFlow!=='unverified'||value.windowsServiceCompatibility!=='unverified'||value.publicRelease!=='unverified')throw Error('READINESS_INVALID_RESPONSE');
 const config=value.configuration,storage=value.identityStorage;
 if(typeof config.httpsOrigin!=='boolean'||typeof config.sessionSecret!=='boolean'||typeof config.serviceSyncToken!=='boolean'
  ||!['ready','missing','unknown'].includes(String(storage.schema))
  ||!(storage.approvedAdminPresent===null||typeof storage.approvedAdminPresent==='boolean')
  ||storage.schema!=='ready'&&storage.approvedAdminPresent!==null)throw Error('READINESS_INVALID_RESPONSE');
 const baseAvailable=value.enabled&&config.httpsOrigin&&config.sessionSecret&&config.serviceSyncToken&&storage.schema==='ready';
 const providers=providerRows(value.providers).map(row=>{
  if(typeof row.configured!=='boolean'||typeof row.loginAvailable!=='boolean'||row.realLogin!=='unverified'
   ||row.loginAvailable!==(baseAvailable&&row.configured))throw Error('READINESS_INVALID_RESPONSE');
  return {id:row.id as ProviderId,configured:row.configured,loginAvailable:row.loginAvailable,realLogin:'unverified' as const};
 });
 return {schema:'g2b-release-readiness-v1',checkedAt:value.checkedAt,enabled:value.enabled,
  configuration:{httpsOrigin:config.httpsOrigin,sessionSecret:config.sessionSecret,serviceSyncToken:config.serviceSyncToken},
  identityStorage:{schema:storage.schema as ReleaseReadiness['identityStorage']['schema'],approvedAdminPresent:storage.approvedAdminPresent},
  providers,approvalFlow:'unverified',windowsServiceCompatibility:'unverified',publicRelease:'unverified'};
}

type Fetcher=(input:RequestInfo|URL,init?:RequestInit)=>Promise<Response>;
const cancelled=()=>Object.assign(new Error('READINESS_CANCELLED'),{name:'AbortError'});
async function fetchReadiness<T>(fetcher:Fetcher,url:string,parse:(value:unknown)=>T,signal:AbortSignal,timeoutMs:number):Promise<T>{
 if(signal.aborted)throw cancelled();
 const controller=new AbortController();
 let timer:ReturnType<typeof setTimeout>|undefined,cancel:()=>void=()=>{};
 const interrupted=new Promise<never>((_,reject)=>{
  cancel=()=>{reject(cancelled());controller.abort();};
  signal.addEventListener('abort',cancel,{once:true});
  timer=setTimeout(()=>{reject(Error('READINESS_TIMEOUT'));controller.abort();},timeoutMs);
 });
 const read=async()=>{
  const response=await fetcher(url,{cache:'no-store',credentials:'same-origin',headers:{Accept:'application/json'},signal:controller.signal});
  if(!response.ok)throw Error(response.status===401||response.status===403?'READINESS_ACCESS_REQUIRED':'READINESS_UNAVAILABLE');
  return parse(await response.json());
 };
 try{return await Promise.race([read(),interrupted]);}
 finally{clearTimeout(timer);signal.removeEventListener('abort',cancel);}
}
export const fetchSignInAvailability=(fetcher:Fetcher,signal:AbortSignal,timeoutMs=15000)=>fetchReadiness(fetcher,'/api/public-auth/readiness',parseSignInAvailability,signal,timeoutMs);
export const fetchReleaseReadiness=(fetcher:Fetcher,signal:AbortSignal,timeoutMs=15000)=>fetchReadiness(fetcher,'/api/release-readiness',parseReleaseReadiness,signal,timeoutMs);
export function readinessErrorMessage(error:unknown,owner=false){
 const code=error instanceof Error?error.message:'';
 if(code==='READINESS_ACCESS_REQUIRED')return owner?'관리자로 로그인한 뒤 준비 상태를 다시 확인해 주세요.':'로그인 준비 상태를 확인할 권한이 없습니다. 잠시 후 다시 시도해 주세요.';
 if(code==='READINESS_TIMEOUT')return '15초 안에 준비 상태를 확인하지 못했습니다. 다시 확인해 주세요.';
 return '준비 상태를 불러오지 못했습니다. 서비스가 꺼졌다는 뜻은 아닙니다. 다시 확인해 주세요.';
}
