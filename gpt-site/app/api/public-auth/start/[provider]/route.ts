import {env} from 'cloudflare:workers';
import {oauthStart} from '../../../../../lib/public-oauth.mjs';
import {publicEnabled,publicConfigured,publicOrigin} from '../../../../../lib/public-auth-service';
export const dynamic='force-dynamic';
export async function GET(request:Request,context:{params:Promise<{provider:string}>}){
 if(!publicEnabled()||!publicConfigured())return Response.json({error:'PUBLIC_AUTH_NOT_READY'},{status:503});
 const origin=publicOrigin();
 if(!origin||new URL(request.url).origin!==origin)return Response.json({error:'AUTH_ORIGIN_INVALID'},{status:403});
 const {provider}=await context.params;
 try{
  const auth=await oauthStart(provider,origin,env as unknown as Record<string,string|undefined>);
  return new Response(null,{status:302,headers:{Location:auth.url,'Set-Cookie':auth.cookie,'Cache-Control':'no-store'}});
 }catch(e){return Response.json({error:(e as Error)?.message==='INVALID_PROVIDER'?'INVALID_PROVIDER':'PROVIDER_NOT_CONFIGURED'},{status:400});}
}
