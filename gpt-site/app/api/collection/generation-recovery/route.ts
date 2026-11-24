import {guardPublicRequest} from '../../../../lib/public-auth-service';
import {recoveryGeneration} from '../../../../lib/g2b-generation-service';

export const dynamic='force-dynamic';
const reply=(body:unknown,status=200)=>Response.json(body,{status,headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
export async function GET(request:Request){const __publicBlock=await guardPublicRequest(request);if(__publicBlock)return __publicBlock;try{return reply(await recoveryGeneration().read(new URL(request.url).searchParams.get('id')||'',new URL(request.url).searchParams.get('runId')||undefined));}catch{return reply({error:'GENERATION_READ_UNAVAILABLE'},409);}}
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
  const service=recoveryGeneration(),ids={generationId:b.generationId,rootId:b.rootId,diagnosticId:b.diagnosticId};
  if(b.action==='create-staging')return reply(await service.create(ids));
  if(b.action==='run-staging')return reply(await service.run(ids,b.runId,started));
  if(b.action==='paused'||b.action==='cancelled')return reply(await service.control(ids,b.action));
  return reply({error:'INVALID_ACTION'},409);
 }
 // Never echo arbitrary error messages, even uppercase secret-bearing strings.
 catch{return reply({error:'GENERATION_FAILED_READ_SAME_ID'},409);}finally{clearTimeout(timer);try{void reader?.cancel().catch(()=>{});}catch{}}
}
