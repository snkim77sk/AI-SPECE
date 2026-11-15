import {parseGenerationJson} from './g2b-generation-json.mjs';
import {LIMITS,VERSION,validateGenerationPage,sha256} from './g2b-generation-domain.mjs';
import {RECOVERY_SNAPSHOT_SQL,recoveryAssessment} from './g2b-diagnostics.mjs';
import {rowChunks} from './g2b-sql.mjs';
import {kstDate,ENDPOINT} from './collector.mjs';

const MANIFEST=JSON.stringify({endpoint:ENDPOINT,query:{type:'json',inqryDiv:'1',numOfRows:999},limits:LIMITS,version:VERSION,baseCommit:'d10c85c122c959f5830aa2fc1dda0524d5bd729d',coverage:'FAILED_QUERY_DATE_ONLY',sourceSnapshotGuaranteed:false});
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const ROOT=['id','mode','from_day','to_day','state','cursor','day','page','page_size','source_total','fixture_total','fetched','matched','excluded','completed_days','calls','max_payload','lease','lease_until','error','created_at','updated_at'];
const META=['invalid_checkpoints','completed_checkpoints','seen_count','day_seen_count','receipt_count','receipt_fetched','receipt_matched','current_page_receipts','day_receipt_count','day_last_receipt','day_first_receipt','day_receipt_fetched','day_receipt_matched','checkpoint_next_page','checkpoint_fetched','checkpoint_matched','checkpoint_state'];
const AUDIT=['id','session_id','day','page','failed_at','attempt_owner','status','quota_day','created_at','completed_at','result_json'];
const object=(alias,fields)=>`json_object(${fields.flatMap(k=>["'"+k+"'",alias+'.'+k]).join(',')})`;
// One SQLite statement: a consistent metadata snapshot, NOT a content digest of all old rows.
const snapshot=(root,diagnostic)=>`SELECT json_object('root',${object('s',ROOT)},'metadata',${object('m',META)},'diagnostic',${object('v',[...AUDIT,'prior_probe_id'])},'prior',${object('p',AUDIT)}) FROM (${RECOVERY_SNAPSHOT_SQL.replace('s.id=?','s.id='+root)}) m JOIN collection_sessions s ON s.id=m.id JOIN collection_source_diagnostics_v2 v ON v.id=${diagnostic} AND v.session_id=s.id JOIN collection_source_probes p ON p.id=v.prior_probe_id`;
const invariant=`g.version='${VERSION}' AND g.manifest_json='${MANIFEST.replaceAll("'","''")}' AND g.snapshot_json=(${snapshot('g.root_id','g.diagnostic_id')})`;
const statement=(sql,...args)=>({sql,args});
const assertSql=(condition,...args)=>statement(`SELECT CASE WHEN ${condition} THEN 1 ELSE json('GENERATION_FENCE_FAILED') END`,...args);
const failureCodes=new Set(['SOURCE_API_ERROR','SOURCE_TOTAL_INVALID','SOURCE_PAGE_MISMATCH','SOURCE_TOTAL_CHANGED','SOURCE_ROW_COUNT_MISMATCH','SOURCE_DUPLICATE_ID','SOURCE_CAPACITY_EXCEEDED','SOURCE_PAGE_LIMIT','SOURCE_PREVIOUS_TOTAL_REQUIRED','SOURCE_ID_INVALID','SOURCE_ID_TYPE_INVALID','SOURCE_ID_TOO_LONG','SOURCE_CANONICAL_LIMIT','SOURCE_CANONICAL_UNSUPPORTED','SOURCE_CANONICAL_CYCLE','SOURCE_CANONICAL_PROTOTYPE','SOURCE_CANONICAL_SPARSE','SOURCE_CLASSIFICATION_INVALID','SOURCE_RESPONSE_TOO_LARGE','RECORD_TOO_LARGE_FOR_D1','SOURCE_ID_MISSING','SOURCE_DATE_INVALID','SOURCE_NUMBER_OUT_OF_RANGE','SOURCE_PAGE_OVERLAP','GENERATION_CAPACITY','GENERATION_PAYLOAD_LIMIT','GENERATION_ORDER_OR_CONTENT_CHANGED','GENERATION_ROOT_CHANGED','GENERATION_PAUSED_OR_CANCELLED','GENERATION_QUOTA_DAY_CHANGED','GENERATION_QUOTA_REACHED','GENERATION_DEADLINE','GENERATION_SOURCE_FAILED','GENERATION_SOURCE_REDIRECT','GENERATION_RESPONSE_TOO_LARGE','GENERATION_SOURCE_INVALID','GENERATION_SOURCE_DUPLICATE_KEY','GENERATION_IDENTITY_INVALID','GENERATION_CANONICAL_INVALID','GENERATION_STATEMENT_LIMIT','GENERATION_BINDING_LIMIT']);
function safeCode(e){let m;try{m=e?.message;}catch{}return failureCodes.has(m)?m:'GENERATION_PROCESSING_OR_STORAGE_FAILED';}
function safeConflict(error){
 try{const c=error?.conflict;if(!c||!Number.isInteger(c.count)||c.count<1||c.count>999||!Array.isArray(c.samples)||c.samples.length>8)return null;
 const samples=c.samples.map(v=>{const {firstPage,currentPage,identityHash,firstPayloadHash,currentPayloadHash,samePayload}=v;
 if(!Number.isInteger(firstPage)||!Number.isInteger(currentPage)||firstPage<1||firstPage>21||currentPage<1||currentPage>21||typeof samePayload!=='boolean'||[identityHash,firstPayloadHash,currentPayloadHash].some(h=>typeof h!=='string'||!/^[a-f0-9]{64}$/.test(h)))throw Error();
 return {firstPage,currentPage,identityHash,firstPayloadHash,currentPayloadHash,samePayload};});return JSON.stringify({count:c.count,samples});}catch{return null;}
}
// No raw payload/error logging. Cancel both a stalled body and a late fetch result.
export async function generationFetch(fetcher,url,milliseconds){
 const controller=new AbortController();let timer,reader,response,finished=false;
 const cancel=()=>{try{void reader?.cancel().catch(()=>{});}catch{}};
 const timeout=new Promise((_,reject)=>{timer=setTimeout(()=>{reject(Error('GENERATION_SOURCE_FAILED'));controller.abort();cancel();},milliseconds);});
 const bound=p=>Promise.race([p,timeout]);
 try{
  response=await bound(fetcher(url,{signal:controller.signal,redirect:'manual'}).then(r=>{if(finished)try{void r.body?.cancel().catch(()=>{});}catch{}return r;}));
  if(response.status>=300&&response.status<400)throw Error('GENERATION_SOURCE_REDIRECT');
  if(!response.ok)throw Error('GENERATION_SOURCE_FAILED');
  if(Number(response.headers.get('content-length')||0)>LIMITS.maxResponseBytes)throw Error('SOURCE_RESPONSE_TOO_LARGE');
  reader=response.body?.getReader();if(!reader)throw Error('GENERATION_SOURCE_INVALID');let bytes=0;const chunks=[];
  while(true){const r=await bound(reader.read());if(r.done)break;bytes+=r.value.length;if(bytes>LIMITS.maxResponseBytes)throw Error('SOURCE_RESPONSE_TOO_LARGE');chunks.push(r.value);}
  const all=new Uint8Array(bytes);let offset=0;for(const c of chunks){all.set(c,offset);offset+=c.length;}
  const text=new TextDecoder('utf-8',{fatal:true}).decode(all);
  const raw=parseGenerationJson(text);
  const hash=Array.from(new Uint8Array(await bound(crypto.subtle.digest('SHA-256',all)))).map(b=>b.toString(16).padStart(2,'0')).join('');return {raw,bytes,hash};
 }finally{finished=true;clearTimeout(timer);controller.abort();cancel();if(!reader)try{void response?.body?.cancel().catch(()=>{});}catch{}}
}
const flags={sourceCompletenessVerified:false,pointInTimeSnapshotVerified:false,canPromote:false,legacyResumed:false};

