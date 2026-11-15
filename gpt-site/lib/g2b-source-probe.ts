import {env} from 'cloudflare:workers';
import {database,config} from './g2b-service';
import {ENDPOINT,kstDate,parsePage,readBounded} from './collector.mjs';
import {FAST_MAX_BYTES,identity,normalizeDelivery,dayOrder} from './g2b-domain.mjs';
import {pageCommitPlan} from './g2b-sql.mjs';
import {assertNoPageOverlap,collectionFailureCode,RECOVERY_SNAPSHOT_SQL,recoveryAssessment} from './g2b-diagnostics.mjs';

// A diagnostic is not a retry: the collection error, checkpoint, receipts and
// source rows are never changed. A durable latch permits one source attempt for
// this exact failed snapshot, even after lost responses or concurrent requests.
type SessionRow={id:string;mode:string;state:string;error:string|null;day:string;page:number;page_size:number;source_total:number|null;updated_at:string;calls:number;fetched:number;matched:number;lease_until:number;from_day:string;to_day:string;cursor:number};
type ProbeRow={id:string;session_id:string;day:string;page:number;status:string;result_json:string|null};
type ProbeResult={code:string;phase:string;responseBytes?:number;responseHash?:string;sourceRows?:number;targetRows?:number;preparedStatements?:number;collectionSnapshotUnchanged?:boolean;sourceRowsWritten?:number;collectionResumed?:boolean;automaticRetryAllowed?:boolean};
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const FENCE="s.id=? AND s.mode='live' AND s.state='error' AND s.error='STORAGE_OR_SOURCE_FAILED' AND s.day=? AND s.page=? AND s.updated_at=? AND s.calls=? AND s.fetched=? AND s.matched=? AND s.lease IS NULL AND s.lease_until=0";
const fingerprint=(s:SessionRow)=>[s.id,s.day,s.page,s.updated_at,s.calls,s.fetched,s.matched];
const describe=(r:ProbeRow|null)=>r?{requestId:r.id,sessionId:r.session_id,day:r.day,page:r.page,status:r.status,result:r.result_json?JSON.parse(r.result_json):null,sourceAttemptReserved:true,collectionResumed:false}:null;

export async function readSourceProbe(id:string){
 if(!UUID.test(id))throw Error('INVALID_PROBE_ID');
 return describe(await database().prepare('SELECT * FROM collection_source_probes WHERE id=?').bind(id).first<ProbeRow>());
}

