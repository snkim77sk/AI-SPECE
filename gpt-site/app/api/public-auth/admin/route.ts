import {database} from '../../../../lib/g2b-service';
import {requireAdmin,publicOrigin} from '../../../../lib/public-auth-service';
export const dynamic='force-dynamic';
const headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'};
const reply=(body:unknown,status=200)=>Response.json(body,{status,headers});
export async function GET(request:Request){
 try{
  await requireAdmin(request);
  const rows=(await database().prepare("SELECT id,display_name,email,status,role,created_at,updated_at,reviewed_at FROM public_members WHERE role='member' ORDER BY CASE WHEN status='pending' THEN 0 ELSE 1 END,created_at DESC LIMIT 100").all()).results;
  return reply({members:rows});
 }catch(e){return reply({error:(e as Error)?.message==='ADMIN_REQUIRED'?'ADMIN_REQUIRED':'AUTH_STORAGE_UNAVAILABLE'},403);}
}
export async function POST(request:Request){
 const origin=publicOrigin();
 if(!origin||request.headers.get('origin')!==origin||new URL(request.url).origin!==origin)return reply({error:'ORIGIN_NOT_ALLOWED'},403);
 if(request.headers.get('content-type')?.split(';')[0]!=='application/json')return reply({error:'JSON_REQUIRED'},415);
 try{
  const admin=await requireAdmin(request);
  const body=await request.text();if(body.length>2048)return reply({error:'INVALID_INPUT'},400);
  let input;try{input=JSON.parse(body);}catch{return reply({error:'INVALID_INPUT'},400);}
  const memberId=input?.id,status=input?.status;
  if(typeof memberId!=='string'||!/^[a-f0-9-]{36}$/.test(memberId)||!['approved','rejected','suspended'].includes(status)||memberId===admin.id)return reply({error:'INVALID_INPUT'},400);
  const now=new Date().toISOString(),db=database();
  const updated=await db.prepare("UPDATE public_members SET status=?,reviewed_by=?,reviewed_at=?,updated_at=? WHERE id=? AND role='member' AND status IN ('pending','approved','rejected','suspended')").bind(status,admin.id,now,now,memberId).run();
  if(!updated.meta.changes)return reply({error:'MEMBER_NOT_FOUND'},404);
  // Every request reads current member status: revocation/suspension is immediate.
  return reply({ok:true,id:memberId,status});
 }catch(e){return reply({error:(e as Error)?.message==='ADMIN_REQUIRED'?'ADMIN_REQUIRED':'AUTH_STORAGE_UNAVAILABLE'},403);}
}
