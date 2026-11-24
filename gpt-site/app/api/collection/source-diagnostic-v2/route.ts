import {guardPublicRequest} from '../../../../lib/public-auth-service';
import {readSourceDiagnosticV2,sourceDiagnosticV2} from '../../../../lib/g2b-source-diagnostic-v2';

export const dynamic='force-dynamic';
const reply=(body:unknown,status=200)=>Response.json(body,{status,headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
export async function GET(request:Request){const __publicBlock=await guardPublicRequest(request);if(__publicBlock)return __publicBlock;try{return reply(await readSourceDiagnosticV2(new URL(request.url).searchParams.get('id')||''));}catch{return reply({error:'DIAGNOSTIC_READ_UNAVAILABLE'},409);}}
export async function POST(request:Request){const __publicBlock=await guardPublicRequest(request);if(__publicBlock)return __publicBlock;
 const started=Date.now();
 const origin=request.headers.get('origin');if(origin&&origin!==new URL(request.url).origin)return reply({error:'ORIGIN_NOT_ALLOWED'},403);
 if(!request.headers.get('content-type')?.startsWith('application/json'))return reply({error:'JSON_REQUIRED'},415);
 const reader=request.body?.getReader();let timer:ReturnType<typeof setTimeout>|undefined;
 try{
  if(!reader)throw Error('BODY_REQUIRED');
  const timeout=new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('REQUEST_TIMEOUT')),2000);});
  const chunks:Uint8Array[]=[];let length=0;
  while(true){const part=await Promise.race([reader.read(),timeout]);if(part.done)break;length+=part.value.length;if(length>2048)throw Error('BODY_TOO_LARGE');chunks.push(part.value);}
  clearTimeout(timer);const bytes=new Uint8Array(length);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
  const b=JSON.parse(new TextDecoder().decode(bytes));
  if(b?.action!=='diagnose-once')return reply({error:'INVALID_ACTION'},409);
  return reply(await sourceDiagnosticV2(b.requestId,b.sessionId,b.priorProbeId,started));
 }
 // Never echo arbitrary error messages, even uppercase secret-bearing strings.
 catch{return reply({error:'DIAGNOSTIC_V2_FAILED_READ_SAME_ID'},409);}finally{clearTimeout(timer);try{void reader?.cancel().catch(()=>{});}catch{}}
}
