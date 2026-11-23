'use client';
import {useEffect,useState} from 'react';
export default function Pending(){
 const [m,setM]=useState<{authenticated:boolean;member?:{displayName:string;status:string;role:string}}|null>(null);
 useEffect(()=>{fetch('/api/public-auth/me',{cache:'no-store'}).then(r=>r.json()).then(setM).catch(()=>setM(null));},[]);
 return <main className="budget-shell"><section className="original-card"><h1>회원 승인 상태</h1>
  {!m?<p>회원 상태 확인 중입니다.</p>:m.authenticated?<><p>회원: {m.member?.displayName}</p><p role="status">{m.member?.status==='approved'?'관리자 승인이 완료됐습니다.':m.member?.status==='pending'?'관리자 승인 대기 중입니다.':m.member?.status==='rejected'?'가입 신청이 거절됐습니다.':'회원 이용이 중지됐습니다.'}</p>{m.member?.status==='approved'&&<p><a href="/dashboard">G2B 대시보드로 이동</a></p>}</>:<p><a href="/login">로그인하거나 회원가입하기</a></p>}
  <form method="POST" action="/api/public-auth/logout"><button type="submit">로그아웃</button></form>
 </section></main>;
}
