'use client';
import {useEffect,useMemo,useState} from 'react';
import {CODE_SNAPSHOT_SHA,FEEDBACK_TYPES,PROJECT_REPOSITORY,SOURCE_VERSION,STAGES,feedbackIssueUrl,validateFeedback} from '../../lib/owner-progress.mjs';
import {syncAvailability} from '../../lib/sync-source-summary.mjs';
import './owner-progress.css';

type SyncInfo={sources?:unknown[];version?:string};
const stateLabel:Record<string,string>={'operator-verified':'운영 기록 확인','code-verified':'GitHub 코드 검증','unverified':'운영 미확인',blocked:'공개 전환 대기'};
const safeTime=(value:unknown)=>typeof value==='string'&&Number.isFinite(Date.parse(value))?new Date(value).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})+' KST':'미확인';

export default function OwnerProgress(){
 const [sync,setSync]=useState<SyncInfo|null>(null);
 const [syncError,setSyncError]=useState('');
 const [loading,setLoading]=useState(true);
 const [type,setType]=useState('ui');
 const [title,setTitle]=useState('');
 const [detail,setDetail]=useState('');
 const [notice,setNotice]=useState('');
 const [refresh,setRefresh]=useState(0);
 const status=useMemo(()=>syncAvailability(sync),[sync]);
 const validation=useMemo(()=>validateFeedback({type,title,detail}),[type,title,detail]);
 useEffect(()=>{
  const abort=new AbortController();
  setLoading(true);setSyncError('');
  fetch('/api/server-sync',{cache:'no-store',signal:abort.signal,headers:{Accept:'application/json'}})
   .then(async response=>{
    if(!response.ok)throw Error(response.status===401||response.status===403?'운영 동기화 정보는 관리자만 볼 수 있습니다.':'운영 수신 영수증을 확인할 수 없습니다.');
    const data:unknown=await response.json();
    if(!data||typeof data!=='object'||!Array.isArray((data as SyncInfo).sources))throw Error('수신 상태 응답 형식을 확인할 수 없습니다.');
    setSync(data as SyncInfo);
   })
   .catch(e=>{if(!abort.signal.aborted){setSync(null);setSyncError(e instanceof Error?e.message:'자료 조회 실패');}})
   .finally(()=>{if(!abort.signal.aborted)setLoading(false);});
  return()=>abort.abort();
 },[refresh]);
 function prepareIssue(){
  setNotice('');
  if(!validation.ok){setNotice(validation.error||'입력 내용을 확인해 주세요.');return;}
  const url=feedbackIssueUrl({type,title,detail});
  // GitHub requires the signed-in owner to review and submit the new issue.
  // No access token or private content is forwarded by the Site.
  window.open(url,'_blank','noopener,noreferrer');
  setNotice('GitHub에 작성 화면을 열었습니다. 제목·내용을 확인한 후 이슈 등록 버튼을 눌러야 실제 전달됩니다.');
 }
 return <main className="original-wrap owner-progress">
  <section className="original-card">
   <div className="owner-progress-heading">
    <div><p className="owner-progress-eyebrow">G2B OWNER WORKSPACE</p><h1>개발 진행현황 · 수정 지시</h1>
     <p className="original-muted">개발 코드와 실제 운영 상태를 구분해 확인하고, 수정·보완 요청을 GitHub로 전달하는 관리자 화면입니다.</p></div>
    <button onClick={()=>setRefresh(v=>v+1)} disabled={loading}>{loading?'수신 확인 중…':'자료 새로고침'}</button>
   </div>
   <div className="owner-progress-metrics">
    <div><strong>v{SOURCE_VERSION}</strong><span>최신 GitHub 통합 소스</span><small>화면 빌드 시점의 기록 · 실제 배포 버전과 별도</small></div>
    <div><strong>미확인</strong><span>현재 Sites 실제 실행 버전</span><small>코드 버전만으로 배포 완료를 단정하지 않음</small></div>
    <div><strong>공개 전환 대기</strong><span>회원가입·외부 공개</span><small>실제 OAuth 및 관리자 승인 검증 필요</small></div>
   </div>
   <p className="original-muted">이 화면의 개발 이력 기준 커밋: <a className="owner-progress-link" href={PROJECT_REPOSITORY+'/commit/'+CODE_SNAPSHOT_SHA} target="_blank" rel="noreferrer">{CODE_SNAPSHOT_SHA.slice(0,12)}</a>. GitHub 최신 PR·CI는 아래 원본 링크에서 확인하세요. 코드 병합은 Sites 운영 배포 완료를 의미하지 않습니다.</p>
  </section>
  <section className="original-card" aria-label="개발 단계">
   <div className="owner-progress-heading"><h2>진행 단계</h2><a className="owner-progress-link" href={PROJECT_REPOSITORY+'/pulls'} target="_blank" rel="noreferrer">GitHub PR·검증 보기</a></div>
   <div className="owner-progress-stages">{STAGES.map(stage=><article key={stage.id} className="owner-progress-stage">
    <div className="owner-progress-stage-head"><h3>{stage.name}</h3><span data-status={stage.status}>{stateLabel[stage.status]||'미확인'}</span></div>
    <p>{stage.detail}</p>{stage.href&&<a className="owner-progress-link" href={stage.href} target={stage.href.startsWith('http')?'_blank':undefined} rel={stage.href.startsWith('http')?'noreferrer':undefined}>관련 화면 확인</a>}
   </article>)}</div>
  </section>
  <section className="original-card">
   <div className="owner-progress-heading"><div><h2>실제 자료 수신 현황</h2><p className="original-muted">기존 Sites DB의 수신 영수증을 읽기만 합니다. 제공기관 API를 호출하거나 원본 자료를 변경하지 않습니다.</p></div></div>
   {loading&&<p role="status">운영 수신 기록 조회 중…</p>}
   {syncError&&<p className="feedback" role="alert">{syncError} 자료가 0건이라는 의미는 아닙니다.</p>}
   {!loading&&!syncError&&<div className="owner-progress-stages">{status.map(source=><article key={source.dataset} className="owner-progress-stage">
    <h3>{source.name}</h3>
    <dl className="owner-progress-dl"><div><dt>최근 수집 날짜</dt><dd>{source.checkedDay||'미확인'}</dd></div><div><dt>Windows 체크포인트 저장</dt><dd>{source.saved===null?'미확인':source.saved.toLocaleString('ko-KR')+'행'}</dd></div><div><dt>Sites 수신</dt><dd>{source.received===null?'미확인':source.received.toLocaleString('ko-KR')+'행'}</dd></div><div><dt>미수신</dt><dd>{source.pending===null?'미확인':source.pending.toLocaleString('ko-KR')+'행'}</dd></div><div><dt>최근 수신 시각</dt><dd>{safeTime(source.receivedAt)}</dd></div></dl>
    {source.href&&<a className="owner-progress-link" href={source.href}>자료 검색으로 이동</a>}
   </article>)}</div>}
   <div className="owner-progress-links"><a href="/contract-review">신규 계약·예산 검토</a><a href="/collection-monitor">수집 상태</a><a href="/admin/members">회원 승인 관리</a></div>
  </section>
  <section className="original-card" aria-label="수정 보완 요청">
   <h2>수정·보완 지시 작성</h2>
   <p className="original-muted">원하는 수정 사항을 적으면 GitHub의 이슈 작성 화면이 열립니다. <strong>GitHub에서 최종 등록을 해야 Codex와 ChatGPT가 확인할 수 있습니다.</strong> 비밀키·비밀번호·개인정보를 입력하지 마세요.</p>
   <div className="owner-progress-form">
    <label>요청 구분<select value={type} onChange={e=>setType(e.target.value)}>{FEEDBACK_TYPES.map(item=><option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
    <label>수정 제목<input maxLength={100} value={title} onChange={e=>setTitle(e.target.value)} placeholder="예: 계약 검색 필터를 더 크게 표시"/></label>
    <label className="owner-progress-span">수정 내용<textarea maxLength={2400} rows={6} value={detail} onChange={e=>setDetail(e.target.value)} placeholder="어느 화면에서, 무엇을 어떻게 바꾸면 되는지 구체적으로 적어주세요."/></label>
   </div>
   <div className="owner-progress-feedback-actions"><button type="button" className="primary" onClick={prepareIssue}>GitHub에 수정 지시 작성</button><a className="owner-progress-link" href={PROJECT_REPOSITORY+'/issues'} target="_blank" rel="noreferrer">등록된 수정 요청 확인</a></div>
   {notice&&<p role="status" className="feedback">{notice}</p>}
  </section>
 </main>;
}
