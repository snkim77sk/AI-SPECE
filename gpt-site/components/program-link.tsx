"use client";
import {useEffect,useRef,useState} from 'react';
import {projectProgramLink} from '../lib/program-link.mjs';
import SourceAvailability from './source-availability';
import './agent-demo.css';

type View={stale:boolean;reportKind:string;observedAt:string;receivedAt:string;digest:string;sources:{source:string;available:boolean;attempted:number|null;limit:number|null;verifiedRows:number|null;state:string;nextRun:string|null;reason:string;rawReason:string;held:number|null;statusLabel:string;statusTone:string;taskEnabled:boolean|null;taskResult:number|null;retryCount:number;runReportedAt:string|null;runFinishedAt:string|null;verifiedPages:number|null;syncedAt:string|null}[];datasets:{dataset:string;pages:number|null;sourceRows:number|null;matchedRows:number|null;receivedAt:string}[]|null};
const number=(x:number|null)=>x===null?'미확인':x.toLocaleString('ko-KR');
const time=(x:string|null)=>x&&Number.isFinite(Date.parse(x))?new Date(x).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})+' KST':'미확인';
const datasetName=(value:string)=>({shopping_delivery:'나라장터 납품요구',budget:'지방재정 세부사업',budget_appropriation:'지방재정 편성자료'}[value]||value);

