'use client';
import {useEffect,useRef,useState} from 'react';
import {useSearchParams} from 'next/navigation';
import {createMatchingReadGuard,matchingMode,matchingJobMatches} from '../../lib/matching-ui.mjs';
const money=(x:any)=>x===null||x===undefined?'미제공':String(x).replace(/\B(?=(\d{3})+(?!\d))/g,',');
const phases:any={INDEX:'예산 기관별 색인',GROUP:'전체 요구건 품목 합산',MATCH:'후보 점수·최고 후보 선택',DONE:'완료'};
export default function Matching() {
 const initialMode=matchingMode(useSearchParams()?.toString()||'');
 const [mode,setMode]=useState(initialMode),[w,setW]=useState<any>(null),[from,setFrom]=useState(''),[to,setTo]=useState(''),[rows,setRows]=useState<any[]>([]),[next,setNext]=useState<string|null>(null),[busy,setBusy]=useState(false),[reading,setReading]=useState(false),[message,setMessage]=useState(''),[level,setLevel]=useState(''),[resultState,setResultState]=useState('idle');
 const keep=useRef(false),locked=useRef(false),id=useRef<string|null>(null),currentMode=useRef(initialMode),currentJob=useRef<any>(null),currentLevel=useRef(''),guard=useRef(createMatchingReadGuard());
 function locate(value:string) {
  const url=new URL(window.location.href);url.searchParams.set('mode',value);
  window.history.replaceState(window.history.state,'',url);window.dispatchEvent(new Event('g2b:mode-change'));
 }
 function clearResults() {setRows([]);setNext(null);setResultState('idle');}
 function selectMode(value:string,updateUrl=true) {
  keep.current=false;guard.current.invalidate();currentMode.current=value;currentJob.current=null;id.current=null;
  setMode(value);setW((previous:any)=>previous?{...previous,job:null}:null);clearResults();setMessage('');setReading(false);
  if(updateUrl)locate(value);
 }
 function adopt(data:any,expectedMode:string,target?:string|null) {
  const job=matchingJobMatches(data.job,expectedMode,target)?data.job:null;
  setW({...data,job});currentJob.current=job;id.current=job?.id||null;
  setFrom(x=>x||data.window.from);setTo(x=>x||data.window.to);
  return job;
 }
 async function workspace(target?:string|null,readResults=true) {
  const token=guard.current.begin('workspace'),expectedMode=currentMode.current;
  guard.current.begin('results');clearResults();setReading(true);setMessage('');
  try {
   const response=await fetch('/api/matching'+(target?'?id='+encodeURIComponent(target):''),{signal:AbortSignal.timeout(15000)}),data:any=await response.json();
   if(!guard.current.current(token))return;
   if(!response.ok)throw Error(data.error||'MATCH_READ_FAILED');
   if(target&&!matchingJobMatches(data.job,expectedMode,target))throw Error('선택한 계획의 자료 모드가 다릅니다. 계획을 다시 선택하세요.');
   const job=adopt(data,expectedMode,target);
   if(readResults&&job)await results(undefined,job.id);
  } catch(e) {if(guard.current.current(token)){setMessage('매칭 저장소 조회 실패: '+(e as Error).message);setResultState('error');}}
  finally {if(guard.current.current(token))setReading(false);}
 }
 async function results(after?:string,target=id.current) {
  const token=guard.current.begin('results'),expectedMode=currentMode.current,expectedLevel=currentLevel.current;
  clearResults();setMessage('');
  if(!target||!matchingJobMatches(currentJob.current,expectedMode,target))return;
  setResultState('loading');
  try {
   const p=new URLSearchParams({action:'results',id:target,level:expectedLevel});if(after)p.set('after',after);
   const response=await fetch('/api/matching?'+p,{signal:AbortSignal.timeout(15000)}),data:any=await response.json();
   if(!guard.current.current(token)||currentMode.current!==expectedMode||id.current!==target)return;
   if(!response.ok)throw Error(data.error||'MATCH_RESULTS_FAILED');
   if(!matchingJobMatches(data.job,expectedMode,target))throw Error('MATCH_RESULT_MODE_MISMATCH');
   setRows(data.rows);setNext(data.next);setResultState('ready');
   currentJob.current=data.job;setW((previous:any)=>({...previous,job:data.job}));
  } catch(e) {if(guard.current.current(token)){setMessage('매칭 결과 조회 실패: '+(e as Error).message);setResultState('error');}}
 }
 useEffect(()=>{
  const restore=()=>{selectMode(matchingMode(window.location.search),false);void workspace();};restore();
  window.addEventListener('popstate',restore);
  return()=>{keep.current=false;guard.current.invalidate();window.removeEventListener('popstate',restore);};
 },[]);
 async function call(action:string,expectedMode:string,target:string|null,token:any) {
  if(!guard.current.current(token)||currentMode.current!==expectedMode)throw Error('MATCH_SELECTION_CHANGED');
  if(action!=='create'&&!matchingJobMatches(currentJob.current,expectedMode,target))throw Error('MATCH_JOB_MODE_MISMATCH');
  const response=await fetch('/api/matching',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,id:target,mode:expectedMode,from,to})}),data:any=await response.json();
  if(!guard.current.current(token))return null;
  if(!response.ok)throw Error(data.error||'MATCH_REQUEST_FAILED');
  if(!matchingJobMatches(data.job,expectedMode,action==='create'?null:target))throw Error('MATCH_JOB_MODE_MISMATCH');
  adopt(data,expectedMode,data.job.id);return data;
 }
 async function run(action:string,continuous=false) {
  if(locked.current||reading)return;
  const expectedMode=currentMode.current;
  if(action!=='create'&&(!id.current||!matchingJobMatches(currentJob.current,expectedMode,id.current))) {setMessage('현재 자료 모드의 계획을 먼저 선택하세요.');return;}
  locked.current=true;keep.current=continuous;guard.current.invalidate();const token=guard.current.begin('write');
  setBusy(true);setMessage('');clearResults();
  try {
   let data=await call(action,expectedMode,id.current,token);
   while(guard.current.current(token)&&keep.current&&data?.job?.state==='ready')data=await call('step',expectedMode,data.job.id,token);
   if(guard.current.current(token)&&continuous&&!keep.current&&data?.job?.state==='ready')await call('pause',expectedMode,data.job.id,token);
   if(guard.current.current(token))await results();
  } catch(e) {if(guard.current.current(token)){setMessage((e as Error).message);setResultState('error');}}
  finally {locked.current=false;keep.current=false;setBusy(false);}
 }
 function chooseJob(job:any) {if(locked.current)return;selectMode(job.mode);id.current=job.id;void workspace(job.id);}
 function chooseMode(value:string) {if(locked.current||value===currentMode.current)return;selectMode(value);void workspace();}
 function chooseLevel(value:string) {currentLevel.current=value;setLevel(value);void results();}
 const job=w?.job,selected=matchingJobMatches(job,mode,id.current),resultBusy=resultState==='loading';
 return <div className="budget-shell"><header><a href={'/budget?mode='+mode}>← 예산·집행</a><a href={'/shopping?category=LIGHTING&mode='+mode}>납품·기관·업체</a><span>비공개 · 예산·납품 매칭</span></header><section className="intro"><div><div className="eyebrow">MATCHING · EVIDENCE</div><h1>요구건 하나에, 가장 가까운 예산 하나.</h1><p>품목을 요구건 전체로 묶고 기관·사업명·분류·시기·금액 근거를 비교합니다.</p></div></section><div className="notice"><div><strong>{'수집된 실제 자료 범위만 분석합니다.'}</strong><p>후보는 예산 집행·발주 예정의 증명이 아닙니다. 전국 원천의 완전성은 검증 전입니다. 행정개편은 확인된 관계와 시행일 방향, 같은 장소 단서가 있는 경우만 비교하며 원천 기관명을 바꾸지 않습니다. 고유 사업명이 겹치지 않는 일반 조명 표현만으로는 높은 신뢰도로 분류하지 않습니다.</p></div></div>{message&&<div className="feedback" role="status">{message}</div>}<section className="panel"><div className="budget-controls"><label>매칭 자료<span className="chip mint">실제 저장자료</span></label><label>요구건 선택 시작<input type="date" value={from} min={w?.window.from} max={to} disabled={busy} onChange={e=>setFrom(e.target.value)}/></label><label>요구건 선택 종료<input type="date" value={to} min={from} max={w?.window.to} disabled={busy} onChange={e=>setTo(e.target.value)}/></label><button className="primary" disabled={busy||reading||!from||!to} onClick={()=>run('create')}>새 매칭 계획</button><button disabled={busy||reading||!selected||!['ready','running'].includes(job?.state)} onClick={()=>run('step',true)}>연속 분석</button><button disabled={!busy} onClick={()=>{keep.current=false;setMessage('현재 작업 저장 후 중단합니다.');}}>분석 중단</button><button disabled={busy||reading||!selected||job?.state!=='paused'} onClick={()=>run('resume')}>재개 준비</button><button disabled={busy||reading||!selected||!['ready','running','paused'].includes(job?.state)} onClick={()=>run('cancel')}>계획 취소</button><button disabled={busy} onClick={()=>{if(!locked.current)void workspace(id.current);}}>상태 새로고침</button></div><p className="analysis-note">선택 기간에 포함된 요구건을 찾되 해당 요구건의 저장된 전체 최신 대상 품목을 합산합니다. 화면을 닫으면 연속 분석은 멈추며 체크포인트에서 재개합니다. 분석 도중 원천 자료가 바뀌면 중단하고 새 계획이 필요합니다.</p>{selected&&<div className="budget-controls"><strong>{phases[job.phase]} · {job.state}{job.stale?' · 원천·분석 규칙 변경됨':''}</strong><span>예산 {job.budget_count.toLocaleString()}건</span><span>요구 {job.request_count.toLocaleString()}건</span><span>비교 {job.candidate_count.toLocaleString()}쌍</span><span>후보 {job.match_count.toLocaleString()}건</span><span>충돌 제외 {job.excluded_count.toLocaleString()}건</span></div>}</section><section className="panel"><div className="budget-controls"><h2>요구건별 최상위 후보</h2><label>점수 분류<select aria-label="매칭 점수 분류" value={level} disabled={busy} onChange={e=>chooseLevel(e.target.value)}><option value="">전체</option><option value="HIGH">높음 · 80점 이상</option><option value="CANDIDATE">검토 · 65~79점</option></select></label><button disabled={busy||reading||!selected} onClick={()=>void results()}>결과 조회</button></div><p className="analysis-note">{!selected||job?.state!=='complete'||job?.stale?'완료·최신 상태가 아닌 결과는 잠정값입니다. ':'완료된 분석 결과입니다. '}금액 미제공 품목이 있는 요구건은 합계가 불완전하므로 별도 표시합니다. 결과는100건씩 표시하고 원문 식별자와 근거를 보관합니다.</p><div className="data-table-wrap"><table><thead><tr><th>예산 사업 / 기관</th><th>납품 요구 / 업체</th><th>예산 / 대상 품목 합계</th><th>점수 / 근거</th></tr></thead><tbody>{rows.map(r=><tr key={r.shopping_source_key}><td><strong>{r.budget_project}</strong><small>{r.budget_org} · {r.budget_dept} · {r.budget_year}</small></td><td><strong>{r.shopping_delivery_req_no}</strong><small>{r.shopping_delivery_name||r.shopping_item}<br/>{r.shopping_vendor} · {r.request_detail_rows}품목 · {r.shopping_date}</small></td><td>{money(r.budget_amount)}<br/>{money(r.shopping_amount)}{r.shopping_missing_amounts>0&&<small>금액 미제공 {r.shopping_missing_amounts}품목 포함</small>}</td><td><strong>{r.score} · {r.level==='HIGH'?'높음':'검토'}</strong><small>{r.evidence.join(' · ')}</small></td></tr>)}</tbody></table>{!rows.length&&<div className="data-empty"><h3>{reading||resultBusy?'매칭 자료 조회 중입니다.':resultState==='error'?'조회를 완료하지 못했습니다.':resultState==='idle'?'현재 모드의 계획을 선택하고 결과를 조회하세요.':'표시할 매칭 후보가 없습니다.'}</h3><p>자료를 수집하고 분석을 완료한 뒤 결과를 확인하세요. 후보가 없다는 것이 관련 사업이 없다는 뜻은 아닙니다.</p></div>}</div><div className="table-footer"><span>요구건마다 하나의 최상위 후보 · 수집된 범위 기준</span><button disabled={!next||busy||reading||resultBusy||!selected} onClick={()=>void results(next!)}>다음100건</button></div></section><section className="panel"><h2>행정개편 관계와 한계</h2><p>인천은 동일한 정확 동 명칭과 구기관의 시행 전 날짜, 신기관의 시행 후 날짜가 함께 있어야 후보로 비교합니다. 화성시 본청은 존속하므로 구청과 동일 기관으로 합치지 않고, 명시적 구 이름이 겹쳐도79점 이하 검토 후보로 제한합니다. 전남광주는 정확한 최상위 정부 명칭만 통합 관계로 연결합니다.</p><p>출처: <a className="text-link" href="https://www.incheon.go.kr/IC040102" target="_blank" rel="noreferrer">인천시 행정구역</a> · <a className="text-link" href="https://www.hscity.go.kr/office/intro.jsp" target="_blank" rel="noreferrer">화성시 구청 안내</a> · <a className="text-link" href="https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=284111" target="_blank" rel="noreferrer">전남광주 특별법</a></p><p>이전 분석 규칙으로 만든 결과는 오래된 결과로 표시합니다. 새 규칙으로 다시 분석하려면 새 매칭 계획을 만드세요.</p></section><section className="panel history"><h2>최근 매칭 계획</h2>{w?.jobs?.map((j:any)=><button key={j.id} disabled={busy} onClick={()=>chooseJob(j)}><span>{'실제'} · {j.from_day}~{j.to_day}</span><strong>{phases[j.phase]} · {j.state} · {j.match_count}후보</strong></button>)}</section><footer>인천2026-07-01·화성2026-02-01·전남광주2026-07-01의 확인된 관계만 지원합니다. 다른 개편은 추정하지 않습니다. 실제 공공 API·무인 일정 검증은 별도입니다.</footer></div>}
