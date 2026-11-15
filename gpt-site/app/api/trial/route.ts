import {readBounded} from '../../../lib/collector.mjs';
import { status,start,step,changeState } from '../../../lib/trial';
export const dynamic='force-dynamic';
function reply(body:unknown,code=200){return Response.json(body,{status:code,headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});}
export async function GET(request:Request){try{return reply(await status(new URL(request.url).searchParams.get('id')));}catch{return reply({error:'DATABASE_UNAVAILABLE'},503);}}
export async function POST(request:Request){
 const origin=request.headers.get('origin');
 if(origin&&origin!==new URL(request.url).origin)return reply({error:'ORIGIN_NOT_ALLOWED'},403);
 if(!request.headers.get('content-type')?.startsWith('application/json'))return reply({error:'JSON_REQUIRED'},415);
 if(Number(request.headers.get('content-length')||0)>2048)return reply({error:'REQUEST_TOO_LARGE'},413);
 try{
  const {raw:body}=await readBounded(new Response(request.body),2048);let result;
  if(body.action==='start')result=await start(body.mode||'synthetic',body.day,Number(body.total||255));
  else if(typeof body.id==='string'&&/^[a-f0-9-]{36}$/.test(body.id)){
   if(body.action==='step')result=await step(body.id);
   else if(body.action==='pause')result=await changeState(body.id,'paused');
   else if(body.action==='resume')result=await changeState(body.id,'ready');
   else return reply({error:'INVALID_ACTION'},400);
  }else return reply({error:'INVALID_INPUT'},400);
  return reply(result);
 }catch(e){const message=e instanceof Error&&/^[A-Z0-9_]+$/.test(e.message)?e.message:'REQUEST_FAILED';return reply({error:message},409);}
}
