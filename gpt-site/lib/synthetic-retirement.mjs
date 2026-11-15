/** Owner-private, bounded backup/delete workflow. No migrations or provider calls. */
import {digest,stableJson} from './budget-domain.mjs';
import {RETIREMENT_COLUMNS} from './retirement-columns.mjs';
export const RETIREMENT_VERSION='5.2.1';
export const MAX_RETIREMENT_ROWS=500,MAX_RETIREMENT_BYTES=1048576;
const own="mode='synthetic'";
const child=(key,parent)=>`${key} IN (SELECT id FROM ${parent} WHERE ${own})`;
const generation="SELECT g.id FROM g2b_recovery_generations g JOIN collection_sessions s ON s.id=g.root_id WHERE s.mode='synthetic'";
// Child-first order preserves all foreign keys. No ON DELETE CASCADE is used.
export const RETIREMENT_TABLES=Object.freeze([
 ['future_rank_rows',child('run_id','future_rank_runs')],['future_rank_runs',own],
 ...['institution_pattern_aliases','institution_pattern_years','institution_pattern_events'].map(t=>[t,child('run_id','institution_pattern_runs')]),
 ['institution_pattern_runs',own],
 ...['matching_aliases','matching_budgets','matching_requests','matching_results'].map(t=>[t,child('job_id','matching_jobs')]),
 ['matching_jobs',own],['budget_observations',own],['budget_revisions',own],['budget_rows',own],
 ...['budget_receipts','budget_seen','budget_replay_state'].map(t=>[t,child('session_id','budget_sessions')]),
 ['budget_sessions',own],['budget_controls',own],
 ...['g2b_recovery_attempts','g2b_recovery_pages','g2b_recovery_source_rows','g2b_recovery_target_rows','g2b_recovery_runs'].map(t=>[t,`generation_id IN (${generation})`]),
 ['g2b_recovery_generations',child('root_id','collection_sessions')],
 ...['collection_source_diagnostics_v2','collection_source_probes','collection_receipts','day_checkpoints','seen_source_keys'].map(t=>[t,child('session_id','collection_sessions')]),
 ['collection_sessions',own],['delivery_rows',own],
 ...['receipts','records'].map(t=>[t,child('job_id','jobs')]),['jobs',own],
 // DELETE triggers can advance epochs; remove those after all dependent rows.
 ...['query_cache','dataset_epochs','budget_epochs','budget_history_epochs','budget_observation_epochs'].map(t=>[t,own]),
].map(([table,where])=>Object.freeze({table,where})));
const spec=table=>{const s=RETIREMENT_TABLES.find(s=>s.table===table);if(!s)throw Error('RETIREMENT_INVALID_TABLE');return s;};
const leased=['jobs','collection_sessions','budget_sessions','matching_jobs','institution_pattern_runs','future_rank_runs'];
async function checkedRead(phase,read){
  try{return await read();}catch(error){error.retirementPhase=phase;throw error;}
}
export async function assertRetirementIdle(db,now=Date.now()){
  const sql=leased.map(t=>`SELECT COUNT(*) n FROM ${t} WHERE ${own} AND lease_until>?`).concat([
    `SELECT COUNT(*) n FROM g2b_recovery_runs WHERE generation_id IN (${generation}) AND state='active' AND deadline>?`,
  ]);
  // Keep each SELECT separate: the hosted database rejects the compound form.
  const result=await checkedRead('LEASE_CHECK',()=>db.batch(sql.map(q=>db.prepare(q).bind(now))));
  if(result.some(r=>Number(r.results?.[0]?.n)>0))throw Error('RETIREMENT_ACTIVE_LEASE');
}
export async function retirementStatus(db){
  const rows=[];for(const s of RETIREMENT_TABLES){const r=await db.prepare(`SELECT COUNT(*) n FROM ${s.table} WHERE ${s.where}`).first();rows.push({table:s.table,count:r.n});}
  return {version:RETIREMENT_VERSION,tables:rows,total:rows.reduce((n,r)=>n+r.count,0),providerCalls:0};
}
async function columns(db,table){
  const names=RETIREMENT_COLUMNS[table];
  if(!names?.length)throw Error('RETIREMENT_SCHEMA_INVALID');
  return names;
}
export async function retirementBatch(db,table,after=0){
  const s=spec(table);if(!Number.isSafeInteger(after)||after<0)throw Error('RETIREMENT_INVALID_CURSOR');
  await assertRetirementIdle(db);
  const cols=await columns(db,table),size='64+'+cols.map(c=>`length(CAST(json_quote("${c}") AS BLOB))+${c.length+4}`).join('+');
  const sizes=(await checkedRead('PAGE_SIZES',()=>db.prepare(`SELECT rowid id,${size} bytes FROM ${table} WHERE ${s.where} AND rowid>? ORDER BY rowid LIMIT ${MAX_RETIREMENT_ROWS}`).bind(after).all())).results;
  let bytes=0;const ids=[];for(const r of sizes){
    // Count actual escaped JSON bytes in SQLite, with reserved envelope space.
    if(bytes+r.bytes>MAX_RETIREMENT_BYTES-1024){if(!ids.length)throw Error('RETIREMENT_ROW_TOO_LARGE');break;}
    ids.push(r.id);bytes+=r.bytes;
  }
  const rows=ids.length?(await checkedRead('PAGE_ROWS',()=>db.prepare(`SELECT rowid _rowid,* FROM ${table} WHERE ${s.where} AND rowid IN (SELECT value FROM json_each(?)) ORDER BY rowid`).bind(JSON.stringify(ids)).all())).results:[];
  const payload={version:RETIREMENT_VERSION,table,rows},hash=await digest(stableJson(payload));
  return {...payload,hash,next:ids.at(-1)??after,providerCalls:0};
}
export async function retireBackedUpBatch(db,input){
  if(!input||input.version!==RETIREMENT_VERSION||!Array.isArray(input.ids)||input.ids.length<1||input.ids.length>MAX_RETIREMENT_ROWS||input.ids.some(x=>!Number.isSafeInteger(x)||x<1)||new Set(input.ids).size!==input.ids.length||!/^\w{64}$/.test(input.hash||''))throw Error('RETIREMENT_INVALID_BATCH');
  const s=spec(input.table),table=s.table;await assertRetirementIdle(db);
  const ids=JSON.stringify(input.ids),rows=(await db.prepare(`SELECT rowid _rowid,* FROM ${table} WHERE ${s.where} AND rowid IN (SELECT value FROM json_each(?)) ORDER BY rowid`).bind(ids).all()).results;
  if(!rows.length)return {ok:true,deleted:0,alreadyAbsent:true,providerCalls:0};
  if(rows.length!==input.ids.length||await digest(stableJson({version:RETIREMENT_VERSION,table,rows}))!==input.hash)throw Error('RETIREMENT_BACKUP_MISMATCH');
  const cols=await columns(db,table),same=cols.map(c=>`t."${c}" IS json_extract(b.value,'$.${c}')`).join(' AND ');
  // Compare every backed-up field inside the DELETE statement as well as above.
  // A changed row aborts the whole page, including any concurrent writer.
  const result=await db.prepare(`DELETE FROM ${table} WHERE ${s.where} AND rowid IN (SELECT value FROM json_each(?)) AND (SELECT COUNT(*) FROM ${table} t JOIN json_each(?) b ON t.rowid=json_extract(b.value,'$._rowid') WHERE ${same})=? RETURNING rowid AS _rowid`).bind(ids,JSON.stringify(rows),rows.length).all();
  // D1 meta.changes includes trigger writes. Count the directly deleted rows.
  if(result.results.length!==rows.length)throw Error('RETIREMENT_SOURCE_CHANGED');
  return {ok:true,deleted:result.results.length,hash:input.hash,providerCalls:0};
}
