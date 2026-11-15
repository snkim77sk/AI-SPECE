import {assertNoPageOverlap,reportCollectionFailure,RECOVERY_SNAPSHOT_SQL,recoveryAssessment} from './g2b-diagnostics.mjs';
import{sourceReadinessBlock}from'./source-readiness.mjs';
import {regionChoices} from './read-filters.mjs';
import { env } from 'cloudflare:workers';
import {ENDPOINT,DAILY_LIMIT,kstDate,parsePage,readBounded} from './collector.mjs';
import {scopeWindow,dayOrder,FAST_PAGE_SIZE,FAST_MAX_BYTES,identity,normalizeDelivery,mixedFixture,csvCell,LIGHTING_CODES,POLE_CODES} from './g2b-domain.mjs';
import {SESSION_ACQUIRE,SESSION_STATE,SESSION_ERROR,pageCommitPlan} from './g2b-sql.mjs';
import {QUOTA_SQL} from './persistence.mjs';
import {cachedQuery} from './g2b-cache';
import {automaticDecision} from './g2b-automation.mjs';
import {queryPlan,summaryQuery,summaryPreflightQuery,deliverySummaryMoneyState,DELIVERY_SUMMARY_LIMIT} from './g2b-query.mjs';
export function database(){if(!env.DB)throw Error('DATABASE_UNAVAILABLE');return env.DB;}
export function config(){const window=scopeWindow();const configured=Number(env.G2B_DAILY_BUDGET||0);const budget=Number.isInteger(configured)&&configured>0&&configured<=DAILY_LIMIT?configured:0;return {...window,hasKey:!!env.G2B_SERVICE_KEY,approved:env.G2B_LIVE_ENABLED==='approved',budget,dailyMaximum:DAILY_LIMIT,quotaScopeReviewed:env.G2B_QUOTA_SCOPE_REVIEWED==='allocation-confirmed',sharedQuotaEnforced:false,liveReady:!!env.G2B_SERVICE_KEY&&env.G2B_LIVE_ENABLED==='approved'&&budget>0&&env.G2B_QUOTA_SCOPE_REVIEWED==='allocation-confirmed',pageSize:FAST_PAGE_SIZE,maxResponseBytes:FAST_MAX_BYTES,automaticSchedule:false,automaticRunnerEnabled:env.G2B_AUTOCOLLECT_ENABLED==='enabled',quotaTimezone:'Asia/Seoul',providerDailyLimitReported:1000,otherCallerUsageKnown:false,cloudMemoryMeasured:false,lightingCodes:[...LIGHTING_CODES],poleCodes:[...POLE_CODES]};}
export async function workspace(id?:string|null,includeSummaries=true,liveOnly=false){
 const d=database(),c=config(),sessions=(await d.prepare(`SELECT * FROM collection_sessions ${liveOnly?"WHERE mode='live'":""} ORDER BY created_at DESC LIMIT 8`).all()).results;
 const session=id?await d.prepare('SELECT * FROM collection_sessions WHERE id=?').bind(id).first():sessions[0]||null;
 const receipts=session?(await d.prepare('SELECT * FROM collection_receipts WHERE session_id=? ORDER BY committed_at DESC LIMIT 8').bind((session as any).id).all()).results:[];
 const checkpoints=session?(await d.prepare('SELECT * FROM day_checkpoints WHERE session_id=? ORDER BY updated_at DESC LIMIT 10').bind((session as any).id).all()).results:[];
 const usage=await d.prepare('SELECT calls FROM usage WHERE day=?').bind(kstDate()).first<any>();
 let summaries:any[]|undefined;
 if(includeSummaries){summaries=[];for(const mode of liveOnly?['live']:['live','synthetic']){const p=queryPlan(new URLSearchParams({mode}),c);const part=await cachedQuery(d,mode,['summary-v1',c.from,c.to],async()=>(await d.prepare(`${p.prefix} SELECT category,COUNT(*) AS count,COUNT(DISTINCT agency) AS agencies,MAX(updated_at) AS latest FROM current WHERE ${p.where.join(' AND ')} GROUP BY category`).bind(...p.args).all()).results);summaries.push(...part.map((x:any)=>({...x,mode})));}}
 return {config:c,session,sessions,receipts,checkpoints,summaries,dailyCalls:usage?.calls||0};
}
export async function createSession(mode:string,from:string,to:string,pageSize=FAST_PAGE_SIZE,fixtureTotal=2105){
 const c=config();if(!['live','synthetic'].includes(mode)||from<c.from||to>c.to)throw Error('INVALID_DATE_RANGE');
 if(![50,250,500,999].includes(pageSize)||!Number.isSafeInteger(fixtureTotal)||fixtureTotal<1||fixtureTotal>50000)throw Error('INVALID_RUN_SIZE');
 const order=dayOrder(from,to);if(mode==='live'&&!c.liveReady)throw Error(sourceReadinessBlock(c)||'LIVE_READINESS_REQUIRED');
 const id=crypto.randomUUID(),now=new Date().toISOString(),d=database();
 const result=await d.prepare("INSERT INTO collection_sessions(id,mode,from_day,to_day,state,cursor,day,page,page_size,source_total,fixture_total,fetched,matched,excluded,completed_days,calls,max_payload,lease_until,created_at,updated_at) SELECT ?,?,?,?,'ready',0,?,1,?,NULL,?,0,0,0,0,0,0,0,?,? WHERE ?='synthetic' OR NOT EXISTS(SELECT 1 FROM collection_sessions WHERE mode='live' AND state NOT IN ('complete','cancelled'))").bind(id,mode,from,to,order[0],mode==='live'?FAST_PAGE_SIZE:pageSize,fixtureTotal,now,now,mode).run();
 if(!result.meta.changes)throw Error('LIVE_SESSION_ALREADY_EXISTS');return workspace(id,true,true);
}
export async function sessionState(id:string,state:string){if(!['ready','paused','cancelled'].includes(state))throw Error('INVALID_STATE');const r=await database().prepare(SESSION_STATE.replace('error=NULL',"error=CASE WHEN state='running' AND (error IS NULL OR error='') THEN 'EXPIRED_REQUEST_REVIEW_REQUIRED' WHEN state='error' AND (error IS NULL OR error='') THEN 'COLLECTION_ERROR_UNCLASSIFIED' ELSE error END").replace('updated_at=?',"updated_at=CASE WHEN (error IS NULL OR error='') AND state NOT IN ('running','error') THEN ? ELSE updated_at END")).bind(state,new Date().toISOString(),id,Date.now()).run();if(!r.meta.changes)throw Error('BUSY_OR_COMPLETE');return workspace(id,true,true);}
export async function sessionStep(id:string){
 const d=database(),token=crypto.randomUUID(),now=Date.now();const r=await d.prepare(SESSION_ACQUIRE+" AND state<>'running' AND NOT(state='error' AND (error IS NULL OR error='')) AND (error IS NULL OR error='' OR (error='SOURCE_NETWORK_OR_TIMEOUT' AND updated_at<=?) OR (error='LOCAL_DAILY_BUDGET_REACHED' AND date(updated_at,'+9 hours')<?))").bind(token,now+90000,id,now,new Date(now-900000).toISOString(),kstDate()).run();if(!r.meta.changes)throw Error('BUSY_PAUSED_COMPLETE_OR_REVIEW_REQUIRED');
 const s=await d.prepare('SELECT * FROM collection_sessions WHERE id=? AND lease=?').bind(id,token).first<any>();if(!s)throw Error('LEASE_LOST');
 let phase='validate';const metrics:{jsonDecoded?:boolean;responseBytes?:number;sourceRows?:number;targetRows?:number}={};
 try{
 const c=config();if(s.day<c.from||s.day>c.to)throw Error('DATE_OUTSIDE_CURRENT_SCOPE');
 let raw:any,bytes:number,hash:string;
 if(s.mode==='synthetic'){raw=mixedFixture(s.day,s.page,s.page_size,s.fixture_total);const encoded=new TextEncoder().encode(JSON.stringify(raw));bytes=encoded.length;const digest=await crypto.subtle.digest('SHA-256',encoded);hash=Array.from(new Uint8Array(digest)).map(b=>b.toString(16).padStart(2,'0')).join('');}
 else {
  if(!c.liveReady)throw Error(sourceReadinessBlock(c)||'LIVE_READINESS_REQUIRED');
  phase='quota';const used=await d.prepare(QUOTA_SQL).bind(kstDate(),c.budget).first();if(!used)throw Error('LOCAL_DAILY_BUDGET_REACHED');
  const url=new URL(ENDPOINT);for(const [k,v] of Object.entries({serviceKey:env.G2B_SERVICE_KEY,pageNo:s.page,numOfRows:s.page_size,type:'json',inqryDiv:'1',inqryBgnDate:s.day.replaceAll('-',''),inqryEndDate:s.day.replaceAll('-','')}))url.searchParams.set(k,String(v));
  phase='source';let response;try{response=await fetch(url,{signal:AbortSignal.timeout(25000)});}catch{throw Error('SOURCE_NETWORK_OR_TIMEOUT');}
  ({raw,bytes,hash}=await readBounded(response,FAST_MAX_BYTES,true));
 }
 metrics.jsonDecoded=true;metrics.responseBytes=bytes;phase='count-response';
 await d.prepare('UPDATE collection_sessions SET calls=calls+1 WHERE id=? AND lease=?').bind(id,token).run();
 phase='parse';const page=parsePage(raw,s.page,s.page_size,s.source_total,(x:any)=>JSON.stringify(identity(x))),keys=page.keys;
 metrics.sourceRows=keys.length;phase='normalize';const normalized:any[]=[];for(const row of page.rows){const n=normalizeDelivery(row,s.day,c,{includeSmart:true});if(n.row)normalized.push(n.row);}raw=null;page.rows.length=0;
 metrics.targetRows=normalized.length;phase='overlap-check';await assertNoPageOverlap(d,s,keys);
 phase='prepare-commit';const plan=pageCommitPlan(s,token,keys,normalized,page.total,page.done,bytes,hash,new Date().toISOString(),dayOrder(s.from_day,s.to_day));
 normalized.length=0;keys.length=0;
 phase='commit';const results=await d.batch(plan.map(q=>d.prepare(q.sql).bind(...q.params)));if(!results.at(-1)?.meta.changes)throw Error('LEASE_LOST');
 }catch(e){const {code}=reportCollectionFailure(e,phase,s,metrics);try{await d.prepare(SESSION_ERROR).bind(code,new Date().toISOString(),id,token).run();}catch(recordError){reportCollectionFailure(recordError,'error-record',s,metrics);}throw Error(code);}
 return workspace(id,false);
}
export async function searchRows(params:URLSearchParams){
 const c=config(),plan=queryPlan(params,c),where=[...plan.where],args=[...plan.args];
 const size=[50,100,250].includes(Number(params.get('size')))?Number(params.get('size')):50;
 const afterDate=params.get('afterDate'),afterKey=params.get('afterKey');if(afterDate&&afterKey){where.push('(source_date<? OR (source_date=? AND source_key>?))');args.push(afterDate,afterDate,afterKey.slice(0,4096));}
 const rows=await cachedQuery(database(),plan.mode,['rows-v1',plan.history,where,args,size],async()=>(await database().prepare(`${plan.prefix} SELECT ${plan.fields} FROM current WHERE ${where.join(' AND ')} ORDER BY source_date DESC,source_key ASC LIMIT ?`).bind(...args,size+1).all()).results) as any[];
 const hasMore=rows.length>size,result=rows.slice(0,size),last=result.at(-1);return {mode:plan.mode,view:plan.history?'history':'current',rows:result,next:hasMore?{afterDate:last.source_date,afterKey:last.source_key}:null,window:c,size};
}
export async function analysisRows(params:URLSearchParams){
 const c=scopeWindow(),group=params.get('group')==='agency'?'agency':'vendor',p=new URLSearchParams(params),vendorQuery=group==='vendor'?(p.get('q')||''):'';if(group==='vendor')p.delete('q');const plan=queryPlan(p,c),d=database();
 if(plan.history)throw Error('CURRENT_ROWS_REQUIRED');
 const requested=p.get('reviewLimit')??p.get('limit');
 const reviewLimit=group==='vendor'&&requested!==null?Number(requested):DELIVERY_SUMMARY_LIMIT;
 if(!Number.isInteger(reviewLimit)||reviewLimit<10||reviewLimit>1000)throw Error('DELIVERY_SUMMARY_INVALID_REVIEW_LIMIT');
 const scopeBytes=new TextEncoder().encode(JSON.stringify(['analysis-v4-vendor-pages',group,vendorQuery.trim().slice(0,100),plan.mode,plan.args,plan.where,reviewLimit]));
 const scope=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',scopeBytes))).map(x=>x.toString(16).padStart(2,'0')).join('');
 const readEpoch=()=>d.prepare('SELECT CAST(revision AS TEXT) revision FROM dataset_epochs WHERE mode=?').bind(plan.mode).first<{revision:string}>();
 const before=await readEpoch();if(!before||!/^(0|[1-9]\d*)$/.test(before.revision))throw Error('DELIVERY_SUMMARY_SOURCE_NOT_FOUND');
 let offset=0;const cursor=params.get('cursor');
 if(cursor!==null){
   let token:any;try{if(cursor.length>1024)throw Error();token=JSON.parse(cursor);}catch{throw Error('DELIVERY_SUMMARY_INVALID_CURSOR');}
   if(!token||token.v!==1||token.scope!==scope||!Number.isInteger(token.offset)||token.offset<200||token.offset>=reviewLimit||token.offset%200!==0||group!=='vendor')throw Error('DELIVERY_SUMMARY_INVALID_CURSOR');
   if(token.revision!==before.revision)throw Error('DELIVERY_SUMMARY_SOURCE_CHANGED_RETRY');
   offset=token.offset;
 }
 const pageSize=Math.min(DELIVERY_SUMMARY_LIMIT,reviewLimit-offset);
 const result=await cachedQuery(d,plan.mode,['analysis-v4-vendor-pages',scope,before.revision,offset,pageSize],async()=>{
   const countQuery=summaryPreflightQuery(plan,group,vendorQuery),preflight=await d.prepare(countQuery.sql).bind(...countQuery.args).first<any>();
   if(!preflight)throw Error('DELIVERY_SUMMARY_INVALID_AGGREGATE');
   const state=deliverySummaryMoneyState(preflight);let rows:any[]=[];
   if(!state.moneyUnavailable){
     const query=summaryQuery(plan,group,vendorQuery,{preflight,revision:before.revision,pageSize,offset});
     rows=(await d.prepare(query.sql).bind(...query.args).all()).results.map((row:any)=>{const {has_lighting,has_pole,...rest}=row;return {...rest,categories:[...(has_lighting?['LIGHTING']:[]),...(has_pole?['POLE']:[])]};});
   }
   const after=await readEpoch();if(after?.revision!==before.revision)throw Error('DELIVERY_SUMMARY_SOURCE_CHANGED_RETRY');
   const selectedGroups=Math.min(Number(state.groupCount),reviewLimit);
   if(offset>0&&offset>=selectedGroups)throw Error('DELIVERY_SUMMARY_INVALID_CURSOR');
   const nextOffset=offset+rows.length;
   const next=!state.moneyUnavailable&&nextOffset<selectedGroups?JSON.stringify({v:1,scope,revision:before.revision,offset:nextOffset}):null;
   return {rows,...state,sourceRevision:before.revision,truncated:!state.moneyUnavailable&&Number(state.groupCount)>reviewLimit,
     reviewLimit,pageSize,offset,totalGroups:state.groupCount,selectedGroups,next};
 });
 const finalEpoch=await readEpoch();if(finalEpoch?.revision!==before.revision||result.sourceRevision!==before.revision)throw Error('DELIVERY_SUMMARY_SOURCE_CHANGED_RETRY');
 return {mode:plan.mode,group,...result,limit:reviewLimit,scope:'FULL_FILTERED_CURRENT',sourceCompletenessVerified:false,providerCalls:0,basis:'최신 활성 품목금액 합계. 금액 미제공은 합계에서 제외. 요구 전체금액 대체 없음. 금액 누락이 있는 합계·순위는 제공된 금액 기준이며 전체 규모를 뜻하지 않습니다.'};
}
export async function facets(mode:string){const c=config(),m=mode==='synthetic'?'synthetic':'live',d=database();const regions=(await d.prepare("SELECT DISTINCT region FROM delivery_rows WHERE mode=? AND is_active=1 AND source_date>=? AND source_date<=? AND region<>'' ORDER BY region LIMIT 100").bind(m,c.from,c.to).all()).results;return {regions:regionChoices(regions.map((x:any)=>x.region))};}
export function rowsCsv(result:any){const keys=['source_date','category','detail_code','request_no','change_order','item_id','item_name','model','agency','region','vendor','quantity','unit_price','amount','request_total','vendor_bizno','contract_no','is_active','inactive_reason'];const header=['기준일','분류','세부품명번호','납품요구번호','변경차수','물품식별번호','품명','모델','수요기관','지역(원천)','공급업체','수량','단가','품목금액','요구전체금액(합산금지)','사업자번호','계약번호','활성여부','비활성사유'];return '\uFEFF'+[header.map(csvCell).join(','),...result.rows.map((r:any)=>keys.map(k=>csvCell(r[k])).join(','))].join('\r\n');}