export default function ProgramLink(){
 const [data,setData]=useState<{status:any;sync:any}|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[clock,setClock]=useState(Date.now());
 const requestRefresh=useRef<(()=>Promise<void>)|null>(null);
 useEffect(()=>{
  let stopped=false,inFlight=false,activeController:AbortController|null=null;
  const refresh=async(force=false)=>{
   if(stopped)return;
   setClock(Date.now());
   if(inFlight||document.hidden&&!force)return;
   inFlight=true;setBusy(true);
   const controller=new AbortController();activeController=controller;
   const timeout=setTimeout(()=>controller.abort(),10000);
   try{
    const [a,b]=await Promise.all([
     fetch('/api/server-status',{cache:'no-store',signal:controller.signal}),
     fetch('/api/server-sync',{cache:'no-store',signal:controller.signal}),
    ]);
    if(!a.ok||!b.ok)throw Error();
    const [status,sync]=await Promise.all([a.json(),b.json()]);
    projectProgramLink(status,sync);
    if(!stopped){setData({status,sync});setError('');setClock(Date.now());}
   }catch{if(!stopped)setError('서버 연동 상태를 새로 읽지 못했습니다. 아래 값은 마지막 보고이며 현재 상태로 단정하지 마세요.');}
   finally{clearTimeout(timeout);activeController=null;inFlight=false;if(!stopped)setBusy(false);}
  };
  requestRefresh.current=()=>refresh(true);
  const visible=()=>{if(!document.hidden)void refresh();};
  void refresh();
  const timer=setInterval(()=>void refresh(),60000);
  document.addEventListener('visibilitychange',visible);
  return()=>{stopped=true;clearInterval(timer);activeController?.abort();document.removeEventListener('visibilitychange',visible);requestRefresh.current=null;};
 },[]);
 const view=data?projectProgramLink(data.status,data.sync,clock,{reportFetchFailed:!!error}) as View:null;
 return <main className="agent-lab">
  <header><span className="agent-tag">실제 저장 상태 · 읽기 전용</span><h1>서버 프로그램과 사이트 연동</h1><p>서버 수집 결과와 사이트 반영 결과를 함께 확인합니다. 서버 예약 수집은 사이트 화면과 별도로 실행됩니다.</p></header>
  <button style={{maxWidth:240}} disabled={busy} onClick={()=>void requestRefresh.current?.()}>{busy?'조회 중…':'현재 저장 상태 다시 읽기'}</button>
  <p className="agent-refresh-note">1분마다 자동 확인하며, 이 화면으로 돌아오면 다시 확인합니다.</p>
  <section aria-live="polite">
   {error&&<p role="alert" className="agent-error">{error}</p>}
   {view&&<>
    {view.stale&&!error&&<p className="agent-warning">{view.reportKind==='stale'?'서버 보고가 5분 이상 지났습니다.':view.reportKind==='previous-day'?'서버 보고 이후 한국시간 날짜가 바뀌었습니다.':'서버 보고 시각을 확인할 수 없습니다.'} 현재 수집 상태와 잔여량은 상세 수집 화면에서 다시 확인하세요.</p>}
    <p>마지막 서버 보고 {time(view.observedAt)} · 사이트 상태보고 수신 {time(view.receivedAt)}</p>
    <div className="agent-grid">{view.sources.map(s=><section className="agent-evidence" key={s.source}>
     <h2>{s.source==='g2b'?'나라장터 G2B':'지방재정365'}</h2>
     <p className="agent-health" data-tone={s.statusTone}>{s.statusLabel}</p>
     <p>{s.reason}</p>
     <dl className="agent-status-details">
      <dt>보고 당시 당일 호출 / 설정 한도</dt><dd>{number(s.attempted)} / {number(s.limit)}</dd>
      <dt>보고 당시 당일 재조회 확인 행 수</dt><dd>{number(s.verifiedRows)}</dd>
      <dt>최근 회차 재조회</dt><dd>{number(s.verifiedPages)}페이지</dd>
      <dt>최근 회차 보고</dt><dd>{time(s.runReportedAt)}</dd>
      <dt>예약 수집</dt><dd>{s.taskEnabled===true?'사용 중':s.taskEnabled===false?'중지됨':'미확인'} · 관측 상태 {s.state}</dd>
      <dt>관측 당시 다음 실행</dt><dd>{time(s.nextRun)}</dd>
      <dt>보류 / 재시도 기록</dt><dd>{number(s.held)} / {number(s.retryCount)}</dd>
      <dt>이 원천의 사이트 최근 반영</dt><dd>{time(s.syncedAt)}</dd>
     </dl>
     {s.state!=='Running'&&s.taskResult!==null&&s.taskResult!==0&&<p className="agent-warning">Windows 작업 결과 코드 {s.taskResult}</p>}
     <small>{s.rawReason}</small>
    </section>)}</div>
    <h2 style={{marginTop:32}}>사이트에 반영된 자료</h2>
    <SourceAvailability sync={data?.sync}/>
    {view.datasets?.length?view.datasets.map(d=><p key={d.dataset}>{datasetName(d.dataset)}: {number(d.pages)}페이지 · 누적 원천 {number(d.sourceRows)}행 · 분류 결과 {number(d.matchedRows)}행 · 최근 반영 {time(d.receivedAt)}</p>):<p>반영 건수 미확인 또는 반영 대기</p>}
    <p>행 수는 전송 페이지의 원천 합계입니다. 전체 수집 완료나 현재 LED 고유건수를 뜻하지 않습니다.</p>
    <details><summary>연동 확인 근거</summary><p>서버 상태 API: /api/server-status<br/>수집자료 동기화 API: /api/server-sync</p><code style={{overflowWrap:'anywhere'}}>{view.digest}</code></details>
   </>}
  </section>
  <h2 style={{marginTop:32}}>분석 결과 확인</h2>
  <p><a href="/budget?mode=live">예산·영업후보</a> · <a href="/vendors?mode=live">업체·단가 분석</a> · <a href="/collection-monitor?mode=live">상세 수집 상태</a></p>
  <section className="agent-evidence"><h2>AI 연결 상태</h2><p>실제 모델 미연결 · 이 화면의 설명은 상태 코드에 따른 규칙 기반 안내입니다. AI 분석 결과로 표시하지 않습니다.</p>{process.env.NODE_ENV==='development'&&<a href="/agent-lab">시험 자료로 모의 AI 화면 열기</a>}</section>
  <footer>공식 API 잔여량은 별도 확인이 필요합니다. 표시 한도는 서버에 설정된 일일 한도입니다. 데이터 수정·수집 재시작·API 원천 호출 기능은 없습니다.</footer>
 </main>;
}
