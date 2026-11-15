export const PAGE_SIZE = 50;
export const MAX_BYTES = 2 * 1024 * 1024;
export const DAILY_LIMIT = 950;
export const ENDPOINT = 'https://apis.data.go.kr/1230000/at/ShoppingMallPrdctInfoService/getDlvrReqDtlInfoList';
export function sourceIdentity(r) {
 const req=String(r.dlvrReqNo||'').trim(), seq=String(r.prdctSno||'').trim();
 if(!req||!seq) throw new Error('SOURCE_ID_MISSING');
 const c=String(r.dlvrReqChgOrd||'0');
 return `${req}|${/^\d+$/.test(c)?String(Number(c)):c}|${seq}`;
}
export function fixture(page, size=PAGE_SIZE, total=255) {
 const from=(page-1)*size;
 return {response:{header:{resultCode:'00',resultMsg:'NORMAL SERVICE'},body:{pageNo:page,numOfRows:size,totalCount:total,
 items:Array.from({length:Math.max(0,Math.min(size,total-from))},(_,i)=>{
 const n=from+i+1;
 return {dlvrReqNo:`SYNTHETIC-${String(n).padStart(6,'0')}`,dlvrReqChgOrd:'0',prdctSno:'1',
 prdctClsfcNoNm:['LED 실내조명','도로조명 기구','경관조명 기구'][n%3],dminsttNm:`합성 기관 ${n%7+1}`,dlvrReqAmt:String(100000+n*1200),isSynthetic:true};
 })}}};
}
/** @param {any} raw @param {number} page @param {number} size @param {number|null} previousTotal @param {(row:any)=>string} identify */
export function parsePage(raw, page, size, previousTotal=null, identify=sourceIdentity) {
 const h=raw?.response?.header, b=raw?.response?.body;
 if(!h||!['00','0'].includes(String(h.resultCode))) throw new Error('SOURCE_API_ERROR');
 const total=Number(b?.totalCount);
 if(!Number.isSafeInteger(total)||total<0) throw new Error('SOURCE_TOTAL_INVALID');
 if(Number(b.pageNo)!==page||Number(b.numOfRows)!==size) throw new Error('SOURCE_PAGE_MISMATCH');
 if(previousTotal!==null&&total!==previousTotal) throw new Error('SOURCE_TOTAL_CHANGED');
 let rows=b.items?.item??b.items;
 if(rows===''||rows==null) rows=[];
 if(!Array.isArray(rows)) rows=[rows];
 const expected=Math.max(0,Math.min(size,total-(page-1)*size));
 if(rows.length!==expected) throw new Error('SOURCE_ROW_COUNT_MISMATCH');
 const ids=rows.map(identify);
 if(new Set(ids).size!==ids.length) throw new Error('SOURCE_DUPLICATE_ID');
 return {rows,total,keys:ids,done:page*size>=total};
}
export async function readBounded(response, max=MAX_BYTES, withHash=false) {
 if(!response.ok) throw new Error(`SOURCE_HTTP_${response.status}`);
 if(Number(response.headers.get('content-length')||0)>max) { await response.body?.cancel(); throw new Error('SOURCE_RESPONSE_TOO_LARGE'); }
 const reader=response.body?.getReader(); if(!reader) throw new Error('SOURCE_EMPTY_RESPONSE');
 const chunks=[]; let n=0;
 while(true) { const {done,value}=await reader.read(); if(done)break; n+=value.byteLength;
  if(n>max) {await reader.cancel();throw new Error('SOURCE_RESPONSE_TOO_LARGE');} chunks.push(value); }
 let all=new Uint8Array(n);let offset=0;for(const c of chunks){all.set(c,offset);offset+=c.length;} chunks.length=0;
 let hash='';if(withHash){const digest=await crypto.subtle.digest('SHA-256',all);hash=Array.from(new Uint8Array(digest)).map(b=>b.toString(16).padStart(2,'0')).join('');}
 const text=new TextDecoder().decode(all);all=new Uint8Array(0);let raw;
 try {raw=JSON.parse(text);} catch {throw new Error('SOURCE_JSON_INVALID');}
 return {raw,bytes:n,hash};
}
export function kstDate(now=new Date()) {return new Date(now.getTime()+9*3600000).toISOString().slice(0,10);}
export function yesterdayKst() {return kstDate(new Date(Date.now()-86400000));}
export function validDay(s) {return /^\d{4}-\d{2}-\d{2}$/.test(s)&&!Number.isNaN(new Date(s+'T00:00:00Z').getTime())&&new Date(s+'T00:00:00Z').toISOString().slice(0,10)===s;}
export async function loadPage(job,key) {
 if(job.mode==='synthetic') {const raw=fixture(job.page,job.page_size,job.total);return {raw,bytes:new TextEncoder().encode(JSON.stringify(raw)).length};}
 const url=new URL(ENDPOINT);
 for(const [k,v] of Object.entries({serviceKey:key,pageNo:job.page,numOfRows:job.page_size,type:'json',inqryDiv:'1',inqryBgnDate:job.day.replaceAll('-',''),inqryEndDate:job.day.replaceAll('-','')}))url.searchParams.set(k,String(v));
 let response;try {response=await fetch(url,{signal:AbortSignal.timeout(20000)});}catch{throw new Error('SOURCE_NETWORK_OR_TIMEOUT');}
 return readBounded(response);
}
