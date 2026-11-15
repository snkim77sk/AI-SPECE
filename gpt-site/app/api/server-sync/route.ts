import {database} from '../../../lib/g2b-service';
import {prepareServerPage,serverPagePlan,readSyncBody,SYNC_PROTOCOL,SYNC_VERSION} from '../../../lib/server-sync.mjs';
export const dynamic='force-dynamic';
const headers={'Cache-Control':'no-store'};
// This shared updater relies on the existing owner-private Sites access boundary.
// It receives no visitor identity from supported OAI-Sites-Authorization access.
export async function POST(request:Request){
 try{
  const origin=request.headers.get('origin');if(origin&&origin!==new URL(request.url).origin)return Response.json({error:'SYNC_ORIGIN_REFUSED'},{status:403,headers});
  const {payload,requestDigest}=await readSyncBody(request),v=await prepareServerPage(payload),d=database();
  const plan=serverPagePlan(v,new Date().toISOString());await d.batch(plan.map(q=>d.prepare(q.sql).bind(...q.params)));
  const receipt=await d.prepare('SELECT * FROM server_sync_pages WHERE id=?').bind(v.id).first();
  return Response.json({ok:true,request_digest:requestDigest,receipt,providerCalls:0},{headers});
 }catch(e){const message=e instanceof Error?e.message:'';const code=/^SYNC_[A-Z_]+$/.test(message)?message:'SYNC_VALIDATION_OR_STORAGE_FAILED';return Response.json({ok:false,error:code,providerCalls:0},{status:422,headers});}
}
export async function GET(request:Request){
 try{
  const d=database(),id=new URL(request.url).searchParams.get('id');
  if(id){if(!/^[a-f0-9]{64}$/.test(id))return Response.json({error:'SYNC_INVALID_ID'},{status:400,headers});return Response.json({receipt:await d.prepare('SELECT * FROM server_sync_pages WHERE id=?').bind(id).first(),providerCalls:0},{headers});}
  const sources=(await d.prepare('SELECT dataset,COUNT(*) pages,SUM(source_rows) source_rows,SUM(matched_rows) matched_rows,MAX(source_updated_at) source_updated_at,MAX(received_at) received_at FROM server_sync_pages GROUP BY dataset').all()).results;
  const epochs=await d.prepare("SELECT (SELECT revision FROM dataset_epochs WHERE mode='live') delivery,(SELECT revision FROM budget_epochs WHERE mode='live') budget").first();
  return Response.json({version:SYNC_VERSION,protocol:SYNC_PROTOCOL,sources,epochs,providerCalls:0,sourceCompletenessVerified:false},{headers});
 }catch{return Response.json({error:'SYNC_STATUS_UNAVAILABLE'},{status:503,headers});}
}
