import {diagnosticFetch} from './g2b-network-diagnostic.mjs';
import {env} from 'cloudflare:workers';
import {database,config} from './g2b-service';
import {kstDate,parsePage} from './collector.mjs';
import {identity,normalizeDelivery,dayOrder} from './g2b-domain.mjs';
import {pageCommitPlan} from './g2b-sql.mjs';
import {assertNoPageOverlap,collectionFailureCode,RECOVERY_SNAPSHOT_SQL,recoveryAssessment} from './g2b-diagnostics.mjs';

// A diagnostic is not a retry: the collection error, checkpoint, receipts and
// source rows are never changed. A durable latch permits one source attempt for
// this exact failed snapshot, even after lost responses or concurrent requests.
type SessionRow={id:string;mode:string;state:string;error:string|null;day:string;page:number;page_size:number;source_total:number|null;updated_at:string;calls:number;fetched:number;matched:number;lease_until:number;from_day:string;to_day:string;cursor:number};
type ProbeRow={id:string;session_id:string;day:string;page:number;status:string;result_json:string|null;prior_probe_id:string};
type ProbeResult={elapsedMs?:number;httpStatus?:number;timeoutObserved?:boolean;errorName?:string;errorCategory?:string;code:string;phase:string;responseBytes?:number;responseHash?:string;sourceRows?:number;targetRows?:number;preparedStatements?:number;collectionSnapshotUnchanged?:boolean;sourceRowsWritten?:number;collectionResumed?:boolean;automaticRetryAllowed?:boolean};
const safeFailure=(error:unknown,phase:string)=>{try{return collectionFailureCode(error,phase);}catch{return 'SOURCE_PROCESSING_FAILED';}};
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const FENCE="s.id=? AND s.mode='live' AND s.state='error' AND s.error='STORAGE_OR_SOURCE_FAILED' AND s.day=? AND s.page=? AND s.updated_at=? AND s.calls=? AND s.fetched=? AND s.matched=? AND s.lease IS NULL AND s.lease_until=0";
const EXTRA=' AND s.page_size=? AND s.source_total IS ? AND s.from_day=? AND s.to_day=? AND s.cursor=?';
const fingerprint=(s:SessionRow)=>[s.id,s.day,s.page,s.updated_at,s.calls,s.fetched,s.matched];
const describe=(r:ProbeRow|null)=>r?{requestId:r.id,sessionId:r.session_id,day:r.day,page:r.page,status:r.status,result:r.result_json?JSON.parse(r.result_json):null,sourceAttemptReserved:true,collectionResumed:false}:null;

export async function readSourceDiagnosticV2(id:string){
 if(!UUID.test(id))throw Error('INVALID_PROBE_ID');
 return describe(await database().prepare('SELECT * FROM collection_source_diagnostics_v2 WHERE id=?').bind(id).first<ProbeRow>());
}

