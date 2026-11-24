import {guardPublicRequest} from '../../../lib/public-auth-service';
import {database} from '../../../lib/g2b-service';
import {readSyncBody} from '../../../lib/server-sync.mjs';
import {prepareCollectorStatus,collectorStatusPlan,STATUS_MAX_BYTES} from '../../../lib/server-collector-status.mjs';
export const dynamic='force-dynamic';
const headers={'Cache-Control':'no-store'};
const read=async()=>{const row:any=await database().prepare("SELECT * FROM server_collector_status WHERE id='windows-primary'").first();return row?{payload:JSON.parse(row.payload_json),digest:row.digest,received_at:row.received_at}:null;};
export async function GET(request:Request){const __publicBlock=await guardPublicRequest(request);if(__publicBlock)return __publicBlock;try{return Response.json({snapshot:await read(),providerCalls:0},{headers});}catch{return Response.json({error:'STATUS_READ_FAILED',providerCalls:0},{status:503,headers});}}
export async function POST(request:Request){const __publicBlock=await guardPublicRequest(request);if(__publicBlock)return __publicBlock;
 try{
  const origin=request.headers.get('origin');if(origin&&origin!==new URL(request.url).origin)return Response.json({error:'STATUS_ORIGIN_REFUSED'},{status:403,headers});
  const {payload,requestDigest}=await readSyncBody(request,STATUS_MAX_BYTES),v=await prepareCollectorStatus(payload),q=collectorStatusPlan(v,new Date().toISOString());
  await database().prepare(q.sql).bind(...q.params).run();
  const snapshot=await read();
  if(snapshot?.digest!==v.digest)return Response.json({ok:false,error:'STATUS_NEWER_SNAPSHOT_EXISTS',providerCalls:0},{status:409,headers});
  return Response.json({ok:true,request_digest:requestDigest,snapshot,providerCalls:0},{headers});
 }catch(e){const message=e instanceof Error?e.message:'';return Response.json({ok:false,error:/^STATUS_[A-Z_]+$/.test(message)?message:'STATUS_VALIDATION_OR_STORAGE_FAILED',providerCalls:0},{status:422,headers});}
}
