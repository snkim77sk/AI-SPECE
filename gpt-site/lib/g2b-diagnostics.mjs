import {databaseErrorCode} from './db-errors.mjs';
import {dayOrder} from './g2b-domain.mjs';
import {rowChunks} from './g2b-sql.mjs';

const SOURCE_CODES=new Set(['API_KEY_NOT_CONFIGURED','LIVE_APPROVAL_REQUIRED','DAILY_BUDGET_NOT_CONFIRMED','G2B_QUOTA_SCOPE_NOT_REVIEWED','DATE_OUTSIDE_CURRENT_SCOPE','LIVE_READINESS_REQUIRED','LOCAL_DAILY_BUDGET_REACHED','SOURCE_NETWORK_OR_TIMEOUT','SOURCE_RESPONSE_TOO_LARGE','SOURCE_EMPTY_RESPONSE','SOURCE_JSON_INVALID','SOURCE_API_ERROR','SOURCE_TOTAL_INVALID','SOURCE_PAGE_MISMATCH','SOURCE_TOTAL_CHANGED','SOURCE_ROW_COUNT_MISMATCH','SOURCE_DUPLICATE_ID','SOURCE_ID_MISSING','SOURCE_DATE_INVALID','SOURCE_NUMBER_OUT_OF_RANGE','RECORD_TOO_LARGE_FOR_D1','LEASE_LOST','SOURCE_PAGE_OVERLAP','SOURCE_ALLOCATION_REVIEW_REQUIRED']);
const STORED_CODES=new Set([...SOURCE_CODES,'STORAGE_OR_SOURCE_FAILED','SOURCE_PROCESSING_FAILED','EXPIRED_REQUEST_REVIEW_REQUIRED','DATABASE_BUSY','DATABASE_OVERLOADED','DATABASE_TIMEOUT','DATABASE_CONNECTION','DATABASE_RESOURCE_LIMIT','DATABASE_STATEMENT_LIMIT','DATABASE_SCHEMA','DATABASE_AMOUNT_OVERFLOW','DATABASE_D1_ERROR','DATABASE_UNKNOWN']);
export const safeStoredError=value=>value==null||value===''?null:STORED_CODES.has(value)||/^SOURCE_HTTP_[1-5]\d\d$/.test(value)?value:'COLLECTION_ERROR_UNCLASSIFIED';
const DB_PHASES=new Set(['quota','count-response','overlap-check','commit','error-record']);

/** Never emit an exception message, cause, SQL, bind value, URL or source row. */
export function collectionFailureCode(error,phase){
 const message=typeof error?.message==='string'?error.message:'';
 if(SOURCE_CODES.has(message)||/^SOURCE_HTTP_[1-5]\d\d$/.test(message))return message;
 const cause=typeof error?.cause?.message==='string'?error.cause.message:'';
 if(phase==='commit'&&/UNIQUE constraint failed:\s*seen_source_keys\.session_id,\s*seen_source_keys\.day,\s*seen_source_keys\.source_key(?:\s|:|$)/i.test(message+' '+cause))return 'SOURCE_PAGE_OVERLAP';
 return DB_PHASES.has(phase)?databaseErrorCode(error):'SOURCE_PROCESSING_FAILED';
}

export async function assertNoPageOverlap(db,session,keys){
 for(const chunk of rowChunks(keys)){
  const found=await db.prepare('SELECT 1 AS overlap FROM seen_source_keys WHERE session_id=? AND day=? AND source_key IN (SELECT value FROM json_each(?)) LIMIT 1').bind(session.id,session.day,chunk).first();
  if(found)throw Error('SOURCE_PAGE_OVERLAP');
 }
}

