import {regionWhere} from './read-filters.mjs';
import {SMART_FIXTURE_CODES,SMART_CONTROL_CODE} from './g2b-domain.mjs';
import {validDay} from './collector.mjs';
export const SOURCE_CURRENT_GUARD="NOT EXISTS(SELECT 1 FROM seen_source_keys observed WHERE observed.mode=r.mode AND observed.is_active=1 AND observed.request_no=r.request_no AND observed.sequence=r.sequence AND observed.day>=? AND observed.day<=? AND (length(observed.change_rank)>length(r.change_rank) OR (length(observed.change_rank)=length(r.change_rank) AND observed.change_rank>r.change_rank) OR (observed.source_key=r.source_key AND observed.stored=0)))";
export const VIEW_FIELDS=['source_key','source_date','category','detail_code','detail_name','request_no','request_name','change_order','sequence','change_rank','final_flag','item_id','item_name','model','agency','agency_code','region','vendor','vendor_bizno','contract_no','quantity','unit_price','amount','request_total','is_active','inactive_reason','updated_at'];
export function queryPlan(params,window){
 const mode=params.get('mode')==='synthetic'?'synthetic':'live',history=params.get('view')==='history';
 const from=params.get('from')||window.from,to=params.get('to')||window.to;
 if(!validDay(from)||!validDay(to)||from<window.from||to>window.to||from>to)throw Error('INVALID_DATE_RANGE');
 const fields=VIEW_FIELDS.join(','),args=[mode,window.from,window.to];
 const newer="(length(n.change_rank),n.change_rank,CASE WHEN upper(n.final_flag)='Y' THEN 1 ELSE 0 END,n.source_date,n.updated_at,n.source_key)>(length(r.change_rank),r.change_rank,CASE WHEN upper(r.final_flag)='Y' THEN 1 ELSE 0 END,r.source_date,r.updated_at,r.source_key)";
 const prefix=history?`WITH current AS (SELECT ${fields} FROM delivery_rows WHERE mode=? AND source_date>=? AND source_date<=?)`:`WITH current AS (SELECT ${VIEW_FIELDS.map(x=>'r.'+x).join(',')} FROM delivery_rows r WHERE mode=? AND source_date>=? AND source_date<=? AND is_active=1 AND ${SOURCE_CURRENT_GUARD} AND NOT EXISTS(SELECT 1 FROM delivery_rows n INDEXED BY idx_delivery_current WHERE n.mode=r.mode AND n.is_active=1 AND n.request_no=r.request_no AND n.sequence=r.sequence AND n.source_date>=? AND n.source_date<=? AND ${newer} AND ${SOURCE_CURRENT_GUARD.replaceAll('r.','n.')}))`;if(!history)args.push(window.from,window.to,window.from,window.to,window.from,window.to);
 const where=['source_date>=?','source_date<=?'];args.push(from,to);
 if(params.get('dataset')==='smart'){
  const kind=params.get('kind'),codes=kind==='CONTROL'?[SMART_CONTROL_CODE]:kind==='FIXTURE'?[...SMART_FIXTURE_CODES]:[...SMART_FIXTURE_CODES,SMART_CONTROL_CODE];
  where.push(`detail_code IN (${codes.map(()=>'?').join(',')})`,"source_date>='2026-10-01'","upper(final_flag) NOT IN ('N','NO','0')");args.push(...codes);
 }else where.push("category IN ('LIGHTING','POLE')");
 const category=params.get('category');if(['LIGHTING','POLE'].includes(category||'')){where.push('category=?');args.push(category);}
 const q=(params.get('q')||'').trim().slice(0,100);if(q){where.push("(agency LIKE ? ESCAPE '\\' OR vendor LIKE ? ESCAPE '\\' OR item_name LIKE ? ESCAPE '\\' OR model LIKE ? ESCAPE '\\' OR request_no LIKE ? ESCAPE '\\' OR vendor_bizno LIKE ? ESCAPE '\\' OR contract_no LIKE ? ESCAPE '\\' OR item_id LIKE ? ESCAPE '\\' OR request_name LIKE ? ESCAPE '\\' OR detail_name LIKE ? ESCAPE '\\')");const like='%'+q.replace(/[\\%_]/g,'\\$&')+'%';args.push(like,like,like,like,like,like,like,like,like,like);}
 regionWhere(params.get('region'),where,args);
 for(const k of ['agency']){const v=(params.get(k)||'').trim().slice(0,1024);if(v){where.push(k+'=?');args.push(v);}}
 return {prefix,fields,where,args,mode,from,to,history};
}
// At most eight nine-digit limbs per amount. The population bound also keeps
// each signed limb plus its carry below Number.MAX_SAFE_INTEGER. Whole monetary
// values are never cast to INTEGER or REAL, including for ranking.
export const DELIVERY_SUMMARY_MAX_DIGITS = 72;
export const DELIVERY_SUMMARY_LIMB_DIGITS = 9;
export const DELIVERY_SUMMARY_LIMB_BASE = 1000000000;
export const DELIVERY_SUMMARY_MAX_ROWS = Math.floor(Number.MAX_SAFE_INTEGER / DELIVERY_SUMMARY_LIMB_BASE);
export const DELIVERY_SUMMARY_LIMIT = 200;
const canonicalAmount = "(typeof(amount)='text' AND instr(amount,char(0))=0 AND (amount='0' OR (substr(amount,1,1) BETWEEN '1' AND '9' AND amount NOT GLOB '*[^0-9]*') OR (substr(amount,1,1)='-' AND substr(amount,2,1) BETWEEN '1' AND '9' AND substr(amount,2) NOT GLOB '*[^0-9]*')))";
const summaryStats = `CAST(COUNT(*) AS TEXT) row_count,CAST(COUNT(DISTINCT group_key) AS TEXT) group_count,
  CAST(COALESCE(SUM(amount IS NULL),0) AS TEXT) missing_amounts,
  CAST(COALESCE(SUM(amount IS NOT NULL AND NOT ${canonicalAmount}),0) AS TEXT) invalid_amounts,
  COALESCE(MAX(length(ltrim(amount,'-'))),0) max_digits`;

