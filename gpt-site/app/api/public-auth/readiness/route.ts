import {env} from 'cloudflare:workers';
import {guardPublicRequest} from '../../../../lib/public-auth-service';
import {database} from '../../../../lib/g2b-service';
import {signInAvailability} from '../../../../lib/public-release-readiness.mjs';

export const dynamic='force-dynamic';
const headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'};
export async function GET(request:Request){
 try{
  const __publicBlock=await guardPublicRequest(request);if(__publicBlock)return __publicBlock;
  const result=await signInAvailability(env,database);
  return Response.json(result,{status:result.state==='unavailable'?503:200,headers});
 }catch{return Response.json({error:'SIGNIN_AVAILABILITY_UNAVAILABLE'},{status:503,headers});}
}
