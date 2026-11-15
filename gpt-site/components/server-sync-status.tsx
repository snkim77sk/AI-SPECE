'use client';
import {useEffect,useRef,useState} from 'react';
type Status={sources:{dataset:string;pages:number;source_rows:number;matched_rows:number;received_at:string}[];epochs:{delivery:number;budget:number}};
export default function ServerSyncStatus(){
 const [status,setStatus]=useState<Status|null>(null),[failed,setFailed]=useState(false),revision=useRef<string|null>(null);
 useEffect(()=>{let stopped=false,busy=false;const poll=async()=>{if(busy||document.hidden)return;busy=true;try{const r=await fetch('/api/server-sync',{cache:'no-store',signal:AbortSignal.timeout(10000)});if(!r.ok)throw Error();const s=await r.json() as Status;if(stopped)return;setStatus(s);setFailed(false);const next=JSON.stringify(s.epochs);if(revision.current!==null&&revision.current!==next&&!/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName||'')){window.location.reload();return;}if(revision.current===null)revision.current=next;}catch{if(!stopped)setFailed(true);}finally{busy=false;}};void poll();const timer=setInterval(poll,60000);document.addEventListener('visibilitychange',poll);return()=>{stopped=true;clearInterval(timer);document.removeEventListener('visibilitychange',poll);};},[]);
 const total=status?.sources.reduce((n,s)=>n+s.source_rows,0)||0;
 const latest=status?.sources.map(s=>s.received_at).sort().at(-1);
 return <div className="original-sub" role="status">서버 자료 연동 v5.2.8 · {failed?'연결 상태 확인 중':total?`검증된 원천 ${total.toLocaleString()}건 반영`:'서버 자료 반영 대기'}{latest?` · 최근 반영 ${new Date(latest).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})} KST`:''}{status?` · 자료 r${status.epochs.delivery}.${status.epochs.budget}`:''} · 1분마다 갱신 확인</div>;
}
