import {guardPublicRequest} from '../../../../lib/public-auth-service';
import {readSourceProbe,sourceProbe} from '../../../../lib/g2b-source-probe';
import {readBounded} from '../../../../lib/collector.mjs';
export const dynamic='force-dynamic';
const reply=(body:unknown,status=200)=>Response.json(body,{status,headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
export async function GET(request:Request){const __publicBlock=await guardPublicRequest(request);if(__publicBlock)return __publicBlock;try{return reply(await readSourceProbe(new URL(request.url).searchParams.get('id')||''));}catch{return reply({error:'DIAGNOSTIC_READ_UNAVAILABLE'},409);}}
export async function POST(request:Request){const __publicBlock=await guardPublicRequest(request);if(__publicBlock)return __publicBlock;
 const origin=request.headers.get('origin');if(origin&&origin!==new URL(request.url).origin)return reply({error:'ORIGIN_NOT_ALLOWED'},403);
 if(!request.headers.get('content-type')?.startsWith('application/json'))return reply({error:'JSON_REQUIRED'},415);
 try{const {raw:b}=await readBounded(new Response(request.body),2048);if(b?.action!=='diagnose-once')throw Error('INVALID_ACTION');return reply(await sourceProbe(b.requestId,b.sessionId));}
 catch(e){return reply({error:e instanceof Error&&/^[A-Z0-9_]+$/.test(e.message)?e.message:'DIAGNOSTIC_FAILED'},409);}
}
