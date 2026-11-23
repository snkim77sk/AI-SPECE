import {memberForRequest,publicEnabled,publicConfigured} from '../../../../lib/public-auth-service';
export const dynamic='force-dynamic';
export async function GET(request:Request){
 if(!publicEnabled()||!publicConfigured())return Response.json({error:'PUBLIC_AUTH_NOT_READY'},{status:503,headers:{'Cache-Control':'no-store'}});
 try{
  const m=await memberForRequest(request);
  return Response.json({authenticated:!!m,member:m?{displayName:m.display_name,status:m.status,role:m.role}:null},{headers:{'Cache-Control':'no-store'}});
 }catch{return Response.json({error:'AUTH_STORAGE_UNAVAILABLE'},{status:503,headers:{'Cache-Control':'no-store'}});}
}
