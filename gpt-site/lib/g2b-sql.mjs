export const SESSION_ACQUIRE="UPDATE collection_sessions SET lease=?,lease_until=?,state='running',error=NULL WHERE id=? AND state IN ('ready','running','error') AND lease_until<=?";
export const SESSION_STATE="UPDATE collection_sessions SET state=?,lease=NULL,lease_until=0,error=NULL,updated_at=? WHERE id=? AND state NOT IN ('complete','cancelled') AND lease_until<=?";
export const SESSION_ERROR="UPDATE collection_sessions SET state='error',error=?,lease=NULL,lease_until=0,updated_at=? WHERE id=? AND lease=?";
const FIELDS=['source_key','is_active','inactive_reason','change_rank','vendor_bizno','contract_no','source_date','date_basis','category','detail_code','detail_name','request_no','change_order','sequence','request_name','final_flag','item_id','item_name','model','agency','agency_code','region','vendor','quantity','unit_price','amount','request_total','raw'];

export function rowChunks(rows,maxBytes=1400000){
 const chunks=[];let parts=[],bytes=2;const encoder=new TextEncoder();
 for(const row of rows){const text=JSON.stringify(row),size=encoder.encode(text).byteLength;if(size+2>maxBytes)throw Error('RECORD_TOO_LARGE_FOR_D1');
  if(parts.length&&bytes+size+1>maxBytes){chunks.push('['+parts.join(',')+']');parts=[];bytes=2;}
  parts.push(text);bytes+=size+1;
 }if(parts.length)chunks.push('['+parts.join(',')+']');return chunks;
}

/** @param {any} session @param {string} token @param {string[]} keys @param {any[]} rows @param {number} total @param {boolean} done @param {number} bytes @param {string} hash @param {string} stamp @param {string[]} order */
export function pageCommitPlan(session,token,keys,rows,total,done,bytes,hash,stamp,order){
 const guard='EXISTS (SELECT 1 FROM collection_sessions WHERE id=? AND lease=? AND day=? AND page=?)', fence=[session.id,token,session.day,session.page];
 const statements=[],stored=new Set(rows.map(r=>r.source_key));
 const observed=keys.map(source_key=>{const [request_no,change_order,sequence]=JSON.parse(source_key);return {source_key,request_no,sequence,change_rank:change_order.replace(/\D/g,'').replace(/^0+/,'')||'0',stored:stored.has(source_key)?1:0};});
 for(const chunk of rowChunks(observed)){
  statements.push({sql:`UPDATE seen_source_keys SET is_active=0 WHERE mode=? AND is_active=1 AND NOT(session_id=? AND day=?) AND source_key IN(SELECT json_extract(value,'$.source_key') FROM json_each(?)) AND ${guard}`,params:[session.mode,session.id,session.day,chunk,...fence]});
  statements.push({sql:`INSERT INTO seen_source_keys(session_id,day,source_key,mode,request_no,sequence,change_rank,stored,is_active) SELECT ?,?,json_extract(value,'$.source_key'),?,json_extract(value,'$.request_no'),json_extract(value,'$.sequence'),json_extract(value,'$.change_rank'),json_extract(value,'$.stored'),1 FROM json_each(?) WHERE ${guard}`,params:[session.id,session.day,session.mode,chunk,...fence]});
 }

 for(const chunk of rowChunks(rows))statements.push({sql:`INSERT INTO delivery_rows(mode,${FIELDS.join(',')},last_session,updated_at) SELECT ?,${FIELDS.map(k=>`json_extract(value,'$.${k}')`).join(',')},?,? FROM json_each(?) WHERE ${guard} ON CONFLICT(mode,source_key) DO UPDATE SET ${FIELDS.filter(k=>k!=='source_key').map(k=>`${k}=excluded.${k}`).join(',')},last_session=excluded.last_session,updated_at=excluded.updated_at`,params:[session.mode,session.id,stamp,chunk,...fence]});
 statements.push({sql:`INSERT INTO collection_receipts(session_id,day,page,fetched,matched,excluded,bytes,hash,committed_at) SELECT ?,?,?,?,?,?,?,?,? WHERE ${guard}`,params:[session.id,session.day,session.page,keys.length,rows.length,keys.length-rows.length,bytes,hash,stamp,...fence]});
 statements.push({sql:`INSERT INTO day_checkpoints(session_id,day,state,next_page,total,fetched,matched,updated_at) SELECT ?,?,?,?,?,?,?,? WHERE ${guard} ON CONFLICT(session_id,day) DO UPDATE SET state=excluded.state,next_page=excluded.next_page,total=excluded.total,fetched=day_checkpoints.fetched+excluded.fetched,matched=day_checkpoints.matched+excluded.matched,updated_at=excluded.updated_at`,params:[session.id,session.day,done?'complete':'incomplete',session.page+1,total,keys.length,rows.length,stamp,...fence]});
 if(done&&total>0){
  statements.push({sql:`UPDATE seen_source_keys SET is_active=0 WHERE mode=? AND day=? AND session_id<>? AND is_active=1 AND ${guard}`,params:[session.mode,session.day,session.id,...fence]});
  statements.push({sql:`SELECT CASE WHEN ${guard} AND (SELECT COUNT(*) FROM seen_source_keys WHERE session_id=? AND day=?)<>? THEN json('INVALID_RECEIPT_COUNT') ELSE 1 END AS verified`,params:[...fence,session.id,session.day,total]});
  statements.push({sql:`UPDATE delivery_rows SET is_active=0,inactive_reason=CASE WHEN EXISTS(SELECT 1 FROM seen_source_keys seen WHERE seen.session_id=? AND seen.day=? AND seen.source_key=delivery_rows.source_key) THEN 'OUTSIDE_TARGET_SCOPE' ELSE 'MISSING_FROM_COMPLETE_SOURCE' END,updated_at=? WHERE mode=? AND source_date=? AND is_active=1 AND last_session<>? AND ${guard}`,params:[session.id,session.day,stamp,session.mode,session.day,session.id,...fence]});
 }
 statements.push({sql:`INSERT INTO dataset_epochs(mode,revision) SELECT ?,1 WHERE ${guard} ON CONFLICT(mode) DO UPDATE SET revision=dataset_epochs.revision+1`,params:[session.mode,...fence]});
 const cursor=done?session.cursor+1:session.cursor,allDone=cursor>=order.length;
 statements.push({sql:'UPDATE collection_sessions SET state=?,cursor=?,day=?,page=?,source_total=?,fetched=fetched+?,matched=matched+?,excluded=excluded+?,completed_days=completed_days+?,max_payload=MAX(max_payload,?),lease=NULL,lease_until=0,updated_at=? WHERE id=? AND lease=? AND day=? AND page=?',params:[allDone?'complete':'ready',cursor,allDone?session.day:order[cursor],done?1:session.page+1,done?null:total,keys.length,rows.length,keys.length-rows.length,done?1:0,bytes,stamp,...fence]});
 return statements;
}
