import {workspace,createSession,sessionState,sessionStep,automaticTick} from '../../../lib/g2b-service';
import {readBounded} from '../../../lib/collector.mjs';
export const dynamic='force-dynamic';
const reply=(body:unknown,status=200)=>Response.json(body,{status,headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
export async function GET(request:Request){try{return reply(await workspace(new URL(request.url).searchParams.get('id')));}catch{return reply({error:'DATABASE_UNAVAILABLE'},503);}}
export async function POST(request:Request){
 const origin=request.headers.get('origin');if(origin&&origin!==new URL(request.url).origin)return reply({error:'ORIGIN_NOT_ALLOWED'},403);
 if(!request.headers.get('content-type')?.startsWith('application/json'))return reply({error:'JSON_REQUIRED'},415);
 try{const {raw:b}=await readBounded(new Response(request.body),2048);let result;
 if(b.action==='automatic')result=await automaticTick(b.dryRun===true);
 else if(b.action==='create')result=await createSession(b.mode,b.from,b.to,b.pageSize,b.fixtureTotal);
 else if(typeof b.id==='string'&&/^[a-f0-9-]{36}$/.test(b.id)){
 if(b.action==='step')result=await sessionStep(b.id);
 else if(['pause','resume','cancel'].includes(b.action))result=await sessionState(b.id,b.action==='pause'?'paused':b.action==='resume'?'ready':'cancelled');else throw Error('INVALID_ACTION');
 }else throw Error('INVALID_INPUT');return reply(result);
 }catch(e){return reply({error:e instanceof Error&&/^[A-Z0-9_]+$/.test(e.message)?e.message:'REQUEST_FAILED'},409);}
}