/** The vendor search selects whole resolved identities, including other aliases
 * and numberless rows. A filtered-out identity cannot make a valid query fail. */
function summaryPrefix(plan, group, vendorQuery, revision) {
 const args=[...plan.args], guard=revision===undefined?'':' AND (SELECT CAST(revision AS TEXT) FROM dataset_epochs WHERE mode=?)=?';
 if(revision!==undefined)args.push(plan.mode,revision);
 const base=`${plan.prefix.replace(/^WITH /,'WITH RECURSIVE ')},filtered AS (
   SELECT agency,agency_code,region,vendor,vendor_bizno,request_no,amount,category FROM current WHERE ${plan.where.join(' AND ')}${guard})`;
 if(group==='agency')return {sql:`${base},selected AS (SELECT agency name,agency_code,region,'' bizno,
   vendor_bizno||':'||vendor counterparty,request_no,amount,category,
   CASE WHEN agency_code<>'' THEN 'C:'||agency_code ELSE 'N:'||json_array(region,agency) END group_key FROM filtered WHERE agency<>'')`,args};
 const q=String(vendorQuery||'').trim().slice(0,100),like='%'+q.replace(/[\\%_]/g,'\\$&')+'%';
 const resolved=`${base},names AS (SELECT lower(trim(vendor)) normalized,COUNT(DISTINCT NULLIF(vendor_bizno,'')) numbers,MAX(vendor_bizno) known_bizno FROM filtered GROUP BY lower(trim(vendor))),
   identified AS (SELECT filtered.*,lower(trim(vendor)) normalized,CASE WHEN vendor_bizno='' AND names.numbers=1 THEN names.known_bizno ELSE vendor_bizno END resolved_bizno FROM filtered JOIN names ON lower(trim(filtered.vendor))=names.normalized),
   scoped AS (SELECT vendor name,'' agency_code,'' region,resolved_bizno bizno,agency counterparty,request_no,amount,category,
     CASE WHEN resolved_bizno<>'' THEN 'B:'||resolved_bizno ELSE 'N:'||normalized END group_key FROM identified WHERE vendor<>'')`;
 if(!q)return {sql:`${resolved},selected AS (SELECT * FROM scoped)`,args};
 args.push(like,like);
 return {sql:`${resolved},matches AS (SELECT group_key FROM scoped GROUP BY group_key
   HAVING MAX(CASE WHEN name LIKE ? ESCAPE '\\' THEN 1 ELSE 0 END)=1 OR MAX(bizno) LIKE ? ESCAPE '\\'),
   selected AS (SELECT scoped.* FROM scoped JOIN matches USING(group_key))`,args};
}

