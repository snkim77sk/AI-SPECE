'use client';
import {useEffect,useState} from 'react';
type Member={id:string;display_name:string;email:string;status:string;role:string;created_at:string};
export default function Members(){
 const [members,setMembers]=useState<Member[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 async function reload(){
  const response=await fetch('/api/public-auth/admin',{cache:'no-store'});
  if(!response.ok)throw Error('관리자 승인 권한 또는 자료 조회를 확인하세요.');
  const data:unknown=await response.json();
  if(!data||typeof data!=='object'||!('members' in data)||!Array.isArray(data.members))throw Error('잘못된 응답입니다.');
  const validMembers=data.members.filter((m:unknown):m is Member=>!!m&&typeof m==='object'&&'id' in m&&typeof m.id==='string'&&'status' in m&&typeof m.status==='string'&&'role' in m&&typeof m.role==='string'&&'display_name' in m&&typeof m.display_name==='string'&&'email' in m&&typeof m.email==='string'&&'created_at' in m&&typeof m.created_at==='string');
  if(validMembers.length!==data.members.length)throw Error('잘못된 회원 응답입니다.');
  setMembers(validMembers);
 }
 useEffect(()=>{reload().catch(e=>setError(e.message));},[]);
 async function change(id:string,status:'approved'|'rejected'|'suspended'){
  if(busy||!window.confirm(status==='approved'?'이 회원에게 계약·예산 조회를 허용하시겠습니까?':'이 회원의 이용상태를 변경하시겠습니까?'))return;
  setBusy(true);setError('');
  try{
   const r=await fetch('/api/public-auth/admin',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,status})});
   if(!r.ok)throw Error('승인 상태 변경에 실패했습니다.');
   await reload();
  }catch(e){setError((e as Error).message);}finally{setBusy(false);}
 }
 return <main className="budget-shell"><section className="original-card"><h1>G2B 회원 승인 관리</h1><p>소셜 가입 계정은 기본적으로 승인 대기입니다. 관리자 승인 후 자료 조회가 가능하며, 중지·거절은 현재 세션에도 즉시 반영됩니다. 수집기 설정 권한은 회원에게 부여하지 않습니다.</p>{error&&<p role="alert" className="feedback">{error}</p>}</section>
 <section className="panel"><div className="data-table-wrap"><table><thead><tr><th>회원</th><th>가입일</th><th>상태</th><th>처리</th></tr></thead><tbody>
 {members.map(m=><tr key={m.id}><td>{m.display_name}<small>{m.email||'이메일 미제공'}</small></td><td>{m.created_at}</td><td>{m.status}</td><td><button disabled={busy||m.status==='approved'} onClick={()=>void change(m.id,'approved')}>승인</button> <button disabled={busy||m.status==='rejected'} onClick={()=>void change(m.id,'rejected')}>거절</button> <button disabled={busy||m.status==='suspended'} onClick={()=>void change(m.id,'suspended')}>중지</button></td></tr>)}
 </tbody></table></div>{!members.length&&!error&&<p>등록된 일반 회원이 없습니다.</p>}</section></main>;
}
