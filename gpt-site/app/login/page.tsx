'use client';
import {useEffect,useState} from 'react';
import {fetchSignInAvailability,readinessErrorMessage,type SignInAvailability} from '../../lib/public-readiness-response';
const methods=[
 {provider:'kakao',name:'카카오톡으로 가입·로그인',url:'/api/public-auth/start/kakao',detail:'카카오계정·Daum 통합계정'},
 {provider:'naver',name:'네이버로 가입·로그인',url:'/api/public-auth/start/naver',detail:'네이버 로그인'},
 {provider:'kakao',name:'다음(Daum) 계정으로 가입·로그인',url:'/api/public-auth/start/kakao',detail:'카카오로 통합된 Daum 계정'},
 {provider:'google',name:'구글·Gmail로 가입·로그인',url:'/api/public-auth/start/google',detail:'Google 계정'}
];
export default function Login(){
 const [readiness,setReadiness]=useState<SignInAvailability|null>(null),[error,setError]=useState('');
 const [loading,setLoading]=useState(true),[refresh,setRefresh]=useState(0),[signInFailed,setSignInFailed]=useState(false);
 useEffect(()=>{
  const abort=new AbortController();setLoading(true);setError('');setReadiness(null);
  setSignInFailed(new URLSearchParams(window.location.search).get('error')==='signin');
  fetchSignInAvailability(fetch,abort.signal)
   .then(value=>{if(!abort.signal.aborted)setReadiness(value);})
   .catch(reason=>{if(!abort.signal.aborted)setError(readinessErrorMessage(reason));})
   .finally(()=>{if(!abort.signal.aborted)setLoading(false);});
  return()=>abort.abort();
 },[refresh]);
 return <main className="budget-shell">
  <section className="original-card"><h1>G2B 회원가입 · 로그인</h1><p>신성라이텍 G2B 공개 전환 준비 화면입니다. 회원가입만으로 계약·납품·예산자료를 볼 수 없습니다. 관리자 승인을 받은 후 이용할 수 있습니다.</p>
   {signInFailed&&<p role="alert" className="feedback">로그인을 완료하지 못했습니다. 준비 상태를 확인한 뒤 다시 시도해 주세요.</p>}
   {loading&&<p role="status">로그인 준비 상태를 확인하고 있습니다.</p>}
   {error&&<p role="alert" className="feedback">{error}</p>}
   {readiness?.state==='disabled'&&<p role="status" className="feedback">현재 소셜 회원 서비스가 꺼져 있습니다.</p>}
   {readiness?.state==='setup-required'&&<p role="status" className="feedback">회원 서비스를 준비하고 있습니다. 준비된 로그인 방법이 확인되면 버튼이 활성화됩니다.</p>}
   {readiness?.state==='unavailable'&&<p role="alert" className="feedback">현재 로그인 준비 상태를 확인할 수 없습니다. 다시 확인해 주세요.</p>}
   <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(230px,1fr))',gap:'16px',margin:'20px 0'}}>
    {methods.map(method=>{
     const available=!loading&&!error&&readiness?.providers.some(provider=>provider.id===method.provider&&provider.available);
     return <div key={method.name}>
      {available?<a className="original-button" href={method.url}>{method.name}</a>:<button type="button" disabled aria-label={method.name+' · 준비 확인 필요'}>{method.name}</button>}
      <p style={{margin:'8px 0',fontSize:'0.875rem'}}>{method.detail}{available?'':' · 준비 확인 필요'}</p>
     </div>;
    })}
   </div>
   <button type="button" disabled={loading} onClick={()=>setRefresh(value=>value+1)}>{loading?'확인 중…':'로그인 준비 상태 다시 확인'}</button>
   <p>다음(Daum) 계정은 통합 카카오계정으로 가입합니다. 이메일 주소가 같아도 다른 소셜 계정을 자동 연결하지 않습니다.</p>
   <p>신청 정보: 소셜 제공자 식별값, 선택적으로 제공한 이름·이메일, 가입시각 및 관리자 승인 기록. 승인 거절·중지 시 자료 접근은 차단됩니다.</p>
  </section>
 </main>;
}
