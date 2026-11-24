'use client';
import {useEffect,useRef,useState} from 'react';
import {projectServerHealth} from '../lib/server-health.mjs';
import SourceAvailability from './source-availability';

type Status={sources:{dataset:string;pages:number;source_rows:number;matched_rows:number;received_at:string}[];epochs:{delivery:number;budget:number}};
const time=(value:string|null)=>value?new Date(value).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})+' KST':'미확인';

export default function ServerSyncStatus(){
 const [sync,setSync]=useState<Status|null>(null),[status,setStatus]=useState<any>(null);
 const [syncFailed,setSyncFailed]=useState(false),[statusFailed,setStatusFailed]=useState(false),[clock,setClock]=useState(Date.now());
 const revision=useRef<string|null>(null);
 useEffect(()=>{
  let stopped=false,busy=false;
  const read=async(path:string)=>{
   const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(10000)});
   if(!response.ok)throw Error();
   return response.json();
  };
  const poll=async()=>{
   if(stopped)return;
   setClock(Date.now());
   if(busy||document.hidden)return;
   busy=true;
   try{
    const [data,report]=await Promise.allSettled([read('/api/server-sync'),read('/api/server-status')]);
    if(stopped)return;
    setClock(Date.now());
    if(report.status==='fulfilled'&&report.value&&Object.hasOwn(report.value,'snapshot')){
     setStatus(report.value);setStatusFailed(false);
    }else setStatusFailed(true);
    if(data.status==='fulfilled'&&data.value!==null&&typeof data.value==='object'&&'sources' in data.value&&'epochs' in data.value&&Array.isArray(data.value.sources)&&data.value.epochs){
     const nextSync=data.value as Status;setSync(nextSync);setSyncFailed(false);
     const next=JSON.stringify(nextSync.epochs);
     if(revision.current!==null&&revision.current!==next&&!/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName||'')){
      window.location.reload();return;
     }
     if(revision.current===null)revision.current=next;
    }else setSyncFailed(true);
   }finally{busy=false;}
  };
  void poll();
  const timer=setInterval(poll,60000);
  document.addEventListener('visibilitychange',poll);
  return()=>{stopped=true;clearInterval(timer);document.removeEventListener('visibilitychange',poll);};
 },[]);
 const health=projectServerHealth(status,sync,clock,{reportFetchFailed:statusFailed});
 const reportWarning=statusFailed?'서버 수집 상태를 새로 읽지 못했습니다. 아래 내용은 마지막 보고입니다.':
  health.report.kind==='stale'?'서버 보고가 5분 이상 지났습니다. 현재 수집 상태는 미확인입니다.':
  health.report.kind==='previous-day'?'서버 보고 이후 한국시간 날짜가 바뀌었습니다. 오늘 수집 상태는 새 보고를 기다리고 있습니다.':
  health.report.kind==='invalid'?'서버 보고 시각을 확인할 수 없습니다. 현재 수집 상태는 미확인입니다.':
  status&&health.report.kind==='missing'?'서버 수집 보고가 없습니다. 사이트 반영 정보와 별도로 확인이 필요합니다.':'';
 return <div className="original-server-status" role="status">
  <div className="original-sync-line">{health.totalSourceRows===null?'사이트 반영 정보 확인 중':`사이트 반영 원천 누적 ${health.totalSourceRows.toLocaleString('ko-KR')}행`}{sync?` · 자료 r${sync.epochs.delivery}.${sync.epochs.budget}`:''}</div>
  <div className="original-sync-line">사이트 최근 반영 {time(health.receivedAt)}</div>
  {sync&&<SourceAvailability sync={sync}/>}
  <div className="original-source-status">{health.sources.map(source=><div className="original-source-state" data-tone={source.tone} key={source.source}>
   <div><strong>{source.name}</strong><span>{source.label}</span></div>
   <small>최근 회차: {source.reason}{source.held>0?` · 보류 ${source.held}범위`:''}{source.verifiedPages!==null?` · 재조회 ${source.verifiedPages.toLocaleString('ko-KR')}페이지`:''}</small>
  </div>)}</div>
  {reportWarning&&<p className="original-sync-warning">{reportWarning}</p>}
  {syncFailed&&<p className="original-sync-warning">사이트 반영 정보를 새로 읽지 못했습니다. 마지막 반영 시각을 확인하세요.</p>}
  <div className="original-sync-line">마지막 서버 보고 {time(health.report.observedAt)} · 화면 1분마다 확인 · <a href="/collection-monitor?mode=live">수집 상태</a> · <a href="/program-link">연동 점검</a></div>
  <div className="original-sync-note">누적 행 수는 전송 페이지의 원천 합계입니다. 전체 수집 완료나 LED 고유건수를 뜻하지 않습니다.</div>
 </div>;
}