/** All network and storage dependencies are injected for offline tests. No retry paths. */
export function generationService({db,configuration,fetcher=globalThis.fetch,now=()=>Date.now(),uuid=()=>crypto.randomUUID()}){
 const one=(sql,...args)=>db.prepare(sql).bind(...args).first();
 const all=async(sql,...args)=>(await db.prepare(sql).bind(...args).all()).results;
 const batch=qs=>db.batch(qs.map(q=>db.prepare(q.sql).bind(...q.args)));
 const gate=(ids)=>{const c=configuration();if(c.enabled!=='approved-staging-only'||c.generationId!==ids.generationId||c.rootId!==ids.rootId||c.diagnosticId!==ids.diagnosticId||!c.liveReady||!Number.isInteger(c.budget)||c.budget<1||c.budget>950)throw Error('GENERATION_NOT_ENABLED');for(const id of Object.values(ids))if(!UUID.test(id))throw Error('GENERATION_INVALID_ID');return c;};
 const runFence=(g,r,owner)=>assertSql(`EXISTS(SELECT 1 FROM g2b_recovery_generations g JOIN g2b_recovery_runs r ON r.id=g.current_run WHERE g.id=? AND g.state='running' AND g.control_version=? AND ${invariant} AND r.id=? AND r.owner=? AND r.state='active' AND r.deadline>? AND r.deadline>unixepoch()*1000+999 AND r.in_flight=0 AND r.attempts<8 AND g.attempts<42)`,g.id,g.control_version,r.id,owner,now());
 const describe=g=>g?{generationId:g.id,rootId:g.root_id,diagnosticId:g.diagnostic_id,day:g.day,state:g.state,pass:g.pass,page:g.page,sourceTotal:g.source_total,sourceRows:g.source_rows,targetRows:g.target_rows,excludedRows:g.excluded_rows,payloadBytesEstimate:g.payload_bytes,attempts:g.attempts,error:g.error,currentRunId:g.current_run,version:g.version,identityContractVerified:false,snapshotScope:'CONSISTENT_LEGACY_METADATA_ONLY',...flags}:null;
 async function read(id,runId){if(!UUID.test(id)||runId&&!UUID.test(runId))throw Error('GENERATION_INVALID_ID');const g=await one('SELECT * FROM g2b_recovery_generations WHERE id=?',id);const r=runId?await one('SELECT id,state,attempts,deadline,error FROM g2b_recovery_runs WHERE id=? AND generation_id=?',runId,id):null;const receipts=await all('SELECT pass,page,total,source_rows,target_rows,excluded_rows,ordered_hash,response_hash FROM g2b_recovery_pages WHERE generation_id=? ORDER BY pass,page LIMIT 42',id);const failures=await all('SELECT pass,page,state,error,conflict_json FROM g2b_recovery_attempts WHERE generation_id=? AND state<>\'committed\' ORDER BY pass,page LIMIT 42',id);return {generation:describe(g),run:r,receipts,failures,...flags};}
 async function create(ids){gate(ids);const existing=await one('SELECT * FROM g2b_recovery_generations WHERE id=? OR root_id=?',ids.generationId,ids.rootId);if(existing){if(existing.id!==ids.generationId||existing.root_id!==ids.rootId||existing.diagnostic_id!==ids.diagnosticId)throw Error('GENERATION_ALREADY_EXISTS');return read(existing.id);}
  const row=await one(`SELECT (${snapshot('?','?')}) AS evidence`,ids.rootId,ids.diagnosticId);let evidence;try{evidence=JSON.parse(row?.evidence||'null');}catch{}if(!evidence)throw Error('GENERATION_ROOT_NOT_ELIGIBLE');const {root:s,metadata,diagnostic:v,prior:p}=evidence;
  const check=recoveryAssessment({...s,...metadata});let result,prior;try{result=JSON.parse(v.result_json);prior=JSON.parse(p.result_json);}catch{}
  if(s.mode!=='live'||s.state!=='error'||s.error!=='STORAGE_OR_SOURCE_FAILED'||s.lease!==null||s.lease_until!==0||s.page_size!==999||!check.metadataConsistent||check.pageCommitState!=='NOT_COMMITTED'||v.status!=='complete'||!v.completed_at||v.day!==s.day||v.page!==s.page||v.failed_at!==s.updated_at||result?.code!=='SOURCE_PAGE_OVERLAP'||result.phase!=='overlap-check'||result.collectionSnapshotUnchanged!==true||result.sourceRowsWritten!==0||result.collectionResumed!==false||result.automaticRetryAllowed!==false||p.status!=='complete'||!p.completed_at||p.session_id!==s.id||p.day!==s.day||p.page!==s.page||p.failed_at!==s.updated_at||prior?.code!=='SOURCE_NETWORK_OR_TIMEOUT'||prior.phase!=='source'||prior.collectionSnapshotUnchanged!==true||prior.sourceRowsWritten!==0||prior.collectionResumed!==false||prior.automaticRetryAllowed!==false)throw Error('GENERATION_ROOT_NOT_ELIGIBLE');
  const c=gate(ids);if(s.day<c.from||s.day>c.to)throw Error('GENERATION_ROOT_NOT_ELIGIBLE');
  await batch([assertSql(`(${snapshot('?','?')})=?`,ids.rootId,ids.diagnosticId,row.evidence),statement("INSERT INTO g2b_recovery_generations(id,root_id,diagnostic_id,snapshot_json,manifest_json,day,version,window_from,window_to,state,created_at) VALUES(?,?,?,?,?,?,?,?,?,'ready',?)",ids.generationId,ids.rootId,ids.diagnosticId,row.evidence,MANIFEST,s.day,VERSION,c.from,c.to,now())]);return read(ids.generationId);
 }
 async function control(ids,state){gate(ids);if(!['paused','cancelled'].includes(state))throw Error('GENERATION_INVALID_CONTROL');await db.prepare("UPDATE g2b_recovery_generations SET state=?,control_version=control_version+1 WHERE id=? AND state IN ('ready','running','paused')").bind(state,ids.generationId).run();return read(ids.generationId);}
 // A synchronous boundary cannot cancel already submitted database work. A timed-out
 // write leaves a durable active/uncertain run and is never replayed automatically.
 async function run(ids,runId,requestStarted=now()){const started=Math.min(now(),requestStarted),deadline=started+90000,owner=uuid();let timer;gate(ids);if(!UUID.test(runId))throw Error('GENERATION_INVALID_ID');
  const bounded=async(p)=>{try{return await Promise.race([p,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('GENERATION_DEADLINE')),Math.max(1,deadline-now()));})]);}finally{clearTimeout(timer);}};
  const prior=await bounded(one('SELECT id FROM g2b_recovery_runs WHERE id=?',runId));if(prior)return bounded(read(ids.generationId,runId));
  let g=await bounded(one('SELECT * FROM g2b_recovery_generations WHERE id=?',ids.generationId));if(!g||g.root_id!==ids.rootId||g.diagnostic_id!==ids.diagnosticId)throw Error('GENERATION_NOT_FOUND');
  const scopeGate=()=>{const c=gate(ids);if(c.from!==g.window_from||c.to!==g.window_to)throw Error('GENERATION_ROOT_CHANGED');return c;};scopeGate();
  try{await bounded(batch([assertSql(`EXISTS(SELECT 1 FROM g2b_recovery_generations g WHERE g.id=? AND g.state='ready' AND (SELECT COUNT(*) FROM g2b_recovery_runs WHERE generation_id=g.id)<42 AND ${invariant} AND (g.current_run IS NULL OR EXISTS(SELECT 1 FROM g2b_recovery_runs r WHERE r.id=g.current_run AND r.state='finished')))`,g.id),statement("INSERT INTO g2b_recovery_runs(id,generation_id,owner,state,started_at,deadline) VALUES(?,?,?,'active',?,?)",runId,g.id,owner,started,deadline),statement("UPDATE g2b_recovery_generations SET state='running',current_run=? WHERE id=?",runId,g.id)]));}
  catch{ // A known or unknown start failure must never lead to a source dispatch.
   try{await bounded(db.prepare("UPDATE g2b_recovery_generations SET state='uncertain',error='GENERATION_RUN_START_UNCERTAIN' WHERE id=? AND state='ready'").bind(g.id).run());}catch{}return {error:'GENERATION_RUN_START_UNCERTAIN',readSameRunId:runId,...flags};
  }
  let activeAttempt=null,committing=false;
  try{
   for(let count=0;count<8;count++){
    // No new request with less than 30 seconds: 25s source/body plus 5s commit reserve.
    if(deadline-now()<30000)break;
    gate(ids);g=await bounded(one('SELECT * FROM g2b_recovery_generations WHERE id=?',g.id));
    if(g.state!=='running')throw Error('GENERATION_PAUSED_OR_CANCELLED');
    const r=await bounded(one('SELECT * FROM g2b_recovery_runs WHERE id=?',runId));
    const c=scopeGate(),quotaDay=kstDate(new Date(now()));activeAttempt={id:uuid(),pass:g.pass,page:g.page};
    await bounded(batch([runFence(g,r,owner),assertSql('COALESCE((SELECT calls FROM usage WHERE day=?),0)<?',quotaDay,c.budget),statement("INSERT INTO g2b_recovery_attempts(id,generation_id,run_id,pass,page,state,quota_day,created_at) VALUES(?,?,?,?,?,'reserved',?,?)",activeAttempt.id,g.id,runId,g.pass,g.page,quotaDay,now()),statement('INSERT INTO usage(day,calls) VALUES(?,1) ON CONFLICT(day) DO UPDATE SET calls=calls+1',quotaDay),statement('UPDATE g2b_recovery_runs SET attempts=attempts+1,in_flight=1 WHERE id=?',runId),statement('UPDATE g2b_recovery_generations SET attempts=attempts+1 WHERE id=?',g.id)]));
    gate(ids);if(kstDate(new Date(now()))!==quotaDay)throw Error('GENERATION_QUOTA_DAY_CHANGED');
    const still=await bounded(one(`SELECT g.id FROM g2b_recovery_generations g WHERE g.id=? AND g.state='running' AND g.control_version=? AND ${invariant}`,g.id,g.control_version));if(!still)throw Error('GENERATION_ROOT_CHANGED');
    scopeGate();if(kstDate(new Date(now()))!==quotaDay)throw Error('GENERATION_QUOTA_DAY_CHANGED');
    if(deadline-now()<5000)throw Error('GENERATION_DEADLINE');
    const url=new URL(ENDPOINT);for(const [k,v] of Object.entries({serviceKey:c.serviceKey,pageNo:g.page,numOfRows:999,type:'json',inqryDiv:'1',inqryBgnDate:g.day.replaceAll('-',''),inqryEndDate:g.day.replaceAll('-','')}))url.searchParams.set(k,String(v));
    const parsed=await generationFetch(fetcher,url,Math.max(1,Math.min(25000,deadline-now()-5000)));
    if(now()>=deadline)throw Error('GENERATION_DEADLINE');
    const page=await bounded(validateGenerationPage(parsed.raw,{page:g.page,total:g.source_total,day:g.day,window:{from:g.window_from,to:g.window_to}}));
    if(now()>=deadline)throw Error('GENERATION_DEADLINE');
    if(g.pass===1){
     let conflicts=[];for(const chunk of rowChunks(page.rows.map(x=>x.source_key))){const found=await bounded(all('SELECT source_key,page,payload_hash FROM g2b_recovery_source_rows WHERE generation_id=? AND source_key IN(SELECT value FROM json_each(?)) LIMIT 999',g.id,chunk));conflicts.push(...found);}
     if(conflicts.length){const samples=[];for(const old of conflicts.slice(0,8)){const current=page.rows.find(x=>x.source_key===old.source_key);samples.push({firstPage:old.page,currentPage:g.page,identityHash:await sha256(old.source_key),firstPayloadHash:old.payload_hash,currentPayloadHash:current.payload_hash,samePayload:old.payload_hash===current.payload_hash});}const e=Error('SOURCE_PAGE_OVERLAP');e.conflict={count:conflicts.length,samples};throw e;}
    }else{
     const first=await bounded(one('SELECT * FROM g2b_recovery_pages WHERE generation_id=? AND pass=1 AND page=?',g.id,g.page));
     if(!first||first.total!==page.total||first.source_rows!==page.rows.length||first.ordered_hash!==page.ordered_hash)throw Error('GENERATION_ORDER_OR_CONTENT_CHANGED');
     // Compare actual stored ordered identities and hashes too, not only a page aggregate.
     const old=await bounded(all('SELECT source_key,payload_hash,ordinal FROM g2b_recovery_source_rows WHERE generation_id=? AND page=? ORDER BY ordinal',g.id,g.page));
     if(old.length!==page.rows.length||old.some((v,i)=>v.source_key!==page.rows[i].source_key||v.payload_hash!==page.rows[i].payload_hash||v.ordinal!==page.rows[i].ordinal))throw Error('GENERATION_ORDER_OR_CONTENT_CHANGED');
    }
    if(g.payload_bytes+(g.pass===1?page.payloadBytes:0)>LIMITS.maxPayloadBytes)throw Error('GENERATION_PAYLOAD_LIMIT');
    scopeGate();
    const qs=[assertSql(`EXISTS(SELECT 1 FROM g2b_recovery_generations g JOIN g2b_recovery_runs r ON r.id=g.current_run JOIN g2b_recovery_attempts a ON a.run_id=r.id WHERE g.id=? AND g.state='running' AND g.control_version=? AND g.pass=? AND g.page=? AND ${invariant} AND r.id=? AND r.owner=? AND r.state='active' AND r.deadline>? AND r.deadline>unixepoch()*1000+999 AND r.in_flight=1 AND a.id=? AND a.state='reserved')`,g.id,g.control_version,g.pass,g.page,runId,owner,now(),activeAttempt.id)];
    if(g.pass===1){
     for(const chunk of rowChunks(page.rows.map(x=>({source_key:x.source_key,ordinal:x.ordinal,payload_hash:x.payload_hash,excluded:x.excluded,source_date:x.source_date,date_basis:x.date_basis})),LIMITS.maxBindingBytes))qs.push(statement("INSERT INTO g2b_recovery_source_rows(generation_id,source_key,page,ordinal,payload_hash,excluded,source_date,date_basis) SELECT ?,json_extract(value,'$.source_key'),?,json_extract(value,'$.ordinal'),json_extract(value,'$.payload_hash'),json_extract(value,'$.excluded'),json_extract(value,'$.source_date'),json_extract(value,'$.date_basis') FROM json_each(?)",g.id,g.page,chunk));
     for(const chunk of rowChunks(page.rows.filter(x=>x.target_json!==null).map(x=>({source_key:x.source_key,target_json:x.target_json})),LIMITS.maxBindingBytes))qs.push(statement("INSERT INTO g2b_recovery_target_rows(generation_id,source_key,target_json) SELECT ?,json_extract(value,'$.source_key'),json_extract(value,'$.target_json') FROM json_each(?)",g.id,chunk));
    }
    qs.push(statement('INSERT INTO g2b_recovery_pages(generation_id,pass,page,total,source_rows,target_rows,excluded_rows,ordered_hash,response_hash,response_bytes,committed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',g.id,g.pass,g.page,page.total,page.rows.length,page.matched,page.excluded,page.ordered_hash,parsed.hash,parsed.bytes,now()));
    if(page.done){qs.push(assertSql('(SELECT COUNT(*) FROM g2b_recovery_pages WHERE generation_id=? AND pass=?)=? AND (SELECT COALESCE(SUM(source_rows),0) FROM g2b_recovery_pages WHERE generation_id=? AND pass=?)=? AND (SELECT COUNT(*) FROM g2b_recovery_source_rows WHERE generation_id=?)=?',g.id,g.pass,g.page,g.id,g.pass,page.total,g.id,page.total));}
    qs.push(statement("UPDATE g2b_recovery_attempts SET state='committed',response_hash=?,ordered_hash=? WHERE id=?",parsed.hash,page.ordered_hash,activeAttempt.id));
    qs.push(statement('UPDATE g2b_recovery_runs SET in_flight=0 WHERE id=?',runId));
    qs.push(statement('UPDATE g2b_recovery_generations SET source_total=?,source_rows=source_rows+?,target_rows=target_rows+?,excluded_rows=excluded_rows+?,payload_bytes=payload_bytes+?,pass=?,page=?,state=? WHERE id=?',page.total,g.pass===1?page.rows.length:0,g.pass===1?page.matched:0,g.pass===1?page.excluded:0,g.pass===1?page.payloadBytes:0,page.done&&g.pass===1?2:g.pass,page.done?1:g.page+1,page.done&&g.pass===2?'observed_consistent_needs_review':'running',g.id));
    if(qs.length>LIMITS.maxStatements)throw Error('GENERATION_STATEMENT_LIMIT');
    for(const q of qs)for(const a of q.args)if(typeof a==='string'&&new TextEncoder().encode(a).length>LIMITS.maxBindingBytes)throw Error('GENERATION_BINDING_LIMIT');
    committing=true;await bounded(batch(qs));committing=false;activeAttempt=null;
    if(page.done&&g.pass===2)break;
   }
   await bounded(batch([assertSql("EXISTS(SELECT 1 FROM g2b_recovery_runs r JOIN g2b_recovery_generations g ON g.current_run=r.id WHERE r.id=? AND r.owner=? AND r.state='active' AND r.in_flight=0 AND r.attempts>0 AND g.state IN ('running','observed_consistent_needs_review') AND g.control_version=? AND r.deadline>? AND r.deadline>unixepoch()*1000+999)",runId,owner,g.control_version,now()),statement("UPDATE g2b_recovery_runs SET state='finished' WHERE id=?",runId),statement("UPDATE g2b_recovery_generations SET state=CASE WHEN state='running' THEN 'ready' ELSE state END WHERE id=?",g.id)]));
  }catch(e){
   // Even a definite reservation rollback stops this durable run. Never start a new run to hide it.
   const code=safeCode(e);const conflict=safeConflict(e);
   try{await bounded(batch([statement("UPDATE g2b_recovery_runs SET state='uncertain',error=? WHERE id=? AND state='active'",committing?'GENERATION_COMMIT_ACK_UNCERTAIN':code,runId),statement("UPDATE g2b_recovery_generations SET state=CASE WHEN state IN ('paused','cancelled') THEN state ELSE 'uncertain' END,error=COALESCE(error,?) WHERE id=?",code,g.id),...(activeAttempt?[statement("UPDATE g2b_recovery_attempts SET state='failed',error=?,conflict_json=? WHERE id=? AND state='reserved'",code,conflict,activeAttempt.id)]:[])]));}catch{}
   // Bounded same-ID readback only, never retry or replace a source request.
   try{return {...await bounded(read(g.id,runId)),outcome:'STOPPED_READBACK_ONLY'};}catch{return {error:'GENERATION_OUTCOME_UNCERTAIN',readSameRunId:runId,...flags};}
  }
  return bounded(read(g.id,runId));
 }
 return {create,run,read,control};
}