/** One scalar preflight, never the full dataset or all grouped identities. */
export function summaryPreflightQuery(plan,group,vendorQuery='') {
 const base=summaryPrefix(plan,group,vendorQuery);
 return {sql:`${base.sql} SELECT ${summaryStats} FROM selected`,args:base.args};
}
const summaryCount=value=>{
 if(typeof value!=='string'||!/^(0|[1-9]\d*)$/.test(value))throw Error('DELIVERY_SUMMARY_INVALID_AGGREGATE');
 return BigInt(value);
};
const publicCount=value=>value<=BigInt(Number.MAX_SAFE_INTEGER)?Number(value):value.toString();
export function deliverySummaryMoneyState(preflight) {
 const rows=summaryCount(preflight.row_count),groups=summaryCount(preflight.group_count),missing=summaryCount(preflight.missing_amounts),invalid=summaryCount(preflight.invalid_amounts);
 if(groups>rows||missing+invalid>rows||!Number.isSafeInteger(preflight.max_digits)||preflight.max_digits<0)throw Error('DELIVERY_SUMMARY_INVALID_AGGREGATE');
 const moneyUnavailable=invalid>0n?'INVALID_STORED_AMOUNT':preflight.max_digits>DELIVERY_SUMMARY_MAX_DIGITS?'AMOUNT_EXCEEDS_SUMMARY_DIGIT_LIMIT':rows>BigInt(DELIVERY_SUMMARY_MAX_ROWS)?'ROW_COUNT_EXCEEDS_EXACT_SUMMARY_LIMIT':null;
 return {rowCount:publicCount(rows),groupCount:publicCount(groups),missingAmounts:publicCount(missing),invalidAmounts:publicCount(invalid),
   moneyUnavailable,moneyComplete:!moneyUnavailable&&missing===0n,
   monetaryStatus:moneyUnavailable?'unavailable':missing>0n?'partial':'complete',
   limits:{maxAmountDigits:DELIVERY_SUMMARY_MAX_DIGITS,maxRows:DELIVERY_SUMMARY_MAX_ROWS,returnedGroups:DELIVERY_SUMMARY_LIMIT}};
}

/** SQL both sums and ranks exact signed integers before LIMIT. Carry propagation
 * builds fixed-width base-1e9 digits for the sum and its negation. Ordering the
 * signed high carry then the positive digits is exact even below zero. Only
 * at most 200 SQL-ranked canonical decimal strings per page cross the D1/Worker boundary.
 * The service fences validated preflight bounds with the dataset epoch. Direct
 * callers instead receive an in-query safety gate. SQLite may materialize
 * grouping/sort state; Worker transfer is bounded. */
