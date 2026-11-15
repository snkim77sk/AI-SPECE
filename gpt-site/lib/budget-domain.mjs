import {validDay} from './collector.mjs';
import {roundedProduct} from './g2b-domain.mjs';

export const LOFIN_LIMIT=500, LOFIN_PAGE_SIZE=1000, LOFIN_MAX_BYTES=8*1024*1024;
export const LOFIN_ENDPOINT='https://www.lofin365.go.kr/lf/hub/';
const object=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);
const list=x=>Array.isArray(x)?x:[x];
const text=(r,...names)=>{for(const k of names)if(r[k]!==undefined&&r[k]!==null&&String(r[k]).trim()!==''){const v=String(r[k]).trim();if(v.length>8192)throw Error('LOFIN_FIELD_TOO_LARGE');return v;}return '';};
export function stableJson(x){if(Array.isArray(x))return '['+x.map(stableJson).join(',')+']';if(object(x))return '{'+Object.keys(x).sort().map(k=>JSON.stringify(k)+':'+stableJson(x[k])).join(',')+'}';return JSON.stringify(x);}
export async function digest(x){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(x)))).map(b=>b.toString(16).padStart(2,'0')).join('');}
export function parseLofin(raw,service){
 if(!['QWGJK','AIDFA'].includes(service)||!object(raw))throw Error('LOFIN_SCHEMA');
 if(!(service in raw)){const r=Array.isArray(raw.RESULT)&&raw.RESULT.length===1?raw.RESULT[0]:raw.RESULT;if(object(r)&&r.CODE==='INFO-200')return {rows:[],total:0};throw Error('LOFIN_STATUS_OR_ENVELOPE');}
 let rows=[],heads=[],rowSeen=false,headSeen=false;
 for(const entry of list(raw[service])){if(!object(entry))throw Error('LOFIN_SCHEMA');if('head'in entry){if(headSeen)throw Error('LOFIN_DUPLICATE_CONTAINER');headSeen=true;heads=list(entry.head);}
  if('row'in entry){if(rowSeen)throw Error('LOFIN_DUPLICATE_CONTAINER');rowSeen=true;rows=entry.row===null||entry.row===''?[]:list(entry.row);if(rows.some(r=>!object(r)))throw Error('LOFIN_SCHEMA');}}
 const counts=[],codes=[];for(const h of heads){if(!object(h))throw Error('LOFIN_SCHEMA');if('list_total_count'in h){const n=String(h.list_total_count);if(!/^\d+$/.test(n)||!Number.isSafeInteger(Number(n)))throw Error('LOFIN_TOTAL_INVALID');counts.push(Number(n));}if('RESULT'in h){if(!object(h.RESULT))throw Error('LOFIN_SCHEMA');codes.push(h.RESULT.CODE);}}
 if(!codes.length||new Set(codes).size!==1||!['INFO-000','INFO-200'].includes(codes[0]))throw Error('LOFIN_SOURCE_STATUS');if(new Set(counts).size>1)throw Error('SOURCE_TOTAL_CHANGED');
 let total=counts[0]??null;if(codes[0]==='INFO-200'){if(rows.length||total>0)throw Error('LOFIN_EMPTY_CONTRADICTION');return {rows:[],total:0};}
 if(!rowSeen)throw Error('LOFIN_ROWS_MISSING');if(total===0&&rows.length)total=null;return {rows,total};
}
export function budgetPageState(session,parsed){
 const count=parsed.rows.length,size=session.page_size;if(count>size)throw Error('LOFIN_PAGE_TOO_LARGE');
 const previous=session.source_total,provided=parsed.total===0&&count===0&&previous===null&&session.fetched>0?null:parsed.total;if(previous!==null&&provided!==null&&previous!==provided)throw Error('SOURCE_TOTAL_CHANGED');
 const total=previous??provided,fetched=session.fetched+count;
 if(total!==null&&fetched>total)throw Error('SOURCE_TOTAL_UNDERRUN');if(total!==null&&fetched<total&&!count)throw Error('PREMATURE_EMPTY_PAGE');
 const done=total!==null?fetched===total:count===0||(count<size&&session.saw_full===1);
 return {total,done,sawFull:session.saw_full===1||count===size?1:0};
}
export function budgetIdentity(r,s){
 const year=text(r,'fyr')||String(s.fiscal_year);if(year!==String(s.fiscal_year))throw Error('LOFIN_YEAR_MISMATCH');
 const region=text(r,'wa_laf_cd','wa_laf_hg_nm'),org=text(r,'laf_cd','laf_hg_nm');
 if(s.service==='QWGJK'){
  const date=text(r,'exe_ymd');if(date&&date.replaceAll('-','')!==s.snapshot_date.replaceAll('-',''))throw Error('LOFIN_DATE_MISMATCH');
  const project=text(r,'dbiz_cd','dbiz_nm');if(!project)throw Error('LOFIN_PROJECT_MISSING');
  return JSON.stringify([year,region,org,text(r,'dept_cd','dpt_cd','deptCode','departmentCode','dept_nm','dpt_nm','deptName','departmentName'),project,text(r,'acnt_dv_cd','acnt_dv_nm')]);
 }
 const structure=[text(r,'fld_cd','fld_nm'),text(r,'sect_cd','sect_nm','part_cd','part_nm'),text(r,'acnt_dv_cd','acnt_dv_nm')];
 return JSON.stringify([year,region,org,...structure,...(structure.some(Boolean)?[]:[stableJson(r)])]);
}
const has=(v,words)=>words.some(w=>v.includes(w));
export function budgetClassification(r,service){
 const fields=['dbiz_nm','dbizNm','project_name','projectName','사업명','세부사업명',...(service==='AIDFA'?['biz_nm','bizNm','fld_nm','part_nm','sect_nm','acnt_dv_nm']:[])],v=fields.map(k=>text(r,k)).join(' | ').toLowerCase();
 const code=text(r,'dtilPrdctClsfcNo','detailPrdctClsfcNo','detailItemNo','dtlPrdctClsfcNo').replace(/\D/g,'');
 if(['3911152601','3911152602','3911152607'].includes(code))return 'POLE';
 if(['3911151502','3911160302','3911160304','3911160501','3911160802','3911161102','3911210201'].includes(code))return 'LIGHTING';
 if(['2611160701','3912110101'].includes(code))return 'SOLAR';
 if(has(v,['가로등주','보안등주','조명등주','도로조명등주','스테인리스등주','스텐등주']))return 'POLE';
 if(has(v,['led','조명','가로등','보안등','투광등','터널등','다운라이트','경관등','평판등','실내등','보행등','등기구']))return 'LIGHTING';
 if(has(v,['태양광','태양전지','photovoltaic','solar panel','pv module']))return 'SOLAR';
 if(has(v,['전기설계','전기 설계','전기감리','전기 감리','전기공사','전기 공사','전기시설공사','전기 시설공사','전기설비','전기 설비','전기시설','전기 시설']))return 'ELECTRICAL';return 'OTHER';
}
function amount(r,...fields){const selected=fields.find(k=>r[k]!==undefined&&r[k]!==null&&String(r[k]).trim()!=='');if(selected&&typeof r[selected]==='number'&&(!Number.isFinite(r[selected])||Math.abs(r[selected])>Number.MAX_SAFE_INTEGER))return {value:null,invalid:true};const v=text(r,...fields);if(!v)return {value:null,invalid:false};try{const value=roundedProduct(v);return {value,invalid:value===null};}catch{return {value:null,invalid:true};}}
export async function normalizeBudget(r,s){
 if(!object(r)||!validDay(s.snapshot_date))throw Error('LOFIN_SCHEMA');const source_key=budgetIdentity(r,s),detail=s.service==='QWGJK';if(new TextEncoder().encode(source_key).length>8192)throw Error('LOFIN_IDENTITY_TOO_LARGE');
 const b=detail?amount(r,'bdg_cash_amt','budget_amount','예산현액'):amount(r,'biz_bdg_tott_amt','biz_bdg_prsm_amt');
 const a=detail?amount(r,'cpl_amt','compile_amt','편성액'):b,e=detail?amount(r,'ep_amt','executed_amount','지출액','집행액'):{value:null,invalid:false};
 const basis=b.invalid?null:(b.value??a.value),remaining=detail&&basis!==null&&e.value!==null?(BigInt(basis)-BigInt(e.value)).toString():null;
 const semantic={...r};if(detail)delete semantic.exe_ymd;const hash=await digest(stableJson(semantic));
 return {source_key,payload_hash:hash,layer:detail?'DETAIL_EXECUTION':'APPROPRIATION',fiscal_year:Number(s.fiscal_year),snapshot_date:s.snapshot_date,region:text(r,'wa_laf_hg_nm'),region_code:text(r,'wa_laf_cd'),org:text(r,'laf_hg_nm','wa_laf_hg_nm'),org_code:text(r,'laf_cd','wa_laf_cd'),dept:detail?text(r,'dept_nm','dpt_nm','deptName','departmentName'):'',project:detail?text(r,'dbiz_nm','dbiz_cd'):[text(r,'fld_nm'),text(r,'sect_nm','part_nm')].filter(Boolean).join(' > '),project_code:detail?text(r,'dbiz_cd'):'',account:text(r,'acnt_dv_nm'),category:budgetClassification(r,s.service),budget_amount:b.value,appropriation_amount:a.value,executed_amount:e.value,remaining_amount:remaining,invalid_amount:b.invalid||a.invalid||e.invalid?1:0,raw:stableJson(r)};
}
export function budgetFixture(service,year,day,page,size,total=2407){const rows=[];for(let n=(page-1)*size;n<Math.min(page*size,total);n++){const q={fyr:String(year),wa_laf_cd:String(10+n%17),wa_laf_hg_nm:['서울특별시','경기도','부산광역시'][n%3],laf_cd:'SYN-'+n%41,laf_hg_nm:'합성기관 '+n%41,dept_cd:'D'+n%7,dept_nm:'합성부서 '+n%7,dbiz_cd:'SYN-B-'+n,dbiz_nm:['합성 가로등 교체사업','합성 가로등주 설치','합성 도서관 운영','합성 태양광 발전'][n%4],exe_ymd:day.replaceAll('-',''),bdg_cash_amt:String(1000000+n*100),cpl_amt:'900000',ep_amt:String(n*10),fld_cd:'F'+n,fld_nm:'합성 조명 분야 '+n,sect_cd:'S'+n,sect_nm:'합성 부문 '+n,acnt_dv_cd:'01',acnt_dv_nm:'일반회계',biz_bdg_tott_amt:String(1000000+n*100)};rows.push(q);}return {[service]:[{head:[{list_total_count:total},{RESULT:{CODE:'INFO-000'}}]},{row:rows}]};}
