import {ENDPOINT} from './collector.mjs';
import {FAST_MAX_BYTES} from './g2b-domain.mjs';
// Error values are never stringified. Only exact, finite category members survive.
const NAMES=new Set(['TypeError','AbortError','TimeoutError','SyntaxError','Error']);
const CODES=new Set(['ECONNRESET','ECONNREFUSED','ENOTFOUND','EAI_AGAIN','ETIMEDOUT','UND_ERR_CONNECT_TIMEOUT','UND_ERR_SOCKET']);
export function safeNetworkError(error){
 let name='Other',code='OTHER';
 try{const observedName=error?.name;if(NAMES.has(observedName))name=observedName;}catch{}
 try{const observedCode=error?.code;if(CODES.has(observedCode))code=observedCode;}catch{}
 return {errorName:name,errorCategory:code};
}
export async function diagnosticFetch(source,key,deadline,metrics){
 const controller=new AbortController();let timer,reader;let finished=false;
 const cancel=()=>{try{void reader?.cancel().catch(()=>{});}catch{}};
 const ms=Math.max(0,Math.min(25000,deadline-Date.now()));
 const timeout=new Promise((_,reject)=>{timer=setTimeout(()=>{metrics.timeoutObserved=true;controller.abort();cancel();reject(Error('DIAGNOSTIC_TIMEOUT'));},ms);});
 const bounded=p=>Promise.race([p,timeout]);
 let response;
 try{
  if(ms<=0)throw Error('DIAGNOSTIC_DEADLINE');
  const url=new URL(ENDPOINT);
  for(const [k,v] of Object.entries({serviceKey:key,pageNo:source.page,numOfRows:source.page_size,type:'json',inqryDiv:'1',inqryBgnDate:source.day.replaceAll('-',''),inqryEndDate:source.day.replaceAll('-','')}))url.searchParams.set(k,String(v));
  metrics.phase='source';
  try{const pending=fetch(url,{redirect:'manual',signal:controller.signal}).then(r=>{if(finished){try{void r.body?.cancel().catch(()=>{});}catch{}}return r;});response=await bounded(pending);}
  catch(e){Object.assign(metrics,safeNetworkError(e));throw Error(metrics.timeoutObserved?'DIAGNOSTIC_TIMEOUT':'SOURCE_FETCH_FAILED');}
  metrics.httpStatus=response.status;
  if(response.status>=300&&response.status<400){void response.body?.cancel().catch(()=>{});throw Error('SOURCE_REDIRECT');}
  metrics.phase='body';
  try{
   if(!response.ok)throw Error(`SOURCE_HTTP_${response.status}`);
   if(Number(response.headers.get('content-length')||0)>FAST_MAX_BYTES){void response.body?.cancel().catch(()=>{});throw Error('SOURCE_RESPONSE_TOO_LARGE');}
   reader=response.body?.getReader();if(!reader)throw Error('SOURCE_EMPTY_RESPONSE');
   const chunks=[];let bytes=0;
   while(true){const {done,value}=await bounded(reader.read());if(done)break;bytes+=value.byteLength;if(bytes>FAST_MAX_BYTES){cancel();throw Error('SOURCE_RESPONSE_TOO_LARGE');}chunks.push(value);}
   const all=new Uint8Array(bytes);let offset=0;for(const part of chunks){all.set(part,offset);offset+=part.length;}chunks.length=0;
   const digest=await bounded(crypto.subtle.digest('SHA-256',all));
   const hash=Array.from(new Uint8Array(digest)).map(b=>b.toString(16).padStart(2,'0')).join('');
   let raw;try{raw=JSON.parse(new TextDecoder().decode(all));}catch{throw Error('SOURCE_JSON_INVALID');}
   return {raw,bytes,hash};
  }
  catch(e){Object.assign(metrics,safeNetworkError(e));if(metrics.timeoutObserved)throw Error('DIAGNOSTIC_TIMEOUT');throw e;}
 }finally{finished=true;clearTimeout(timer);controller.abort();cancel();if(!reader){try{void response?.body?.cancel().catch(()=>{});}catch{}}}
}
