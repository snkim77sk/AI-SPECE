'use client';
import {useEffect,useState} from 'react';
import {collectorStatusView,collectorReason} from '../lib/server-collector-status.mjs';
const number=(n:number|null)=>n===null?'미확인':n.toLocaleString();
const time=(value:string|null)=>value?new Date(value).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})+' KST':'미확인';
export default function ServerCollectorStatus(){
 const [snapshot,setSnapshot]=useState<any>(null),[error,setError]=useState(''),[refresh,setRefresh]=useState(0),[clock,setClock]=useState(Date.now());
 useEffect(()=>{const timer=setInterval(()=>{setClock(Date.now());setRefresh(v=>v+1);},60000);return()=>clearInterval(timer);},[]);
 useEffect(()=>{const c=new AbortController();fetch('/api/server-status',{signal:c.signal}).then(async r=>{const d:any=await r.json();if(!r.ok)throw Error();if(!c.signal.aborted){setSnapshot(d.snapshot);setError('');setClock(Date.now());}}).catch(()=>{if(!c.signal.aborted)setError('서버 상태를 새로 확인하지 못했습니다. 마지막 보고 시각을 확인해 주세요.');});return()=>c.abort();},[refresh]);
 const view=collectorStatusView(snapshot,clock);
 return <section className="original-card">
  <div className="panel-heading"><div><h2>오늘 API 호출 잔여량 · 한국시간</h2><p>Windows 수집기 장부 기준 · {view.day}</p></div><button onClick={()=>setRefresh(v=>v+1)}>서버 상태 새로고침</button></div>
  <p>서버 보고 {time(snapshot?.payload?.observed_at)} · 15분마다 전송, 화면은 1분마다 갱신합니다.</p>
  {error&&<p role="status" className="feedback">{error}</p>}
  {!snapshot?<p role="status">Windows 서버 상태 보고를 기다리고 있습니다. 호출 잔여량은 미확인입니다.</p>:<>
   {!view.fresh&&<p role="status" className="feedback">최신 호출 잔여량 미확인 · 날짜가 바뀌었거나 보고가 20분 이상 지났습니다. 아래 마지막 보고를 현재 잔여량으로 판단하지 마세요.</p>}
   <div className="original-stage-grid">{view.sources.map((s:any)=><article className="original-stage-card" key={s.source}>
    <div className="original-stage-head"><h3>{s.source==='g2b'?'나라장터':'지방재정365'}</h3><span className="original-pill">{s.quotaState==='EXHAUSTED'?'오늘 설정 한도 소진':s.quotaState==='AVAILABLE'?'설정 한도 내 잔여 있음':'잔여량 미확인'}</span></div>
    <h2>{s.known?'잔여 '+number(s.remaining)+'회':'현재 잔여 미확인'}</h2>
    <p>실제 호출 <strong>{number(s.attempted)}회</strong> / 서버 설정 한도 <strong>{number(s.limit)}회</strong></p>
    {s.pending>0&&<p>미확정 예약 {number(s.pending)}회도 한도에서 차감했습니다.</p>}
    {!s.known&&s.lastRemaining!==null&&<p>마지막 보고 당시 잔여 {number(s.lastRemaining)}회</p>}
    <p>제공기관 공식 잔여량: <strong>미확인</strong></p>
    <p>{s.quotaState==='EXHAUSTED'?'설정 한도를 모두 사용해 자정까지 호출을 대기합니다.':s.quotaState==='AVAILABLE'?'서버 설정 한도 소진으로 중단된 상태는 아닙니다.':'최신 보고가 없으므로 한도 소진 여부를 판단할 수 없습니다.'}</p>
    <hr/>
    <h4>최근 실행 · 중단 사유</h4>
    <p><strong>{s.runningObserved?'보고 시점 Windows 작업 실행 중':collectorReason(s.last_run?.reason)}</strong></p>
    {s.last_run&&<><p>최근 회차 API 호출 {number(s.last_run.attempted)}회 · 저장 {number(s.last_run.stored)}페이지 · 재조회 {number(s.last_run.verified)}페이지</p><p>실행 보고 {time(s.last_run.reported_at)}</p></>}
    {s.task?<p>예약 {s.task.enabled?'사용 중':'중지됨'} · 다음 실행 {time(s.task.next_run_at)}</p>:<p>Windows 예약 상태 미확인</p>}
    {s.task?.last_result!==0&&s.task?.last_result!=null&&<small>Windows 종료 코드 {s.task.last_result}</small>}
    {s.progress.map((p:any)=><p key={p.scope}>{p.scope} · 다음 {p.next_page}페이지 · 해당 범위 저장 {number(p.saved)}건</p>)}
    {s.held.length>0&&<details><summary>일부 범위 보류 {s.held.length}건</summary>{s.held.map((h:any)=><p key={h.scope}>{h.scope} · {h.next_page}페이지 · {collectorReason(h.reason)}</p>)}<p>보류된 범위는 자동 중복 호출하지 않으며 다른 정상 범위는 별도로 처리합니다.</p></details>}
   </article>)}</div>
   <p className="analysis-note">한국시간 자정에 날짜별 사용량이 새로 집계됩니다. 다음 초기화 {time(snapshot.payload.reset_at)}. 이 숫자는 서버에 설정한 한도이며 제공기관이 보장한 잔여량이 아닙니다. 실패한 실제 요청도 사용량에 포함하고, 전송하지 않은 것으로 확인된 요청은 제외합니다. 상태 자료 r{snapshot.digest.slice(0,12)}.</p>
  </>}
 </section>;
}