export function reportCollectionFailure(error,phase,session,metrics={}){
 const code=collectionFailureCode(error,phase),incident=crypto.randomUUID();
 const event={area:'g2b_collection',code,incident,phase,sessionId:session.id,day:session.day,page:session.page,
  jsonDecoded:metrics.jsonDecoded===true,responseBytes:Number.isSafeInteger(metrics.responseBytes)?metrics.responseBytes:null,
  sourceRows:Number.isSafeInteger(metrics.sourceRows)?metrics.sourceRows:null,targetRows:Number.isSafeInteger(metrics.targetRows)?metrics.targetRows:null};
 try{console.error(JSON.stringify(event));}catch{}return {code,incident};
}

/** One SQL statement gives a consistent metadata snapshot. No writes or provider calls. */
export const RECOVERY_SNAPSHOT_SQL=`SELECT s.id,s.mode,s.state,s.day,s.page,s.error,s.updated_at,s.lease_until,s.fetched,s.matched,s.calls,s.from_day,s.to_day,s.cursor,s.completed_days,
 (SELECT COUNT(*) FROM day_checkpoints d WHERE d.session_id=s.id AND (d.state NOT IN ('complete','incomplete') OR d.next_page<>(SELECT COALESCE(MAX(page),0)+1 FROM collection_receipts r WHERE r.session_id=s.id AND r.day=d.day) OR d.fetched<>(SELECT COALESCE(SUM(fetched),0) FROM collection_receipts r WHERE r.session_id=s.id AND r.day=d.day) OR d.matched<>(SELECT COALESCE(SUM(matched),0) FROM collection_receipts r WHERE r.session_id=s.id AND r.day=d.day) OR (SELECT COALESCE(MIN(page),0) FROM collection_receipts r WHERE r.session_id=s.id AND r.day=d.day)<>1 OR (SELECT COUNT(*) FROM collection_receipts r WHERE r.session_id=s.id AND r.day=d.day)<>(SELECT COALESCE(MAX(page),0) FROM collection_receipts r WHERE r.session_id=s.id AND r.day=d.day) OR d.fetched<>(SELECT COUNT(*) FROM seen_source_keys k WHERE k.session_id=s.id AND k.day=d.day) OR (d.state='complete' AND d.total<>d.fetched))) AS invalid_checkpoints,
 (SELECT COUNT(*) FROM day_checkpoints c WHERE c.session_id=s.id AND c.state='complete') AS completed_checkpoints,
 (SELECT COUNT(*) FROM seen_source_keys k WHERE k.session_id=s.id) AS seen_count,
 (SELECT COUNT(*) FROM seen_source_keys k WHERE k.session_id=s.id AND k.day=s.day) AS day_seen_count,
 (SELECT COUNT(*) FROM collection_receipts r WHERE r.session_id=s.id) AS receipt_count,
 (SELECT COALESCE(SUM(fetched),0) FROM collection_receipts r WHERE r.session_id=s.id) AS receipt_fetched,
 (SELECT COALESCE(SUM(matched),0) FROM collection_receipts r WHERE r.session_id=s.id) AS receipt_matched,
 (SELECT COUNT(*) FROM collection_receipts r WHERE r.session_id=s.id AND r.day=s.day AND r.page=s.page) AS current_page_receipts,
 (SELECT COUNT(*) FROM collection_receipts r WHERE r.session_id=s.id AND r.day=s.day) AS day_receipt_count,
 (SELECT COALESCE(MAX(page),0) FROM collection_receipts r WHERE r.session_id=s.id AND r.day=s.day) AS day_last_receipt,
 (SELECT COALESCE(MIN(page),0) FROM collection_receipts r WHERE r.session_id=s.id AND r.day=s.day) AS day_first_receipt,
 (SELECT COALESCE(SUM(fetched),0) FROM collection_receipts r WHERE r.session_id=s.id AND r.day=s.day) AS day_receipt_fetched,
 (SELECT COALESCE(SUM(matched),0) FROM collection_receipts r WHERE r.session_id=s.id AND r.day=s.day) AS day_receipt_matched,
 c.next_page AS checkpoint_next_page,c.fetched AS checkpoint_fetched,c.matched AS checkpoint_matched,c.state AS checkpoint_state
 FROM collection_sessions s LEFT JOIN day_checkpoints c ON c.session_id=s.id AND c.day=s.day WHERE s.id=?`;

