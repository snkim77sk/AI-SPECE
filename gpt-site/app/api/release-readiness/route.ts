import {env} from 'cloudflare:workers';
import {guardPublicRequest,publicEnabled,requireAdmin} from '../../../lib/public-auth-service';
import {database} from '../../../lib/g2b-service';
import {releaseReadiness} from '../../../lib/public-release-readiness.mjs';

export const dynamic='force-dynamic';
const headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'};
export async function GET(request:Request){
 try{
  const __publicBlock=await guardPublicRequest(request);if(__publicBlock)return __publicBlock;
  if(publicEnabled())await requireAdmin(request);
  return Response.json(await releaseReadiness(env,database),{headers});
 }catch(error){
  const forbidden=error instanceof Error&&error.message==='ADMIN_REQUIRED';
  return Response.json({error:forbidden?'ADMIN_REQUIRED':'RELEASE_READINESS_UNAVAILABLE'},{status:forbidden?403:503,headers});
 }
}
