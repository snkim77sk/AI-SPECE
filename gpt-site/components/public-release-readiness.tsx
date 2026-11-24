'use client';
import {useEffect,useState} from 'react';
import {fetchReleaseReadiness,readinessErrorMessage,type ReleaseReadiness} from '../lib/public-readiness-response';

const providerNames={kakao:'카카오톡·다음',naver:'네이버',google:'구글·Gmail'};
const schemaNames={ready:'조회 확인',missing:'회원 저장구조 준비 필요',unknown:'조회 실패 · 미확인'};
const checkedTime=(value:string)=>new Date(value).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})+' KST';
const cardStyle={border:'1px solid #dde2ea',borderRadius:12,padding:16,minWidth:0};
const pairStyle={display:'flex',justifyContent:'space-between',gap:12,margin:'10px 0',flexWrap:'wrap' as const};

/** Safe booleans only. This component cannot configure providers, promote members or publish. */
export default function PublicReleaseReadiness({refreshKey=0}:{refreshKey?:number}){
 const [data,setData]=useState<ReleaseReadiness|null>(null),[error,setError]=useState('');
 const [loading,setLoading]=useState(true),[refresh,setRefresh]=useState(0);
 useEffect(()=>{
  const abort=new AbortController();setLoading(true);setData(null);setError('');
  fetchReleaseReadiness(fetch,abort.signal)
   .then(value=>{if(!abort.signal.aborted)setData(value);})
   .catch(reason=>{if(!abort.signal.aborted)setError(readinessErrorMessage(reason,true));})
   .finally(()=>{if(!abort.signal.aborted)setLoading(false);});
  return()=>abort.abort();
 },[refresh,refreshKey]);
 return <section id="public-readiness" className="original-card" aria-label="회원가입 및 공개 준비 상태">
  <div style={{display:'flex',alignItems:'start',justifyContent:'space-between',gap:16,flexWrap:'wrap'}}>
   <div><h2>회원가입·공개 준비 상태</h2><p>소셜 로그인·가입 신청·관리자 승인 코드는 구현되어 있습니다. 아래에서 실제 서비스 연결과 공개 준비 상태를 확인하세요.</p></div>
   <button type="button" disabled={loading} onClick={()=>setRefresh(value=>value+1)}>{loading?'준비 상태 확인 중…':'준비 상태 새로고침'}</button>
  </div>
  <div style={cardStyle}>
   <h3>일반 회원은 자료 조회만</h3>
   <p>관리자가 승인한 회원은 계약·납품·예산자료 검색, 저장된 분석 결과 조회, 자료 다운로드를 이용합니다. 자료 수집·변경·삭제, 분석 실행, 설정과 회원 관리는 관리자에게만 허용합니다.</p>
   <p><a className="original-button" href="/dashboard?viewer=member">일반 사용자 화면 미리보기</a>{' '}<a className="original-button" href="/login">회원가입·로그인 화면</a></p>
   <p className="original-muted">미리보기는 현재 비공개 사이트에서 조회 전용 화면을 확인하는 기능입니다. 실제 소셜 로그인과 회원 승인 시험은 별도로 필요합니다.</p>
  </div>
  {loading&&<p role="status">로그인 제공자와 관리자 준비 상태를 확인하고 있습니다.</p>}
  {error&&<p role="alert" className="feedback">{error}</p>}
  {data&&<>
   <p className="original-muted">확인 시각: {checkedTime(data.checkedAt)} · 회원 인증 {data.enabled?'켜짐':'꺼짐'}. 인증 활성 상태만으로 외부 공개 여부를 판단하지 않습니다.</p>
   <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(240px,1fr))',gap:14}}>
    <article style={cardStyle}><h3>기본 인증 설정</h3><dl>
     <div style={pairStyle}><dt>HTTPS 로그인 주소</dt><dd>{data.configuration.httpsOrigin?'설정 확인':'준비 필요'}</dd></div>
     <div style={pairStyle}><dt>로그인 세션 보호</dt><dd>{data.configuration.sessionSecret?'설정 확인':'준비 필요'}</dd></div>
     <div style={pairStyle}><dt>Windows 동기화 인증</dt><dd>{data.configuration.serviceSyncToken?'설정 확인':'준비 필요'}</dd></div>
    </dl><p>설정의 존재 여부만 확인합니다. 실제 Windows 업로더 연결 시험은 미검증입니다.</p></article>
    <article style={cardStyle}><h3>회원 저장구조·관리자</h3><dl>
     <div style={pairStyle}><dt>회원 저장구조</dt><dd>{schemaNames[data.identityStorage.schema]}</dd></div>
     <div style={pairStyle}><dt>승인된 관리자</dt><dd>{data.identityStorage.approvedAdminPresent===null?'미확인':data.identityStorage.approvedAdminPresent?'있음':'최초 관리자 지정 필요'}</dd></div>
    </dl><p>최초 관리자 지정 전에도 준비된 소셜 로그인으로 계정을 확인할 수 있습니다. 운영자가 확인한 계정만 관리자로 지정해야 합니다.</p></article>
    {data.providers.map(provider=><article key={provider.id} style={cardStyle}><h3>{providerNames[provider.id]}</h3><dl>
     <div style={pairStyle}><dt>제공자 앱 설정</dt><dd>{provider.configured?'설정 확인':'준비 필요'}</dd></div>
     <div style={pairStyle}><dt>로그인 시작 조건</dt><dd>{provider.loginAvailable?'충족':'준비 필요'}</dd></div>
     <div style={pairStyle}><dt>실제 계정 로그인</dt><dd>미검증</dd></div>
    </dl></article>)}
    <article style={cardStyle}><h3>공개 전 운영 검증</h3><dl>
     <div style={pairStyle}><dt>회원 승인·차단 시험</dt><dd>미검증</dd></div>
     <div style={pairStyle}><dt>Windows 동기화 호환</dt><dd>미검증</dd></div>
     <div style={pairStyle}><dt>외부 공개 승인</dt><dd>미검증</dd></div>
    </dl><p>비공개에서 실제 로그인과 관리자 승인·차단, 동기화 및 되돌리기를 확인한 뒤 공개 전환을 검토합니다. 로그인 요청 제한과 개인정보 안내 등 공개 운영 항목도 확인해야 합니다.</p></article>
   </div>
  </>}
 </section>;
}
