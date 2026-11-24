import {accountResponse,type AccountResponse} from './public-auth-response';

export type ViewerCapabilities={canRead:boolean;canManage:boolean;isAdministrator:boolean;preview:boolean};
/** Presentation mirrors server roles; malformed or unresolved accounts stay closed. */
export function viewerCapabilities(publicMembership:boolean,account:AccountResponse|null,previewRequested=false,laboratory=false):ViewerCapabilities{
  if(laboratory)return {canRead:false,canManage:false,isAdministrator:false,preview:false};
  const approved=account?.authenticated===true&&account.member.status==='approved';
  const isAdministrator=!publicMembership||(approved&&account.member.role==='admin');
  const canRead=!publicMembership||approved;
  return {canRead,canManage:isAdministrator&&!previewRequested,isAdministrator,preview:isAdministrator&&previewRequested};
}

/** Carry a read-only preview through same-origin links and API requests only. */
export function viewerHref(target:string,preview:boolean){
  if(!preview||!target.startsWith('/')||target.startsWith('//')||target.includes('\\'))return target;
  const url=new URL(target,'https://g2b-view.invalid');
  url.searchParams.set('viewer','member');
  return url.pathname+url.search+url.hash;
}

export async function fetchViewerAccount(fetcher:typeof fetch,signal:AbortSignal,timeoutMs=15000):Promise<AccountResponse>{
  const controller=new AbortController();
  let timer:ReturnType<typeof setTimeout>|undefined;
  let cancel:()=>void=()=>{};
  const interrupted=new Promise<never>((_,reject)=>{
    cancel=()=>{controller.abort();reject(new DOMException('Cancelled','AbortError'));};
    if(signal.aborted)cancel();else signal.addEventListener('abort',cancel,{once:true});
    timer=setTimeout(()=>{controller.abort();reject(Error('VIEWER_ACCESS_TIMEOUT'));},timeoutMs);
  });
  const read=async()=>{
    if(controller.signal.aborted)throw new DOMException('Cancelled','AbortError');
    const response=await fetcher('/api/public-auth/me',{cache:'no-store',credentials:'same-origin',signal:controller.signal});
    if(!response.ok)throw Error('VIEWER_ACCESS_UNAVAILABLE');
    return accountResponse(await response.json());
  };
  try{return await Promise.race([interrupted,read()]);}
  finally{clearTimeout(timer);signal.removeEventListener('abort',cancel);}
}