export function summaryQuery(plan,group,vendorQuery='',options={}) {
 const pageSize=options.pageSize??DELIVERY_SUMMARY_LIMIT,offset=options.offset??0;
 if(!Number.isInteger(pageSize)||pageSize<1||pageSize>DELIVERY_SUMMARY_LIMIT||!Number.isInteger(offset)||offset<0||offset>800||(group==='agency'&&offset!==0))throw Error('DELIVERY_SUMMARY_INVALID_PAGE');
 const preflight=options.preflight;
 if(preflight&&deliverySummaryMoneyState(preflight).moneyUnavailable)throw Error('DELIVERY_SUMMARY_UNSAFE_AGGREGATE');
 const limbs=preflight?Math.max(1,Math.ceil(preflight.max_digits/DELIVERY_SUMMARY_LIMB_DIGITS)):DELIVERY_SUMMARY_MAX_DIGITS/DELIVERY_SUMMARY_LIMB_DIGITS;
 const base=summaryPrefix(plan,group,vendorQuery,options.revision),B=DELIVERY_SUMMARY_LIMB_BASE;
 const fenced=preflight&&options.revision!==undefined;
 const safety=fenced?'':`,safety AS (SELECT ${summaryStats} FROM selected)`;
 const guard=fenced?'':`WHERE (SELECT CAST(row_count AS INTEGER)<=${DELIVERY_SUMMARY_MAX_ROWS} AND max_digits<=${limbs*9} AND invalid_amounts='0' FROM safety)`;
 const digits="ltrim(amount,'-')",sums=Array.from({length:limbs},(_,i)=>{
   const limb=`CASE WHEN length(${digits})>${i*9} THEN CAST(substr(${digits},max(1,length(${digits})-${(i+1)*9}+1),min(9,length(${digits})-${i*9})) AS INTEGER) ELSE 0 END`;
   return `COALESCE(SUM((CASE WHEN substr(amount,1,1)='-' THEN -1 ELSE 1 END)*(${limb})),0)`;
 });
 const fields='group_key,name,agency_code,region,bizno,rows,counterparties,missing_amounts,requests,has_lighting,has_pole,limb_sums';
 const limb="json_extract(limb_sums,'$['||step||']')",plus=`(${limb}+positive_carry)`,minus=`(-${limb}+negative_carry)`;
 const carry=x=>`(${x}/${B}-(${x}%${B}<0))`,digit=x=>`printf('%09d',((${x}%${B})+${B})%${B})`;
 const absolute="ltrim(CAST(CASE WHEN positive_carry<0 THEN negative_carry ELSE positive_carry END AS TEXT)||CASE WHEN positive_carry<0 THEN negative_digits ELSE positive_digits END,'0')";
 return {sql:`${base.sql}${safety},
   grouped AS (SELECT group_key,MAX(name) name,MAX(agency_code) agency_code,MAX(region) region,MAX(bizno) bizno,
     COUNT(*) rows,COUNT(DISTINCT counterparty) counterparties,SUM(amount IS NULL) missing_amounts,COUNT(DISTINCT request_no) requests,
     MAX(category='LIGHTING') has_lighting,MAX(category='POLE') has_pole,
     json_array(${sums.join(',')}) limb_sums FROM selected
     ${guard} GROUP BY group_key),
   carried AS (SELECT ${fields},0 step,0 positive_carry,0 negative_carry,'' positive_digits,'' negative_digits FROM grouped
     UNION ALL SELECT ${fields},step+1,${carry(plus)},${carry(minus)},${digit(plus)}||positive_digits,${digit(minus)}||negative_digits
     FROM carried WHERE step<${limbs})
   SELECT name,${group==='agency'?'agency_code,region':'bizno'},rows,counterparties,
     CASE WHEN rows=missing_amounts THEN NULL ELSE (CASE WHEN positive_carry<0 THEN '-' ELSE '' END)||COALESCE(NULLIF(${absolute},''),'0') END amount,
     missing_amounts,requests,has_lighting,has_pole,CASE WHEN missing_amounts=0 THEN 1 ELSE 0 END money_complete
   FROM carried WHERE step=${limbs}
   ORDER BY (rows>missing_amounts) DESC,positive_carry DESC,positive_digits DESC,name,bizno,agency_code,group_key LIMIT ${pageSize} OFFSET ${offset}`,args:base.args,limbs};
}

// Legacy int64 diagnostic retained for compatibility; exact summaries do not use it.
export function amountRangeQuery(plan){return {sql:`${plan.prefix} SELECT COUNT(*) AS invalid_amounts FROM current WHERE ${plan.where.join(' AND ')} AND amount IS NOT NULL AND (length(ltrim(amount,'-'))>19 OR (length(ltrim(amount,'-'))=19 AND ltrim(amount,'-')>'9223372036854775807'))`,args:plan.args};}