export async function sourceDiagnosticV2(id:string,sessionId:string,priorProbeId:string,started=Date.now()){
 const deadline=started+90000;
 const gate=()=>{if(env.G2B_DIAGNOSTIC_V2_ENABLED!=='approved-once'||env.G2B_DIAGNOSTIC_V2_REQUEST_ID!==id||env.G2B_DIAGNOSTIC_V2_SESSION_ID!==sessionId||env.G2B_DIAGNOSTIC_V2_PRIOR_PROBE_ID!==priorProbeId)throw Error('DIAGNOSTIC_V2_NOT_ENABLED');};
 gate();
 if(!UUID.test(id)||!UUID.test(sessionId)||!UUID.test(priorProbeId))throw Error('INVALID_PROBE_ID');
 const rawDb=database();
 // Bound every awaited storage operation too. An expired write is uncertain, never replayed.
 const bounded=async <T>(p:Promise<T>):Promise<T>=>{let timer:ReturnType<typeof setTimeout>|undefined;try{return await Promise.race([p,new Promise<T>((_,reject)=>{timer=setTimeout(()=>reject(Error('DIAGNOSTIC_DEADLINE')),Math.max(0,deadline-Date.now()));})]);}finally{clearTimeout(timer);}};
 const originals=new WeakMap<D1PreparedStatement,D1PreparedStatement>();
 const wrap=(q:D1PreparedStatement):D1PreparedStatement=>{const proxy=new Proxy(q,{get(target,property){if(property==='bind')return (...args:unknown[])=>wrap(target.bind(...args));if(['first','all','run','raw'].includes(String(property)))return (...args:unknown[])=>bounded((target[property as keyof D1PreparedStatement] as (...v:unknown[])=>Promise<unknown>).apply(target,args));return Reflect.get(target,property);}});originals.set(proxy,q);return proxy;};
 const d={prepare:(sql:string)=>wrap(rawDb.prepare(sql)),batch:<T>(qs:D1PreparedStatement[])=>bounded(rawDb.batch<T>(qs.map(q=>originals.get(q)||q)))},c=config();if(!c.liveReady)throw Error('LIVE_READINESS_REQUIRED');
 const existing=await d.prepare('SELECT * FROM collection_source_diagnostics_v2 WHERE id=?').bind(id).first<ProbeRow>();
 if(existing){if(existing.session_id!==sessionId||existing.prior_probe_id!==priorProbeId)throw Error('PROBE_REQUEST_CONFLICT');return describe(existing);}
 const s=await d.prepare(RECOVERY_SNAPSHOT_SQL).bind(sessionId).first<SessionRow>();
 const assessment=recoveryAssessment(s);
 if(!s)throw Error('COLLECTION_SESSION_NOT_FOUND');
 if(s?.mode!=='live'||s.state!=='error'||s.error!=='STORAGE_OR_SOURCE_FAILED'||s.lease_until!==0||!assessment.metadataConsistent||assessment.pageCommitState!=='NOT_COMMITTED')throw Error('DIAGNOSTIC_SNAPSHOT_NOT_ELIGIBLE');
 if(s.day<c.from||s.day>c.to)throw Error('DATE_OUTSIDE_CURRENT_SCOPE');
 // RECOVERY_SNAPSHOT_SQL omits page_size/source_total. Read the same fenced
 // snapshot without acquiring or modifying the collection's lease or state.
 const source=await d.prepare('SELECT * FROM collection_sessions s WHERE '+FENCE).bind(...fingerprint(s)).first<SessionRow>();
 if(!source)throw Error('DIAGNOSTIC_SNAPSHOT_CHANGED');
 const old=await d.prepare("SELECT * FROM collection_source_probes WHERE id=? AND session_id=? AND day=? AND page=? AND failed_at=? AND status='complete' AND completed_at IS NOT NULL").bind(priorProbeId,s.id,s.day,s.page,s.updated_at).first<{result_json:string}>();
 let oldResult;try{oldResult=JSON.parse(old?.result_json||'null');}catch{}
 if(!oldResult||oldResult.code!=='SOURCE_NETWORK_OR_TIMEOUT'||oldResult.phase!=='source'||oldResult.collectionSnapshotUnchanged!==true||oldResult.sourceRowsWritten!==0||oldResult.collectionResumed!==false||oldResult.automaticRetryAllowed!==false)throw Error('DIAGNOSTIC_PRIOR_PROBE_NOT_ELIGIBLE');
 const fence=FENCE+EXTRA,values=[...fingerprint(s),source.page_size,source.source_total,source.from_day,source.to_day,source.cursor];
 const prior=await d.prepare('SELECT * FROM collection_source_diagnostics_v2 WHERE prior_probe_id=? OR (session_id=? AND day=? AND page=? AND failed_at=?)').bind(priorProbeId,s.id,s.day,s.page,s.updated_at).first<ProbeRow>();
 if(prior)return describe(prior);
 if(Date.now()>=deadline)throw Error('DIAGNOSTIC_DEADLINE');
 const owner=crypto.randomUUID(),stamp=new Date().toISOString(),quotaDay=kstDate();
 const qs=[
  {sql:`INSERT INTO collection_source_diagnostics_v2(id,prior_probe_id,session_id,day,page,failed_at,attempt_owner,status,quota_day,created_at) SELECT ?,?,s.id,s.day,s.page,s.updated_at,?,'reserved',?,? FROM collection_sessions s WHERE ${fence} AND EXISTS(SELECT 1 FROM collection_source_probes p WHERE p.id=? AND p.session_id=s.id AND p.day=s.day AND p.page=s.page AND p.failed_at=s.updated_at AND p.completed_at IS NOT NULL AND p.status='complete' AND p.result_json=?) AND COALESCE((SELECT calls FROM usage WHERE day=?),0)<? ON CONFLICT DO NOTHING`,args:[id,priorProbeId,owner,quotaDay,stamp,...values,priorProbeId,old!.result_json,quotaDay,c.budget]},
  {sql:"INSERT INTO usage(day,calls) SELECT ?,1 WHERE EXISTS(SELECT 1 FROM collection_source_diagnostics_v2 WHERE id=? AND attempt_owner=? AND status='reserved') ON CONFLICT(day) DO UPDATE SET calls=calls+1 WHERE calls<?",args:[quotaDay,id,owner,c.budget]},
 ];
 // D1 batch is atomic and serialized. The insertion's quota check and the
 // increment cannot interleave with another caller. A lost batch acknowledgement
 // deliberately stops before fetch; the durable latch must never be replayed.
 let reserved;try{reserved=await d.batch(qs.map(q=>d.prepare(q.sql).bind(...q.args)));}catch{throw Error('DIAGNOSTIC_RESERVATION_OUTCOME_UNCERTAIN');}
 if(reserved[0]?.meta.changes!==1){const row=await d.prepare('SELECT * FROM collection_source_diagnostics_v2 WHERE session_id=? AND day=? AND page=? AND failed_at=?').bind(s.id,s.day,s.page,s.updated_at).first<ProbeRow>();if(row)return describe(row);throw Error('DIAGNOSTIC_QUOTA_OR_SNAPSHOT_BLOCKED');}
 if(reserved[1]?.meta.changes!==1)throw Error('DIAGNOSTIC_RESERVATION_OUTCOME_UNCERTAIN');
 let phase='validate',result:ProbeResult;
 const metrics:{phase:string;timeoutObserved:boolean;httpStatus?:number;errorName?:string;errorCategory?:string}={phase,timeoutObserved:false};
 try{
  if(!(await d.prepare('SELECT s.id FROM collection_sessions s WHERE '+fence).bind(...values).first()))throw Error('DIAGNOSTIC_SNAPSHOT_CHANGED');
  const current=config();gate();if(!current.liveReady||current.budget!==c.budget)throw Error('LIVE_READINESS_REQUIRED');
  const fresh=await d.prepare(RECOVERY_SNAPSHOT_SQL).bind(sessionId).first<SessionRow>();
  const checked=recoveryAssessment(fresh);if(!checked.metadataConsistent||checked.pageCommitState!=='NOT_COMMITTED')throw Error('DIAGNOSTIC_SNAPSHOT_CHANGED');
  if(!(await d.prepare('SELECT s.id FROM collection_sessions s WHERE '+fence).bind(...values).first()))throw Error('DIAGNOSTIC_SNAPSHOT_CHANGED');
  if(!(await d.prepare("SELECT s.id FROM collection_sessions s WHERE "+fence+" AND EXISTS(SELECT 1 FROM collection_source_probes p WHERE p.id=? AND p.session_id=s.id AND p.day=s.day AND p.page=s.page AND p.failed_at=s.updated_at AND p.status='complete' AND p.completed_at IS NOT NULL AND p.result_json=?)").bind(...values,priorProbeId,old!.result_json).first()))throw Error('DIAGNOSTIC_PRIOR_PROBE_NOT_ELIGIBLE');
  gate();if(!config().liveReady)throw Error('LIVE_READINESS_REQUIRED');if(kstDate()!==quotaDay)throw Error('DIAGNOSTIC_QUOTA_DAY_CHANGED');
  if(Date.now()>=deadline)throw Error('DIAGNOSTIC_DEADLINE');
  phase='source';const parsed=await diagnosticFetch(source,env.G2B_SERVICE_KEY,deadline,metrics);let raw=parsed.raw;const {bytes,hash}=parsed;
  phase='parse';const page=parsePage(raw,source.page,source.page_size,source.source_total,row=>JSON.stringify(identity(row)));
  phase='normalize';const rows=[];for(const row of page.rows){const normalized=normalizeDelivery(row,source.day,c,{includeSmart:true});if(normalized.row)rows.push(normalized.row);}raw=null;page.rows.length=0;
  phase='overlap-check';await assertNoPageOverlap(d,source,page.keys);
  phase='prepare-commit';const plan=pageCommitPlan(source,'diagnostic-no-commit',page.keys,rows,page.total,page.done,bytes,hash,stamp,dayOrder(source.from_day,source.to_day));
  if(Date.now()>=deadline)throw Error('DIAGNOSTIC_DEADLINE');
  result={code:'SOURCE_VALIDATION_PASSED_STORAGE_NOT_TESTED',phase,responseBytes:bytes,responseHash:hash,sourceRows:page.keys.length,targetRows:rows.length,preparedStatements:plan.length};
 }catch(error){let message='';try{const value=(error as Error)?.message;if(typeof value==='string')message=value;}catch{}if(phase==='source')phase=metrics.phase;result={code:['DIAGNOSTIC_PRIOR_PROBE_NOT_ELIGIBLE','DIAGNOSTIC_SNAPSHOT_CHANGED','DIAGNOSTIC_QUOTA_DAY_CHANGED','DIAGNOSTIC_V2_NOT_ENABLED','DIAGNOSTIC_TIMEOUT','DIAGNOSTIC_DEADLINE','SOURCE_FETCH_FAILED','SOURCE_REDIRECT'].includes(message)?message:safeFailure(error,phase),phase};}
 const unchanged=!!await d.prepare('SELECT s.id FROM collection_sessions s WHERE '+fence).bind(...values).first();
 result={...result,elapsedMs:Math.max(0,Date.now()-started),timeoutObserved:metrics.timeoutObserved,...(metrics.httpStatus!==undefined?{httpStatus:metrics.httpStatus}:{}),...(metrics.errorName?{errorName:metrics.errorName,errorCategory:metrics.errorCategory}:{}),collectionSnapshotUnchanged:unchanged,sourceRowsWritten:0,collectionResumed:false,automaticRetryAllowed:false};
 // If this acknowledgement is lost, read the same request ID. Never issue a new
 // probe or treat reserved as proof that the source call did/did not occur.
 try{await d.prepare("UPDATE collection_source_diagnostics_v2 SET status='complete',result_json=?,completed_at=? WHERE id=? AND attempt_owner=? AND status='reserved'").bind(JSON.stringify(result),new Date().toISOString(),id,owner).run();}catch{throw Error('DIAGNOSTIC_RESULT_OUTCOME_UNCERTAIN');}
 return describe(await d.prepare('SELECT * FROM collection_source_diagnostics_v2 WHERE id=?').bind(id).first<ProbeRow>());
}
