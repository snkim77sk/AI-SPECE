import {normalizeDelivery, identity} from './g2b-domain.mjs';
import {validDay} from './collector.mjs';

export const LIMITS=Object.freeze({pageSize:999,maxRows:20000,maxPages:21,maxAttempts:42,maxResponseBytes:8388608,maxPayloadBytes:67108864,maxBindingBytes:1400000,maxStatements:64,maxRunPages:8,maxRunMs:90000});
export const VERSION='generation-v1;identity-strict-v1;canonical-json-v1;classification-v35-smart-v1';
const encoder=new TextEncoder();
const ID_ALIASES=['dlvrReqNo','deliveryReqNo','reqNo','prdctSno','dlvrReqDtlSeq','dlvrReqDtlSn','detailSeq','seq','dlvrReqChgOrd','deliveryReqChangeOrder','dlvrReqChangeOrd'];
const DATE_ALIASES=['dlvrReqRcptDate','IntlCntrctDlvrReqDate','cntrctDlvrReqDate','deliveryReqDate'];
const fail=code=>{throw Error(code);};

/** Canonical parsed JSON, not an authoritative source snapshot or raw-wire signature. */
export function canonical(value){
 const ancestors=new Set();let nodes=0;
 function visit(v,depth){
  if(++nodes>250000||depth>32)fail('SOURCE_CANONICAL_LIMIT');
  if(v===null)return 'null';
  if(typeof v==='string'||typeof v==='boolean')return JSON.stringify(v);
  if(typeof v==='number'){
   if(!Number.isFinite(v)||Math.abs(v)>Number.MAX_SAFE_INTEGER||(Number.isInteger(v)&&!Number.isSafeInteger(v)))fail('SOURCE_NUMBER_OUT_OF_RANGE');
   return Object.is(v,-0)?'-0':JSON.stringify(v);
  }
  if(typeof v!=='object')fail('SOURCE_CANONICAL_UNSUPPORTED');
  if(ancestors.has(v))fail('SOURCE_CANONICAL_CYCLE');
  const array=Array.isArray(v);
  if(Object.getPrototypeOf(v)!==(array?Array.prototype:Object.prototype))fail('SOURCE_CANONICAL_PROTOTYPE');
  const keys=Reflect.ownKeys(v);
  if(keys.some(k=>typeof k!=='string'))fail('SOURCE_CANONICAL_UNSUPPORTED');
  const descriptors=Object.getOwnPropertyDescriptors(v);
  if(keys.some(k=>!('value' in descriptors[k])||(!descriptors[k].enumerable&&!(array&&k==='length'))))fail('SOURCE_CANONICAL_UNSUPPORTED');
  ancestors.add(v);let out;
  if(array){
   if(keys.length!==v.length+1)fail('SOURCE_CANONICAL_SPARSE');
   const parts=[];
   for(let n=0;n<v.length;n++){if(!Object.hasOwn(v,n))fail('SOURCE_CANONICAL_SPARSE');parts.push(visit(descriptors[String(n)].value,depth+1));}
   out='['+parts.join(',')+']';
  }else out='{'+keys.sort().map(k=>JSON.stringify(k)+':'+visit(descriptors[k].value,depth+1)).join(',')+'}';
  ancestors.delete(v);return out;
 }
 return visit(value,0);
}
export async function sha256(value){
 if(typeof value!=='string')fail('SOURCE_HASH_INPUT_INVALID');
 return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',encoder.encode(value)))).map(x=>x.toString(16).padStart(2,'0')).join('');
}
export function strictIdentity(row){
 if(!row||Array.isArray(row)||Object.getPrototypeOf(row)!==Object.prototype)fail('SOURCE_ID_INVALID');
 for(const name of ID_ALIASES){
  const descriptor=Object.getOwnPropertyDescriptor(row,name);
  if(!descriptor)continue;
  if(!('value' in descriptor)||typeof descriptor.value!=='string')fail('SOURCE_ID_TYPE_INVALID');
  if(descriptor.value.length>1024)fail('SOURCE_ID_TOO_LONG');
 }
 return JSON.stringify(identity(row));
}
function sourceDate(row,day){
 let text='';
 for(const name of DATE_ALIASES){
  if(!Object.hasOwn(row,name)||row[name]===null||row[name]==='')continue;
  if(typeof row[name]!=='string'||row[name].length>1024)fail('SOURCE_DATE_INVALID');
  if(row[name].trim()){text=row[name].trim();break;}
 }
 if(!text)return {source_date:day,date_basis:'query_date'};
 const digits=text.replace(/\D/g,'');
 // Nonempty malformed source dates are not silently converted to a query date.
 if(digits.length<8)fail('SOURCE_DATE_INVALID');
 const date=digits.slice(0,4)+'-'+digits.slice(4,6)+'-'+digits.slice(6,8);
 if(!validDay(date))fail('SOURCE_DATE_INVALID');
 return {source_date:date,date_basis:'source'};
}

