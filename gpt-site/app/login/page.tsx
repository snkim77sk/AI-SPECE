'use client';
import {useEffect,useState} from 'react';
const methods=[
 {name:'카카오톡으로 가입·로그인',url:'/api/public-auth/start/kakao',detail:'카카오계정·Daum 통합계정'},
 {name:'네이버로 가입·로그인',url:'/api/public-auth/start/naver',detail:'네이버 로그인'},
 {name:'구글·Gmail로 가입·로그인',url:'/api/public-auth/start/google',detail:'Google 계정'}
];
export default function Login(){
 const [enabled,setEnabled]=useState<boolean|null>(null);
 useEffect(()=>{fetch('/api/public-auth/me',{cache:'no-store'}).then(r=>setEnabled(r.ok)).catch(()=>setEnabled(false));},[]);
 return <main className="budget-shell">
  <section className="original-card"><h1>G2B 회원가입 · 로그인</h1><p>신성라이텍 G2B 공개 전환 준비 화면입니다. 회원가입만으로 계약·납품·예산자료를 볼 수 없습니다. 관리자 승인을 받은 후 이용할 수 있습니다.</p>
   {enabled===false&&<p role="status" className="feedback">현재 소셜 회원 서비스가 비활성화되어 있습니다. 비공개 운영환경과 기존 데이터는 유지됩니다.</p>}
   {enabled&&methods.map(method=><p key={method.url}><a className="original-button" href={method.url}>{method.name}</a> <small>{method.detail}</small></p>)}
   {enabled===null&&<p>회원가입 준비상태 확인 중</p>}
   <p>다음(Daum) 계정은 통합 카카오계정으로 가입합니다. 이메일 주소가 같아도 다른 소셜 계정을 자동 연결하지 않습니다.</p>
   <p>신청 정보: 소셜 제공자 식별값, 선택적으로 제공한 이름·이메일, 가입시각 및 관리자 승인 기록. 승인 거절·중지 시 자료 접근은 차단됩니다.</p>
  </section>
 </main>;
}