/** One durable page per scheduled request. No background promise survives the request. */
export async function automaticTick(dryRun=false){
 const c=config(),d=database();
 const session=await d.prepare("SELECT * FROM collection_sessions WHERE mode='live' AND state NOT IN ('complete','cancelled') ORDER BY created_at DESC LIMIT 1").first<any>()||await d.prepare("SELECT * FROM collection_sessions WHERE mode='live' ORDER BY created_at DESC LIMIT 1").first<any>();
 const decision=automaticDecision(session,c),blocking=sourceReadinessBlock(c)||(!c.automaticRunnerEnabled?'AUTOMATIC_RUNNER_NOT_ENABLED':null);
 if(dryRun||blocking)return {dryRun,performed:false,blocking,decision,config:c};
 if(decision.action==='wait'||decision.action==='stop')return {performed:false,blocking:decision.reason,decision,session};
 let id=decision.id;
 if(decision.action==='create'){
  try{const created=await createSession('live',decision.from!,decision.to!);id=(created.session as any)?.id;if(!id)throw Error('SESSION_CREATE_FAILED');}
  catch(e){if((e as Error).message==='LIVE_SESSION_ALREADY_EXISTS')return {performed:false,blocking:'SESSION_BUSY'};throw e;}
 }
 try{return {performed:true,...await sessionStep(id!)};}
 catch(e){if((e as Error).message==='BUSY_PAUSED_COMPLETE_OR_REVIEW_REQUIRED')return {performed:false,blocking:'SESSION_BUSY'};throw e;}
}

export async function collectionRecovery(id:string){return recoveryAssessment(await database().prepare(RECOVERY_SNAPSHOT_SQL).bind(id).first());}