/** Ordinal is the 1-based position in the entire date traversal. */
export async function validateGenerationPage(raw,{page,total=null,day,window}){
 if(!Number.isSafeInteger(page)||page<1||page>LIMITS.maxPages)fail('SOURCE_PAGE_LIMIT');
 if(!validDay(day)||!window||!validDay(window.from)||!validDay(window.to)||window.from>window.to)fail('SOURCE_DATE_INVALID');
 if(total!==null&&(!Number.isSafeInteger(total)||total<0||total>LIMITS.maxRows))fail('SOURCE_TOTAL_INVALID');
 if(page>1&&total===null)fail('SOURCE_PREVIOUS_TOTAL_REQUIRED');
 const full=canonical(raw);
 if(encoder.encode(full).byteLength>LIMITS.maxResponseBytes)fail('SOURCE_RESPONSE_TOO_LARGE');
 const h=raw?.response?.header,b=raw?.response?.body;
 if(!h||!['00','0'].includes(String(h.resultCode)))fail('SOURCE_API_ERROR');
 const asInteger=v=>typeof v==='number'&&Number.isSafeInteger(v)?v:typeof v==='string'&&/^\d+$/.test(v)?Number(v):NaN;
 const count=asInteger(b?.totalCount);
 if(!Number.isSafeInteger(count)||count<0)fail('SOURCE_TOTAL_INVALID');
 if(count>LIMITS.maxRows)fail('SOURCE_CAPACITY_EXCEEDED');
 if(total!==null&&count!==total)fail('SOURCE_TOTAL_CHANGED');
 if(asInteger(b.pageNo)!==page||asInteger(b.numOfRows)!==LIMITS.pageSize)fail('SOURCE_PAGE_MISMATCH');
 const pages=Math.max(1,Math.ceil(count/LIMITS.pageSize));
 if(pages>LIMITS.maxPages||page>pages)fail('SOURCE_PAGE_LIMIT');
 let items=b.items?.item??b.items;if(items===''||items==null)items=[];if(!Array.isArray(items))items=[items];
 if(items.length!==Math.max(0,Math.min(LIMITS.pageSize,count-(page-1)*LIMITS.pageSize)))fail('SOURCE_ROW_COUNT_MISMATCH');
 const rows=[],seen=new Map(),samples=[];let duplicates=0,matched=0,excludedCount=0,payloadBytes=2;
 for(let index=0;index<items.length;index++){
  const rawRow=items[index],source_key=strictIdentity(rawRow),payload_hash=await sha256(canonical(rawRow));
  const prior=seen.get(source_key);
  if(prior){
   duplicates++;
   if(samples.length<8)samples.push({firstPage:page,currentPage:page,identityHash:await sha256(source_key),firstPayloadHash:prior.payload_hash,currentPayloadHash:payload_hash,samePayload:prior.payload_hash===payload_hash});
  }else seen.set(source_key,{payload_hash});
  const date=sourceDate(rawRow,day),normalized=normalizeDelivery(rawRow,day,window,{includeSmart:true});
  const target_json=normalized.row?JSON.stringify(normalized.row):null,excluded=normalized.row?null:normalized.excluded;
  if(target_json!==null)matched++;else if(typeof excluded==='string'&&excluded)excludedCount++;else fail('SOURCE_CLASSIFICATION_INVALID');
  const row={source_key,ordinal:(page-1)*LIMITS.pageSize+index+1,payload_hash,target_json,excluded,...date};
  const size=encoder.encode(JSON.stringify(row)).byteLength;
  if(size+2>LIMITS.maxBindingBytes)fail('RECORD_TOO_LARGE_FOR_D1');
  payloadBytes+=size+(index?1:0);rows.push(row);
 }
 if(duplicates){const error=Error('SOURCE_DUPLICATE_ID');error.conflict={count:duplicates,samples};throw error;}
 if(matched+excludedCount!==items.length)fail('SOURCE_CLASSIFICATION_INVALID');
 if(payloadBytes>LIMITS.maxPayloadBytes)fail('SOURCE_CAPACITY_EXCEEDED');
 const ordered_hash=await sha256(canonical(rows.map(r=>[r.ordinal,r.source_key,r.payload_hash])));
 return {total:count,done:page===pages,rows,ordered_hash,payloadBytes,matched,excluded:excludedCount};
}
