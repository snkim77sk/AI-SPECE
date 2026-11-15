/** Verified Windows pages feed the existing query models. No provider calls. */
import {identity,normalizeDelivery,scopeWindow} from './g2b-domain.mjs';
import {digest,normalizeBudget,stableJson} from './budget-domain.mjs';
import {validDay,kstDate} from './collector.mjs';
import {rowChunks} from './g2b-sql.mjs';
import {budgetObservationPlan} from './budget-observations.mjs';

export const SYNC_VERSION='5.2.11';
export const SYNC_PROTOCOL='windows-verified-pages-v1';
export const SYNC_MAX_BYTES=12*1024*1024;
const datasets=['shopping_delivery','budget','budget_appropriation'];
const fields=['source_key','is_active','inactive_reason','change_rank','vendor_bizno','contract_no','source_date','date_basis','category','detail_code','detail_name','request_no','change_order','sequence','request_name','final_flag','item_id','item_name','model','agency','agency_code','region','vendor','quantity','unit_price','amount','request_total','raw'];
const object=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);
const integer=x=>Number.isSafeInteger(x)&&x>=0;

export async function prepareServerPage(p,today=kstDate()){
 if(!object(p)||p.protocol!==SYNC_PROTOCOL||!datasets.includes(p.dataset)||!Array.isArray(p.rows)||p.rows.length>999)throw Error('SYNC_INVALID_PAGE');
 if(typeof p.scope!=='string'||p.scope.length>128||typeof p.generation!=='string'||!/^[a-zA-Z0-9_-]{1,128}$/.test(p.generation)||!integer(p.page_no)||p.page_no<1)throw Error('SYNC_INVALID_IDENTITY');
 if(!/^[a-f0-9]{64}$/.test(p.revision)||!validDay(p.snapshot_date)||p.snapshot_date<'2026-01-01'||p.snapshot_date>today)throw Error('SYNC_INVALID_SCOPE');
 if(!integer(p.source_saved_count)||!integer(p.source_total)||p.source_saved_count<p.rows.length||!['FAILED','IDLE','RUNNING','COMPLETE','PAUSED','ERROR','HOLD','READY','INCOMPLETE','BLOCKED'].includes(p.source_status))throw Error('SYNC_INVALID_CHECKPOINT');
 if(typeof p.source_updated_at!=='string'||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{3})?Z$/.test(p.source_updated_at)||!Number.isFinite(Date.parse(p.source_updated_at)))throw Error('SYNC_INVALID_TIMESTAMP');
 if(p.rows.some(r=>!object(r)||Object.entries(r).some(([k,v])=>/^(servicekey|key|token|authorization|password|secret|access_token|api_key)$/i.test(k)||!['string','number','boolean'].includes(typeof v)&&v!==null)))throw Error('SYNC_INVALID_SOURCE_FIELDS');
 const id=await digest(stableJson([p.dataset,p.scope,p.generation,p.page_no]));
 const rows=[],observed=[],keys=new Set(),service=p.dataset==='budget'?'QWGJK':'AIDFA';
 const session={id:'server:'+id,mode:'live',service,fiscal_year:Number(p.scope.slice(0,4)),snapshot_date:p.snapshot_date};
 if(!Number.isInteger(session.fiscal_year)||session.fiscal_year<2026||session.fiscal_year>2100)throw Error('SYNC_INVALID_YEAR');
 for(const raw of p.rows){
  let row,key;
  if(p.dataset==='shopping_delivery'){
   const [request_no,change_order,sequence]=identity(raw);key=JSON.stringify([request_no,change_order,sequence]);
   row=normalizeDelivery(raw,p.snapshot_date,scopeWindow(today),{includeSmart:true}).row;
   observed.push({source_key:key,request_no,sequence,change_rank:change_order.replace(/\D/g,'').replace(/^0+/,'')||'0',stored:row?1:0});
  }else{row=await normalizeBudget(raw,session);key=row.source_key;}
  if(keys.has(key))throw Error('SYNC_DUPLICATE_SOURCE_KEY');keys.add(key);if(row)rows.push(row);
 }
 return {p,id,rows,observed,keys:[...keys],session,contentHash:await digest(stableJson(p.rows))};
}