export function recoveryAssessment(s,now=Date.now()){
 if(!s)throw Error('COLLECTION_SESSION_NOT_FOUND');
 const numbers=['page','fetched','matched','calls','receipt_count','receipt_fetched','receipt_matched','current_page_receipts','day_receipt_count','day_last_receipt','day_receipt_fetched','day_receipt_matched','day_first_receipt','seen_count','day_seen_count','cursor','completed_days','completed_checkpoints','invalid_checkpoints'];
 if(numbers.some(k=>!Number.isSafeInteger(s[k])||s[k]<0))throw Error('COLLECTION_DIAGNOSTIC_INVALID');
 const order=dayOrder(s.from_day,s.to_day);
 const totals=s.invalid_checkpoints===0&&s.fetched===s.receipt_fetched&&s.matched===s.receipt_matched&&s.seen_count===s.receipt_fetched&&s.calls>=s.receipt_count&&s.completed_days===s.completed_checkpoints&&s.cursor===s.completed_days;
 const cursorMatches=s.state==='complete'?s.cursor===order.length&&s.day===order.at(-1):order[s.cursor]===s.day;
 const empty=s.page===1&&s.day_receipt_count===0&&s.checkpoint_next_page===null;
 const partial=s.page>1&&s.day_receipt_count===s.page-1&&s.day_first_receipt===1&&s.day_seen_count===s.day_receipt_fetched&&s.day_last_receipt===s.page-1&&s.checkpoint_next_page===s.page&&s.checkpoint_state==='incomplete'&&s.checkpoint_fetched===s.day_receipt_fetched&&s.checkpoint_matched===s.day_receipt_matched;
 const completedDay=s.checkpoint_state==='complete'&&s.checkpoint_next_page===s.day_last_receipt+1&&s.day_receipt_count===s.day_last_receipt&&s.day_first_receipt===1&&s.checkpoint_fetched===s.day_receipt_fetched&&s.checkpoint_matched===s.day_receipt_matched&&s.day_seen_count===s.day_receipt_fetched;
 const busy=s.lease_until>now,expired=s.state==='running'&&!busy;
 const uncommitted=!busy&&!expired&&s.state!=='complete'&&s.current_page_receipts===0&&totals&&cursorMatches&&(empty||partial);
 return {sessionId:s.id,state:s.state,day:s.day,page:s.page,error:safeStoredError(s.error),updatedAt:s.updated_at,
  receiptCount:s.receipt_count,respondedCalls:s.calls,committedSourceRows:s.receipt_fetched,committedTargetRows:s.receipt_matched,
  metadataConsistent:totals&&cursorMatches&&((s.state==='complete'&&completedDay)||empty||partial),pageCommitState:busy?'IN_FLIGHT':expired?'EXECUTION_OUTCOME_UNCERTAIN':s.state==='complete'&&totals&&cursorMatches&&completedDay?'SESSION_COMPLETE':uncommitted?'NOT_COMMITTED':'REVIEW_REQUIRED',
  resumeAuthorized:false,providerCalls:0,sourceIoPerformed:false,mutationsPerformed:false,
  nextAction:busy?'WAIT_FOR_EXISTING_REQUEST':expired?'REVIEW_EXPIRED_REQUEST_BEFORE_ACTION':s.state==='complete'&&totals&&cursorMatches&&completedDay?'NO_RECOVERY_NEEDED':['paused','cancelled'].includes(s.state)?'RESPECT_USER_STOP':s.state==='error'?'DIAGNOSE_ERROR_BEFORE_ANY_RETRY':'REVIEW_CHECKPOINT_BEFORE_ACTION'};
}
