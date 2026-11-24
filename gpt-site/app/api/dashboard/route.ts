import {guardPublicRequest,readOnlyDataView} from '../../../lib/public-auth-service';
import {dashboard} from '../../../lib/dashboard-service';
import {reportDatabaseError} from '../../../lib/db-errors.mjs';

export const dynamic='force-dynamic';
export async function GET(request:Request){
 const __publicBlock=await guardPublicRequest(request);if(__publicBlock)return __publicBlock;
 try{
  const data=await dashboard(new URL(request.url).searchParams.get('mode')||'live',true);
  const {readiness,storage,...storedData}=data;
  return Response.json(readOnlyDataView(request)?storedData:data,{headers:{'Cache-Control':'no-store'}});
 }catch(e){
  const code=e instanceof Error&&/^[A-Z0-9_]+$/.test(e.message)?e.message:null;
  if(code)return Response.json({error:code},{status:code==='DASHBOARD_AGGREGATE_PENDING'?202:code==='DASHBOARD_SOURCE_CHANGED_RETRY'?409:code==='INVALID_MODE'?400:503});
  const f=reportDatabaseError('dashboard',e);
  return Response.json({error:f.code,incident:f.incident},{status:503});
 }
}
