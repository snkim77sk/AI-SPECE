import {env} from 'cloudflare:workers';
import {oauthIdentity,verifyState,clearStateCookie} from '../../../../../lib/public-oauth.mjs';
import {publicEnabled,publicConfigured,publicOrigin,registerIdentity,createSession,sessionCookie} from '../../../../../lib/public-auth-service';
export const dynamic='force-dynamic';
export async function GET(request:Request,context:{params:Promise<{provider:string}>}){
 if(!publicEnabled()||!publicConfigured())return Response.json({error:'PUBLIC_AUTH_NOT_READY'},{status:503});
 const origin=publicOrigin();if(!origin||new URL(request.url).origin!==origin)return Response.json({error:'AUTH_ORIGIN_INVALID'},{status:403});
 const {provider}=await context.params,url=new URL(request.url),code=url.searchParams.get('code'),state=url.searchParams.get('state');
 let clear='';
 try{
  if(!['kakao','naver','google'].includes(provider)||!code||!state||url.searchParams.has('error'))throw Error('OAUTH_CALLBACK_INVALID');
  const flow=verifyState(provider,state,request.headers.get('cookie'));
  clear=clearStateCookie(provider);
  const identity=await oauthIdentity(provider,code,flow,origin,env as unknown as Record<string,string|undefined>,state);
  const member=await registerIdentity(identity.provider,identity.subject,identity.name||'신규 회원',identity.email);
  const session=await createSession(member.id);
  const destination=member.status==='approved'?'/dashboard':'/account/pending';
  const headers=new Headers({Location:origin+destination,'Cache-Control':'no-store'});
  headers.append('Set-Cookie',clear);headers.append('Set-Cookie',sessionCookie(session));
  return new Response(null,{status:303,headers});
 }catch{
  const headers=new Headers({Location:origin+'/login?error=signin','Cache-Control':'no-store'});
  if(clear)headers.append('Set-Cookie',clear);
  return new Response(null,{status:303,headers});
 }
}