export async function sourceProbe(id:string,sessionId:string){
 if(!UUID.test(id)||!UUID.test(sessionId))throw Error('INVALID_PROBE_ID');
 if(env.G2B_DIAGNOSTIC_PROBE_ENABLED!=='approved-once')throw Error('DIAGNOSTIC_PROBE_NOT_ENABLED');
 const d=database(),c=config();if(!c.liveReady)throw Error('LIVE_READINESS_REQUIRED');
 const existing=await d.prepare('SELECT * FROM collection_source_probes WHERE id=?').bind(id).first<ProbeRow>();
 if(existing){if(existing.session_id!==sessionId)throw Error('PROBE_REQUEST_CONFLICT');return describe(existing);}
 const s=await d.prepare(RECOVERY_SNAPSHOT_SQL).bind(sessionId).first<SessionRow>();
 const assessment=recoveryAssessment(s);
 if(!s)throw Error('COLLECTION_SESSION_NOT_FOUND');
 if(s?.mode!=='live'||s.state!=='error'||s.error!=='STORAGE_OR_SOURCE_FAILED'||s.lease_until!==0||!assessment.metadataConsistent||assessment.pageCommitState!=='NOT_COMMITTED')throw Error('DIAGNOSTIC_SNAPSHOT_NOT_ELIGIBLE');
 if(s.day<c.from||s.day>c.to)throw Error('DATE_OUTSIDE_CURRENT_SCOPE');
 // RECOVERY_SNAPSHOT_SQL omits page_size/source_total. Read the same fenced
 // snapshot without acquiring or modifying the collection's lease or state.
 const source=await d.prepare('SELECT * FROM collection_sessions s WHERE '+FENCE).bind(...fingerprint(s)).first<SessionRow>();
 if(!source)throw Error('DIAGNOSTIC_SNAPSHOT_CHANGED');
 const prior=await d.prepare('SELECT * FROM collection_source_probes WHERE session_id=? AND day=? AND page=? AND failed_at=?').bind(s.id,s.day,s.page,s.updated_at).first<ProbeRow>();
 if(prior)return describe(prior);
 const owner=crypto.randomUUID(),stamp=new Date().toISOString(),quotaDay=kstDate();
 const qs=[
  {sql:`INSERT INTO collection_source_probes(id,session_id,day,page,failed_at,attempt_owner,status,quota_day,created_at) SELECT ?,s.id,s.day,s.page,s.updated_at,?,'reserved',?,? FROM collection_sessions s WHERE ${FENCE} AND COALESCE((SELECT calls FROM usage WHERE day=?),0)<? ON CONFLICT DO NOTHING`,args:[id,owner,quotaDay,stamp,...fingerprint(s),quotaDay,c.budget]},
  {sql:"INSERT INTO usage(day,calls) SELECT ?,1 WHERE EXISTS(SELECT 1 FROM collection_source_probes WHERE id=? AND attempt_owner=? AND status='reserved') ON CONFLICT(day) DO UPDATE SET calls=calls+1 WHERE calls<?",args:[quotaDay,id,owner,c.budget]},
 ];
 // D1 batch is atomic and serialized. The insertion's quota check and the
 // increment cannot interleave with another caller. A lost batch acknowledgement
 // deliberately stops before fetch; the durable latch must never be replayed.
 let reserved;try{reserved=await d.batch(qs.map(q=>d.prepare(q.sql).bind(...q.args)));}catch{throw Error('DIAGNOSTIC_RESERVATION_OUTCOME_UNCERTAIN');}
 if(reserved[0]?.meta.changes!==1){const row=await d.prepare('SELECT * FROM collection_source_probes WHERE session_id=? AND day=? AND page=? AND failed_at=?').bind(s.id,s.day,s.page,s.updated_at).first<ProbeRow>();if(row)return describe(row);throw Error('DIAGNOSTIC_QUOTA_OR_SNAPSHOT_BLOCKED');}
 if(reserved[1]?.meta.changes!==1)throw Error('DIAGNOSTIC_RESERVATION_OUTCOME_UNCERTAIN');
 let phase='validate',result:ProbeResult;
 try{
  if(!(await d.prepare('SELECT s.id FROM collection_sessions s WHERE '+FENCE).bind(...fingerprint(s)).first()))throw Error('DIAGNOSTIC_SNAPSHOT_CHANGED');
  const current=config();if(!current.liveReady||env.G2B_DIAGNOSTIC_PROBE_ENABLED!=='approved-once')throw Error('LIVE_READINESS_REQUIRED');
  const url=new URL(ENDPOINT);for(const [key,value] of Object.entries({serviceKey:env.G2B_SERVICE_KEY,pageNo:source.page,numOfRows:source.page_size,type:'json',inqryDiv:'1',inqryBgnDate:source.day.replaceAll('-',''),inqryEndDate:source.day.replaceAll('-','')}))url.searchParams.set(key,String(value));
  // Never spend yesterday's reserved permit on today's provider allowance.
  // Keep the latch and consumed permit; an operator must inspect this outcome.
  if(kstDate()!==quotaDay)throw Error('DIAGNOSTIC_QUOTA_DAY_CHANGED');
  phase='source';let response;try{response=await fetch(url,{redirect:'error',signal:AbortSignal.timeout(25000)});}catch{throw Error('SOURCE_NETWORK_OR_TIMEOUT');}
  const parsed=await readBounded(response,FAST_MAX_BYTES,true);let raw=parsed.raw;const {bytes,hash}=parsed;
  phase='parse';const page=parsePage(raw,source.page,source.page_size,source.source_total,row=>JSON.stringify(identity(row)));
  phase='normalize';const rows=[];for(const row of page.rows){const normalized=normalizeDelivery(row,source.day,c,{includeSmart:true});if(normalized.row)rows.push(normalized.row);}raw=null;page.rows.length=0;
  phase='overlap-check';await assertNoPageOverlap(d,source,page.keys);
  phase='prepare-commit';const plan=pageCommitPlan(source,'diagnostic-no-commit',page.keys,rows,page.total,page.done,bytes,hash,stamp,dayOrder(source.from_day,source.to_day));
  result={code:'SOURCE_VALIDATION_PASSED_STORAGE_NOT_TESTED',phase,responseBytes:bytes,responseHash:hash,sourceRows:page.keys.length,targetRows:rows.length,preparedStatements:plan.length};
 }catch(error){const message=(error as Error)?.message;result={code:['DIAGNOSTIC_SNAPSHOT_CHANGED','DIAGNOSTIC_QUOTA_DAY_CHANGED'].includes(message)?message:collectionFailureCode(error,phase),phase};}
 const unchanged=!!await d.prepare('SELECT s.id FROM collection_sessions s WHERE '+FENCE).bind(...fingerprint(s)).first();
 result={...result,collectionSnapshotUnchanged:unchanged,sourceRowsWritten:0,collectionResumed:false,automaticRetryAllowed:false};
 // If this acknowledgement is lost, read the same request ID. Never issue a new
 // probe or treat reserved as proof that the source call did/did not occur.
 try{await d.prepare("UPDATE collection_source_probes SET status='complete',result_json=?,completed_at=? WHERE id=? AND attempt_owner=? AND status='reserved'").bind(JSON.stringify(result),new Date().toISOString(),id,owner).run();}catch{throw Error('DIAGNOSTIC_RESULT_OUTCOME_UNCERTAIN');}
 return readSourceProbe(id);
}
