import {env} from 'cloudflare:workers';
import {database} from './g2b-service';
import {SESSION_COOKIE,SESSION_TTL_SECONDS,publicAuthReady,cookieValue,safeErrorResponse,accessDecision,configuredOrigin} from './public-auth-policy.mjs';

export type Member={id:string;display_name:string;email:string;status:'pending'|'approved'|'rejected'|'suspended';role:'member'|'admin'};
type SessionMember=Member&{expires_at:number};
const settings=env as unknown as Record<string,string|undefined>;
const encoder=new TextEncoder();
export const publicEnabled=()=>Object.hasOwn(settings,'G2B_PUBLIC_AUTH_ENABLED')&&settings.G2B_PUBLIC_AUTH_ENABLED==='enabled';
export const publicConfigured=()=>publicAuthReady(settings)&&!!settings.G2B_PUBLIC_SESSION_PEPPER&&settings.G2B_PUBLIC_SESSION_PEPPER.length>=32&&!!settings.G2B_PUBLIC_SERVICE_SYNC_TOKEN&&settings.G2B_PUBLIC_SERVICE_SYNC_TOKEN.length>=32;
export const publicOrigin=()=>configuredOrigin(settings.G2B_PUBLIC_AUTH_ORIGIN);
export const randomToken=()=>{const arr=crypto.getRandomValues(new Uint8Array(32));return Array.from(arr,x=>x.toString(16).padStart(2,'0')).join('');};
export async function tokenHash(raw:string){
  if(!/^[a-f0-9]{64}$/.test(raw))throw Error('INVALID_SESSION_TOKEN');
  const pepper=settings.G2B_PUBLIC_SESSION_PEPPER;
  if(!pepper||pepper.length<32)throw Error('PUBLIC_AUTH_NOT_CONFIGURED');
  const key=await crypto.subtle.importKey('raw',encoder.encode(pepper),'HMAC',false,['sign']);
  const bytes=new Uint8Array(await crypto.subtle.sign('HMAC',key,encoder.encode(raw)));
  return Array.from(bytes,x=>x.toString(16).padStart(2,'0')).join('');
}
export async function memberForRequest(request:Request):Promise<Member|null>{
  const raw=cookieValue(request.headers.get('cookie'),SESSION_COOKIE);
  if(!raw)return null;
  const hash=await tokenHash(raw),now=Date.now();
  const row=await database().prepare("SELECT m.id,m.display_name,m.email,m.status,m.role,s.expires_at FROM public_sessions s JOIN public_members m ON m.id=s.member_id WHERE s.token_hash=? AND s.expires_at>?").bind(hash,now).first<SessionMember>();
  if(!row||!['pending','approved','rejected','suspended'].includes(row.status))return null;
  return {id:row.id,display_name:row.display_name,email:row.email,status:row.status,role:row.role};
}
export async function createSession(memberId:string){
  const raw=randomToken(),token_hash=await tokenHash(raw),now=Date.now();
  await database().prepare('INSERT INTO public_sessions(token_hash,member_id,created_at,expires_at) VALUES(?,?,?,?)').bind(token_hash,memberId,now,now+SESSION_TTL_SECONDS*1000).run();
  return raw;
}
export async function revokeSession(request:Request){
  const raw=cookieValue(request.headers.get('cookie'),SESSION_COOKIE);
  if(raw)await database().prepare('DELETE FROM public_sessions WHERE token_hash=?').bind(await tokenHash(raw)).run();
}
export function sessionCookie(raw:string){
  return SESSION_COOKIE+'='+raw+'; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age='+SESSION_TTL_SECONDS;
}
export function expiredSessionCookie(){return SESSION_COOKIE+'=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0';}
export async function registerIdentity(provider:'kakao'|'naver'|'google',providerId:string,name:string,email:string):Promise<Member>{
  if(!/^[A-Za-z0-9_-]{1,256}$/.test(providerId))throw Error('INVALID_PROVIDER_ID');
  const d=database(),stamp=new Date().toISOString(),candidate=crypto.randomUUID();
  // A single D1 transaction is atomic. Never link accounts just by email.
  await d.batch([
    d.prepare("INSERT INTO public_members(id,display_name,email,status,role,created_at,updated_at) SELECT ?,?,?,'pending','member',?,? WHERE NOT EXISTS (SELECT 1 FROM public_identities WHERE provider=? AND provider_user_id=?)").bind(candidate,name.slice(0,120),email.slice(0,254),stamp,stamp,provider,providerId),
    d.prepare("INSERT INTO public_identities(provider,provider_user_id,member_id,created_at) SELECT ?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM public_identities WHERE provider=? AND provider_user_id=?)").bind(provider,providerId,candidate,stamp,provider,providerId),
  ]);
  const member=await d.prepare("SELECT m.id,m.display_name,m.email,m.status,m.role FROM public_members m JOIN public_identities i ON i.member_id=m.id WHERE i.provider=? AND i.provider_user_id=?").bind(provider,providerId).first<Member>();
  if(!member)throw Error('REGISTER_FAILED');
  return member;
}
export async function guardPublicRequest(request:Request){
  if(!publicEnabled())return null;
  if(!publicConfigured()||!publicOrigin()||new URL(request.url).origin!==publicOrigin())return safeErrorResponse(503,'PUBLIC_AUTH_NOT_READY');
  let principal:Member|null=null;
  try{principal=await memberForRequest(request);}catch{return safeErrorResponse(503,'PUBLIC_AUTH_STORAGE_UNAVAILABLE');}
  const p=new URL(request.url).pathname;
  // Sync clients require a purpose-built service credential after switching to public mode.
  // Existing OAI-Sites-Authorization is not interchangeable with this application credential.
  let service=false;
  if((p==='/api/server-sync'||p==='/api/server-status')&&settings.G2B_PUBLIC_SERVICE_SYNC_TOKEN){
    const header=request.headers.get('x-g2b-service-token')||'';
    // Fixed-length constant-time compare without exposing configured secret.
    if(header.length===settings.G2B_PUBLIC_SERVICE_SYNC_TOKEN.length&&header.length>=32){
      let diff=0;for(let i=0;i<header.length;i++)diff|=header.charCodeAt(i)^settings.G2B_PUBLIC_SERVICE_SYNC_TOKEN.charCodeAt(i);service=diff===0;
    }
  }
  const decision=accessDecision(p,request.method,principal,service);
  if(p==='/api/budget'&&request.method==='GET'&&['workspace','history-plan'].includes(new URL(request.url).searchParams.get('action')||'')&&!(principal?.status==='approved'&&principal.role==='admin'))return safeErrorResponse(403,'ADMIN_REQUIRED');
  if(decision==='allow'||decision==='service')return null;
  if(decision==='unauthorized'){
    if(p.startsWith('/api/'))return safeErrorResponse(401,'MEMBER_LOGIN_REQUIRED');
    return Response.redirect(publicOrigin()+'/login',303);
  }
  if(p.startsWith('/api/'))return safeErrorResponse(403,'MEMBER_APPROVAL_REQUIRED');
  return Response.redirect(publicOrigin()+'/account/pending',303);
}
export async function requireAdmin(request:Request){
  if(!publicEnabled())throw Error('PUBLIC_AUTH_DISABLED');
  if(!publicConfigured()||!publicOrigin())throw Error('PUBLIC_AUTH_NOT_READY');
  const member=await memberForRequest(request);
  if(!member||member.status!=='approved'||member.role!=='admin')throw Error('ADMIN_REQUIRED');
  return member;
}
