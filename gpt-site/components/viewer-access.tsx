'use client';

import {createContext,useContext,useEffect,useMemo,useState} from 'react';
import {usePathname,useSearchParams} from 'next/navigation';
import type {AccountResponse} from '../lib/public-auth-response';
import {fetchViewerAccount,viewerCapabilities,viewerHref,type ViewerCapabilities} from '../lib/viewer-access';

type Access=ViewerCapabilities&{loading:boolean;publicMembership:boolean;error:string;href:(target:string)=>string};
const closed:Access={canRead:false,canManage:false,isAdministrator:false,preview:false,loading:true,publicMembership:true,error:'',href:target=>target};
const Context=createContext<Access>(closed);
export const useViewerAccess=()=>useContext(Context);

export function ViewerAccessProvider({children,publicMembership}:{children:React.ReactNode;publicMembership:boolean}){
  const pathname=usePathname()||'/';
  const search=useSearchParams();
  const requested=search?.getAll('viewer').includes('member')===true;
  const laboratory=pathname==='/agent-lab';
  const [state,setState]=useState<{path:string;account:AccountResponse|null;loading:boolean;error:string}>({path:'',account:null,loading:publicMembership,error:''});
  useEffect(()=>{
    if(!publicMembership||laboratory)return;
    const abort=new AbortController();
    setState({path:pathname,account:null,loading:true,error:''});
    fetchViewerAccount(fetch,abort.signal).then(account=>{
      if(!abort.signal.aborted)setState({path:pathname,account,loading:false,error:''});
    }).catch(error=>{
      if(!abort.signal.aborted)setState({path:pathname,account:null,loading:false,error:error?.message==='VIEWER_ACCESS_TIMEOUT'?'15초 안에 이용 권한을 확인하지 못했습니다. 새로고침해 주세요.':'이용 권한을 확인하지 못했습니다. 새로고침해 주세요.'});
    });
    return()=>abort.abort();
  },[publicMembership,pathname,laboratory]);
  const current=state.path===pathname;
  const capabilities=viewerCapabilities(publicMembership,current?state.account:null,requested,laboratory);
  const value=useMemo<Access>(()=>({...capabilities,
    loading:publicMembership&&!laboratory&&(!current||state.loading),publicMembership,error:current?state.error:'',
    href:target=>viewerHref(target,capabilities.preview),
  }),[capabilities.canRead,capabilities.canManage,capabilities.isAdministrator,capabilities.preview,publicMembership,laboratory,current,state.loading,state.error]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
