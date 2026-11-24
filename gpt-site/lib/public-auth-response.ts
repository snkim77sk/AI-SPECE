/** Validate the JSON boundary before displaying account or approval state. */
type Status='pending'|'approved'|'rejected'|'suspended';
type Role='member'|'admin';
export type MemberSummary={displayName:string;status:Status;role:Role};
export type AccountResponse={authenticated:false;member:null}|{authenticated:true;member:MemberSummary};
export type ReviewMember={id:string;display_name:string;email:string;status:Status;role:Role;created_at:string};
const record=(value:unknown):value is Record<string,unknown>=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const status=(value:unknown):value is Status=>typeof value==='string'&&['pending','approved','rejected','suspended'].includes(value);
const role=(value:unknown):value is Role=>value==='member'||value==='admin';
export function accountResponse(value:unknown):AccountResponse{
 if(record(value)&&value.authenticated===false&&value.member===null)return {authenticated:false,member:null};
 if(record(value)&&value.authenticated===true&&record(value.member)){
  const m=value.member;
  if(typeof m.displayName==='string'&&status(m.status)&&role(m.role))return {authenticated:true,member:{displayName:m.displayName,status:m.status,role:m.role}};
 }
 throw Error('회원 상태 응답을 확인할 수 없습니다.');
}
export function reviewMembers(value:unknown):ReviewMember[]{
 if(!record(value)||!Array.isArray(value.members)||value.members.length>100)throw Error('회원 목록 응답을 확인할 수 없습니다.');
 return value.members.map((m:unknown)=>{
  if(!record(m)||typeof m.id!=='string'||typeof m.display_name!=='string'||typeof m.email!=='string'||typeof m.created_at!=='string'||!status(m.status)||!role(m.role))throw Error('회원 목록 응답을 확인할 수 없습니다.');
  return {id:m.id,display_name:m.display_name,email:m.email,created_at:m.created_at,status:m.status,role:m.role};
 });
}
