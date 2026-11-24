import packageInfo from '../../../package.json' with {type:'json'};
import {guardPublicRequest,publicEnabled,requireAdmin} from '../../../lib/public-auth-service';
import {runtimeInfo} from '../../../lib/runtime-info.mjs';

export const dynamic='force-dynamic';
const headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'};

// Owner-private Sites retain their existing gate. Public member mode requires an approved admin.
// This read exposes only packaged app metadata: no environment values, source data or provider calls.
export async function GET(request:Request){
 try{
  const __publicBlock=await guardPublicRequest(request);if(__publicBlock)return __publicBlock;
  if(publicEnabled())await requireAdmin(request);
  return Response.json(runtimeInfo(packageInfo.version),{headers});
 }catch(error){
  const forbidden=error instanceof Error&&error.message==='ADMIN_REQUIRED';
  return Response.json({error:forbidden?'ADMIN_REQUIRED':'RUNTIME_INFO_UNAVAILABLE'},{status:forbidden?403:503,headers});
 }
}
