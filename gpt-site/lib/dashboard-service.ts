import {dashboardStage} from './dashboard-stage.mjs';
import{database,config}from'./g2b-service';import{budgetConfig}from'./budget-service';import{queryPlan}from'./g2b-query.mjs';
import {controlCountPlan,dashboardLogicalPlan} from './dashboard-query.mjs';
type SourceEpochs={delivery:string,budget:string,history:string,observations:string};
const count=(condition:string)=>`CAST(COALESCE(SUM(CASE WHEN ${condition} THEN 1 ELSE 0 END),0) AS TEXT)`;
// Treat optional stored JSON as untrusted structure, not as a typed response.
const record=(v:any)=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const exactFields=(v:any,fields:string[])=>record(v)&&Object.keys(v).length===fields.length&&fields.every(k=>Object.hasOwn(v,k));
const counts=(v:any,fields:string[])=>fields.every(k=>typeof v[k]==='string'&&/^(0|[1-9][0-9]{0,18})$/.test(v[k]));
const dates=(v:any,fields:string[])=>fields.every(k=>v[k]===null||(typeof v[k]==='string'&&v[k].length<=128));
const service=(v:any)=>v==='QWGJK'||v==='AIDFA';
function validAggregates(v:any){
 if(!record(v))return false;
 const dc=['stored_source_rows','active_source_rows','inactive_source_rows','latest_rows','requests','agency_identities','smart_control_rows'],dd=['first_source_date','last_source_date','updated_at'];
 const bc=['stored_rows','active_rows','inactive_rows','target_rows'];
 const oc=['observed_states','observed_dates'],od=['first_date','last_date'];
 return exactFields(v.delivery,[...dc,...dd])&&counts(v.delivery,dc)&&dates(v.delivery,dd)
  &&Array.isArray(v.budgets)&&v.budgets.length<=150&&v.budgets.every((r:any)=>exactFields(r,['service','fiscal_year',...bc,'updated_at'])&&service(r.service)&&Number.isInteger(r.fiscal_year)&&r.fiscal_year>=1000&&r.fiscal_year<=9999&&counts(r,bc)&&dates(r,['updated_at']))
  &&new Set(v.budgets.map((r:any)=>r.service+':'+r.fiscal_year)).size===v.budgets.length
  &&Array.isArray(v.revisions)&&v.revisions.length<=2&&v.revisions.every((r:any)=>exactFields(r,['service','unique_states','first_stored_snapshot','last_stored_snapshot'])&&service(r.service)&&counts(r,['unique_states'])&&dates(r,['first_stored_snapshot','last_stored_snapshot']))
  &&new Set(v.revisions.map((r:any)=>r.service)).size===v.revisions.length
  &&exactFields(v.observations,[...oc,...od])&&counts(v.observations,oc)&&dates(v.observations,od);
}
export async function dashboard(mode='live',progressive=false){
 if(!['live','synthetic'].includes(mode))throw Error('INVALID_MODE');const d=database(),g=config(),b=budgetConfig();
 const epochSQL="SELECT CAST(d.revision AS TEXT) delivery,CAST(b.revision AS TEXT) budget,COALESCE((SELECT CAST(revision AS TEXT) FROM budget_history_epochs WHERE mode=d.mode),'0') history,COALESCE((SELECT CAST(revision AS TEXT) FROM budget_observation_epochs WHERE mode=d.mode),'0') observations FROM dataset_epochs d JOIN budget_epochs b ON b.mode=d.mode WHERE d.mode=?";
 const epochs=()=>d.prepare(epochSQL).bind(mode).first<SourceEpochs>();
 const before=await epochs();if(!before)throw Error('DASHBOARD_SOURCE_NOT_FOUND');
 // Cache only source aggregates. Every epoch and the resolved date window are
 // part of the identity; readiness and request-time metadata remain uncached.
 const assertCurrent=async()=>{if(JSON.stringify(before)!==JSON.stringify(await epochs()))throw Error('DASHBOARD_SOURCE_CHANGED_RETRY');};
 const identity=JSON.stringify(['dashboard-aggregates-v1',mode,g.from,g.to,before]);
 const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(identity));
 const key=mode+':dashboard:'+Array.from(new Uint8Array(digest)).map(x=>x.toString(16).padStart(2,'0')).join('');
 const now=Date.now();
 let aggregates:any=null;
 try{
  const hit=await d.prepare('SELECT payload FROM query_cache WHERE cache_key=? AND mode=? AND CAST(revision AS TEXT)=? AND expires_at>? AND length(CAST(payload AS BLOB))<=524288').bind(key,mode,before.delivery,now).first<{payload:string}>();
  if(hit){const value=JSON.parse(hit.payload);if(value&&value.identity===identity&&validAggregates(value))aggregates={delivery:value.delivery,budgets:value.budgets,revisions:value.revisions,observations:value.observations};}
 }catch{/* Optional cache reads, including damaged entries, fall back to computation. */}
 const cacheHit=aggregates!==null;

 const empty={delivery:{stored_source_rows:'0',active_source_rows:'0',inactive_source_rows:'0',latest_rows:'0',requests:'0',agency_identities:'0',smart_control_rows:'0',first_source_date:null,last_source_date:null,updated_at:null},budgets:[],revisions:[],observations:{observed_states:'0',observed_dates:'0',first_date:null,last_date:null}};
 const validStage=(stage:string,v:any)=>{
  const fields:any={stored:['stored_source_rows','active_source_rows','inactive_source_rows','first_source_date','last_source_date','updated_at'],logical:['latest_rows','requests','agency_identities'],control:['rows']};
  if(stage==='control')return exactFields(v,['rows'])&&counts(v,['rows']);
  if(stage==='stored'||stage==='logical')return exactFields(v,fields[stage])&&validAggregates({...empty,delivery:{...empty.delivery,...v}});
  return validAggregates({...empty,[stage]:v});
 };
 const stage=async(name:string,compute:()=>Promise<any>)=>progressive?dashboardStage(d,{key,mode,revision:before.delivery,identity,stage:name,validate:(v:any)=>validStage(name,v),compute,assertCurrent}):compute();
 if(!aggregates){
 const started=Date.now();const phase=(stage:string)=>console.info(JSON.stringify({area:'dashboard-stage',stage,elapsedMs:Date.now()-started}));phase('start');
 const delivery=await stage('stored',async()=>await d.prepare(`SELECT CAST(COUNT(*) AS TEXT) stored_source_rows,${count('is_active=1')} active_source_rows,${count('is_active=0')} inactive_source_rows,MIN(source_date) first_source_date,MAX(source_date) last_source_date,MAX(updated_at) updated_at FROM delivery_rows INDEXED BY idx_delivery_dashboard_cover WHERE mode=?`).bind(mode).first());phase('delivery_stored');
 const p=dashboardLogicalPlan(mode,g),logical=await stage('logical',async()=>await d.prepare(`${p.prefix} SELECT CAST(COUNT(*) AS TEXT) latest_rows,CAST(COUNT(DISTINCT request_no) AS TEXT) requests,CAST(COUNT(DISTINCT CASE WHEN trim(agency_code)<>'' THEN 'C:'||agency_code WHEN trim(agency)<>'' THEN 'N:'||agency ELSE NULL END) AS TEXT) agency_identities FROM current WHERE ${p.where.join(' AND ')}`).bind(...p.args).first());phase('delivery_logical');
 const smart=controlCountPlan(mode,g),control=await stage('control',async()=>await d.prepare(`${smart.prefix} SELECT CAST(COUNT(*) AS TEXT) rows FROM current WHERE ${smart.where.join(' AND ')}`).bind(...smart.args).first());phase('delivery_control');
 const budgets=await stage('budgets',async()=>await (await d.prepare(`SELECT service,fiscal_year,CAST(COUNT(*) AS TEXT) stored_rows,${count('is_active=1')} active_rows,${count('is_active=0')} inactive_rows,${count("is_active=1 AND service='QWGJK' AND category IN ('LIGHTING','POLE') AND json_extract(row_json,'$.invalid_amount')=0 AND json_extract(row_json,'$.remaining_amount') IS NOT NULL AND json_extract(row_json,'$.remaining_amount')<>'0' AND substr(json_extract(row_json,'$.remaining_amount'),1,1)<>'-'")} target_rows,MAX(updated_at) updated_at FROM budget_rows WHERE mode=? GROUP BY service,fiscal_year ORDER BY fiscal_year DESC,service LIMIT 151`).bind(mode).all()).results);phase('budgets');
 if(budgets.length>150)throw Error('DASHBOARD_YEAR_GROUP_LIMIT');
 const revisions=await stage('revisions',async()=>await (await d.prepare("SELECT service,CAST(COUNT(*) AS TEXT) unique_states,MIN(first_snapshot) first_stored_snapshot,MAX(first_snapshot) last_stored_snapshot FROM budget_revisions WHERE mode=? GROUP BY service").bind(mode).all()).results);phase('revisions');
 const observations=await stage('observations',async()=>await d.prepare("SELECT CAST(COUNT(*) AS TEXT) observed_states,CAST(COUNT(DISTINCT snapshot_date) AS TEXT) observed_dates,MIN(snapshot_date) first_date,MAX(snapshot_date) last_date FROM budget_observations WHERE mode=?").bind(mode).first());phase('observations');
 aggregates={delivery:{...delivery,...logical,smart_control_rows:(control as any)?.rows||'0'},budgets,revisions,observations};
 // Never publish a mixed computation, even into an optional cache. Check again
 // after the write as well: a writer may commit during that awaited operation.
 await assertCurrent();
 const payload=JSON.stringify({identity,...aggregates});
 if(new TextEncoder().encode(payload).byteLength<=524288&&validAggregates(aggregates)){
  try{await d.batch([
   d.prepare(`INSERT INTO query_cache(cache_key,mode,revision,payload,created_at,expires_at) SELECT ?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM (${epochSQL}) WHERE delivery=? AND budget=? AND history=? AND observations=?) ON CONFLICT(cache_key) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,created_at=excluded.created_at,expires_at=excluded.expires_at`).bind(key,mode,before.delivery,payload,now,now+60000,mode,before.delivery,before.budget,before.history,before.observations),
   d.prepare('DELETE FROM query_cache WHERE mode=? AND cache_key NOT IN(SELECT cache_key FROM query_cache WHERE mode=? ORDER BY created_at DESC,cache_key DESC LIMIT 64)').bind(mode,mode)
  ]);}catch{/* Cache storage is optional; the fenced computed response remains usable. */}
 }
 }
 await assertCurrent();
 return{version:19,mode,asOf:new Date().toISOString(),window:{from:g.from,to:g.to,retentionMonths:27},sourceRevisions:before,...aggregates,aggregateCache:{hit:cacheHit,ttlSeconds:60,sourceBound:true},readiness:{g2b:{hasKey:g.hasKey,liveReady:g.liveReady,localDailyCap:g.budget,quotaScopeReviewed:g.quotaScopeReviewed,sharedQuotaEnforced:false,otherCallerUsageKnown:false},lofin:{hasKey:b.hasKey,liveReady:b.liveReady,localDailyCap:b.budget,quotaScopeReviewed:b.quotaScopeReviewed,sharedQuotaEnforced:false,otherCallerUsageKnown:false},scheduleInstalled:false},storage:{bytesMeasured:false,physicalRetentionEnabled:false},memory:{platformMiB:128,peakMeasured:false},sourceCompletenessVerified:false,completeDailyPresenceVerified:false,providerCalls:0};
}
