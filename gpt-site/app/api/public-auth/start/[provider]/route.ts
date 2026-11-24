import {env} from 'cloudflare:workers';
import {oauthStart} from '../../../../../lib/public-oauth.mjs';
import {publicEnabled,publicConfigured,publicOrigin} from '../../../../../lib/public-auth-service';
import {database} from '../../../../../lib/g2b-service';
import {inspectIdentityStorage} from '../../../../../lib/public-release-readiness.mjs';
export const dynamic='force-dynamic';
export async function GET(request:Request,context:{params:Promise<{provider:string}>}){
 if(!publicEnabled()||!publicConfigured())return Response.json({error:'PUBLIC_AUTH_NOT_READY'},{status:503});
 const origin=publicOrigin();
 if(!origin||new URL(request.url).origin!==origin)return Response.json({error:'AUTH_ORIGIN_INVALID'},{status:403});
 const {provider}=await context.params;
 try{
  const auth=await oauthStart(provider,origin,env as unknown as Record<string,string|undefined>);
  // A missing administrator must not block the first owner's sign-in rehearsal.
  // Validate storage only; no enrollment, bootstrap or provider HTTP calls occur here.
  const storage=await inspectIdentityStorage(database);
  if(storage.schema!=='ready')return Response.json({error:'SIGNIN_UNAVAILABLE'},{status:503,headers:{'Cache-Control':'no-store'}});
  return new Response(null,{status:302,headers:{Location:auth.url,'Set-Cookie':auth.cookie,'Cache-Control':'no-store'}});
 }catch(e){return Response.json({error:(e as Error)?.message==='INVALID_PROVIDER'?'INVALID_PROVIDER':'PROVIDER_NOT_CONFIGURED'},{status:400});}
}