export function serverPagePlan(v,stamp){
 const {p,id,rows,observed,keys,session,contentHash}=v,out=[];
 const guard='NOT EXISTS(SELECT 1 FROM server_sync_pages WHERE id=?)',fence=[id];
 const add=(sql,params)=>out.push({sql,params});
 add("SELECT CASE WHEN EXISTS(SELECT 1 FROM server_sync_pages WHERE id=? AND (revision<>? OR content_hash<>?)) THEN json('SYNC_PAGE_CONFLICT') ELSE 1 END",[id,p.revision,contentHash]);
 for(const chunk of rowChunks(keys.map(source_key=>({source_key})))){
  add(`INSERT INTO server_sync_keys(dataset,scope,generation,source_key,page_id) SELECT ?,?,?,json_extract(value,'$.source_key'),? FROM json_each(?) WHERE ${guard}`,[p.dataset,p.scope,p.generation,id,chunk,...fence]);
 }
 if(p.dataset==='shopping_delivery'){
  for(const chunk of rowChunks(observed)){
   // Keep earlier evidence; only the newest known observation is active.
   add(`UPDATE seen_source_keys SET is_active=0 WHERE mode='live' AND is_active=1 AND source_key IN(SELECT json_extract(value,'$.source_key') FROM json_each(?)) AND NOT EXISTS(SELECT 1 FROM delivery_rows r WHERE r.mode='live' AND r.source_key=seen_source_keys.source_key AND r.updated_at>?) AND ${guard}`,[chunk,p.source_updated_at,...fence]);
   add(`INSERT INTO seen_source_keys(session_id,day,source_key,mode,request_no,sequence,change_rank,stored,is_active) SELECT ?,?,json_extract(value,'$.source_key'),'live',json_extract(value,'$.request_no'),json_extract(value,'$.sequence'),json_extract(value,'$.change_rank'),json_extract(value,'$.stored'),CASE WHEN EXISTS(SELECT 1 FROM delivery_rows r WHERE r.mode='live' AND r.source_key=json_extract(value,'$.source_key') AND r.updated_at>?) THEN 0 ELSE 1 END FROM json_each(?) WHERE ${guard}`,[session.id,p.snapshot_date,p.source_updated_at,chunk,...fence]);
  }
  for(const chunk of rowChunks(rows))add(`INSERT INTO delivery_rows(mode,${fields.join(',')},last_session,updated_at) SELECT 'live',${fields.map(k=>`json_extract(value,'$.${k}')`).join(',')},?,? FROM json_each(?) WHERE ${guard} ON CONFLICT(mode,source_key) DO UPDATE SET ${fields.filter(k=>k!=='source_key').map(k=>`${k}=excluded.${k}`).join(',')},last_session=excluded.last_session,updated_at=excluded.updated_at WHERE excluded.updated_at>=delivery_rows.updated_at`,[session.id,p.source_updated_at,chunk,...fence]);
  add(`INSERT INTO dataset_epochs(mode,revision) SELECT 'live',1 WHERE ${guard} ON CONFLICT(mode) DO UPDATE SET revision=revision+1`,fence);
 }else{
  for(const chunk of rowChunks(rows)){
   add(`INSERT INTO budget_revisions(mode,service,source_key,payload_hash,row_json,first_seen,first_snapshot) SELECT 'live',?,json_extract(value,'$.source_key'),json_extract(value,'$.payload_hash'),value,?,? FROM json_each(?) WHERE ${guard} ON CONFLICT(mode,service,source_key,payload_hash) DO NOTHING`,[session.service,p.source_updated_at,p.snapshot_date,chunk,...fence]);
   add(`INSERT INTO budget_rows(mode,service,source_key,payload_hash,fiscal_year,snapshot_date,category,region,org,dept,project,row_json,is_active,last_session,updated_at) SELECT 'live',?,json_extract(value,'$.source_key'),json_extract(value,'$.payload_hash'),?,?,json_extract(value,'$.category'),json_extract(value,'$.region'),json_extract(value,'$.org'),json_extract(value,'$.dept'),json_extract(value,'$.project'),value,1,?,? FROM json_each(?) WHERE ${guard} ON CONFLICT(mode,service,source_key) DO UPDATE SET payload_hash=excluded.payload_hash,snapshot_date=excluded.snapshot_date,category=excluded.category,region=excluded.region,org=excluded.org,dept=excluded.dept,project=excluded.project,row_json=excluded.row_json,is_active=1,last_session=excluded.last_session,updated_at=excluded.updated_at WHERE (excluded.snapshot_date,excluded.updated_at)>=(budget_rows.snapshot_date,budget_rows.updated_at)`,[session.service,session.fiscal_year,p.snapshot_date,session.id,p.source_updated_at,chunk,...fence]);
  }
  out.push(...budgetObservationPlan(session,rows,p.source_updated_at,guard,fence));
  add(`INSERT INTO budget_epochs(mode,revision) SELECT 'live',1 WHERE ${guard} ON CONFLICT(mode) DO UPDATE SET revision=revision+1`,fence);
 }
 // Last statement is the atomic receipt. A lost HTTP response can be resolved by GET.
 add(`INSERT INTO server_sync_pages(id,dataset,scope,generation,page_no,revision,content_hash,source_rows,matched_rows,source_saved_count,source_total,source_status,source_updated_at,received_at) SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,? WHERE ${guard}`,[id,p.dataset,p.scope,p.generation,p.page_no,p.revision,contentHash,p.rows.length,rows.length,p.source_saved_count,p.source_total,p.source_status,p.source_updated_at,stamp,...fence]);
 return out;
}

export async function readSyncBody(request,maxBytes=SYNC_MAX_BYTES){
 if(!request.body||request.headers.get('content-type')?.split(';')[0]!=='application/json')throw Error('SYNC_JSON_REQUIRED');
 const encoding=request.headers.get('content-encoding');if(encoding&&encoding!=='gzip')throw Error('SYNC_ENCODING_UNSUPPORTED');
 const stream=encoding==='gzip'?request.body.pipeThrough(new DecompressionStream('gzip')):request.body;
 const reader=stream.getReader(),chunks=[];let bytes=0;
 try{while(true){const {value,done}=await reader.read();if(done)break;bytes+=value.byteLength;if(bytes>maxBytes)throw Error('SYNC_BODY_TOO_LARGE');chunks.push(value);}}
 finally{await reader.cancel().catch(()=>{});}
 const buffer=new Uint8Array(bytes);let offset=0;for(const c of chunks){buffer.set(c,offset);offset+=c.length;}
 const text=new TextDecoder('utf-8',{fatal:true}).decode(buffer);return {payload:JSON.parse(text),requestDigest:await digest(text)};
}
