import {validDay,kstDate} from './collector.mjs';
export const BOOTSTRAP='2026-01-01';export const RETENTION_MONTHS=27;export const FAST_PAGE_SIZE=999;export const FAST_MAX_BYTES=8*1024*1024;
export const LIGHTING_CODES=new Set(['3910161601','3911151501','3911151502','3911160301','3911160302','3911160304','3911160501','3911160801','3911160802','3911161101','3911161102','3911210201','3911210301','3911980201','3911980202','3911980301','3911980302','3911980303','3911980304']);
export const SMART_FIXTURE_CODES=new Set(['3911980201','3911980202','3911980301','3911980302','3911980303','3911980304']);
export const SMART_CONTROL_CODE='3911980401';
export const POLE_CODES=new Set(['3911152601','3911152602','3911152607']);
export function pick(r,...names){for(const n of names){if(r[n]!==undefined&&r[n]!==null&&String(r[n]).trim()!=='')return String(r[n]).trim().slice(0,1024);}return '';}
export function scopeWindow(today=kstDate()){
 const d=new Date(today+'T00:00:00Z'),month=d.getUTCMonth()-RETENTION_MONTHS;
 const first=new Date(Date.UTC(d.getUTCFullYear(),month,1));const last=new Date(Date.UTC(first.getUTCFullYear(),first.getUTCMonth()+1,0)).getUTCDate();
 first.setUTCDate(Math.min(d.getUTCDate(),last));const cutoff=first.toISOString().slice(0,10);const upper=new Date(d.getTime()-86400000).toISOString().slice(0,10);
 return {from:cutoff>BOOTSTRAP?cutoff:BOOTSTRAP,to:upper,retentionCutoff:cutoff};
}
export function dayOrder(from,to){
 if(!validDay(from)||!validDay(to)||from>to)throw Error('INVALID_DATE_RANGE');
 const a=new Date(from+'T00:00:00Z').getTime(),b=new Date(to+'T00:00:00Z').getTime();
 const count=(b-a)/86400000+1;if(count>850)throw Error('DATE_RANGE_TOO_LARGE');
 const out=[];for(let n=0;n<Math.min(3,count);n++)out.push(new Date(b-n*86400000).toISOString().slice(0,10));
 for(let n=0;n<count-3;n++)out.push(new Date(a+n*86400000).toISOString().slice(0,10));return out;
}
export function identity(r){const req=pick(r,'dlvrReqNo','deliveryReqNo','reqNo'),seq=pick(r,'prdctSno','dlvrReqDtlSeq','dlvrReqDtlSn','detailSeq','seq');if(!req||!seq)throw Error('SOURCE_ID_MISSING');let chg=pick(r,'dlvrReqChgOrd','deliveryReqChangeOrder','dlvrReqChangeOrd')||'0';if(/^\d+$/.test(chg))chg=BigInt(chg).toString();return [req,chg,seq];}
export function decimal(v){const s=String(v??'').replaceAll(',','').replaceAll('원','').trim();return /^-?\d+(\.\d+)?$/.test(s)?s:null;}
export function roundedProduct(a,b='1'){
 const x=decimal(a),y=decimal(b);if(x===null||y===null)return null;
 function parts(s){const sign=s.startsWith('-')?-1n:1n;const [i,f='']=s.replace('-','').split('.');return [sign*BigInt(i+f),10n**BigInt(f.length)];}
 const [xn,xd]=parts(x),[yn,yd]=parts(y),n=xn*yn,den=xd*yd,sign=n<0n?-1n:1n;return (sign*((n*sign+den/2n)/den)).toString();
}

export function canonicalRegion(value){const t=String(value||'').trim().replace(/\s+/g,' ');const aliases={'강원도':'강원특별자치도','전라북도':'전북특별자치도','전북':'전북특별자치도','전남광주':'전남광주통합특별시'};for(const [a,c] of Object.entries(aliases))if(t.startsWith(a))return c;for(const r of ['서울특별시','부산광역시','대구광역시','인천광역시','광주광역시','대전광역시','울산광역시','세종특별자치시','경기도','강원특별자치도','충청북도','충청남도','전북특별자치도','전라남도','경상북도','경상남도','제주특별자치도','전남광주통합특별시'])if(t.startsWith(r))return r;const short={'서울':'서울특별시','부산':'부산광역시','대구':'대구광역시','인천':'인천광역시','광주':'광주광역시','대전':'대전광역시','울산':'울산광역시','세종':'세종특별자치시','경기':'경기도','강원':'강원특별자치도','충북':'충청북도','충남':'충청남도','전남':'전라남도','경북':'경상북도','경남':'경상남도','제주':'제주특별자치도'};for(const [a,c] of Object.entries(short))if(t===a||t.startsWith(a+' '))return c;return '';}
export function normalizeDelivery(r,fallbackDay,window=scopeWindow(),options={includeSmart:false}){
 const code=pick(r,'dtilPrdctClsfcNo','detailPrdctClsfcNo','detailItemNo','dtlPrdctClsfcNo','prdctClsfcNo').replace(/\D/g,'');
 const category=LIGHTING_CODES.has(code)?'LIGHTING':POLE_CODES.has(code)?'POLE':options.includeSmart&&code===SMART_CONTROL_CODE?'SMART_CONTROL':null;
 if(!category)return {excluded:'CATEGORY_NOT_ALLOWED'};
 const dateText=pick(r,'dlvrReqRcptDate','IntlCntrctDlvrReqDate','cntrctDlvrReqDate','deliveryReqDate').replace(/\D/g,'');
 const candidate=dateText.length>=8?`${dateText.slice(0,4)}-${dateText.slice(4,6)}-${dateText.slice(6,8)}`:fallbackDay;
 if(!validDay(candidate))throw Error('SOURCE_DATE_INVALID');
 if(candidate<window.from||candidate>window.to||(category==='SMART_CONTROL'&&candidate<'2026-10-01'))return {excluded:'OUTSIDE_DATE_SCOPE'};
 for(const k of ['prdctQty','dlvrReqQty','reqQty','quantity','qty','prdctUprc','unitPric','unitPrice','cntrctUnitPric','cntrctPrce','prc','prdctAmt','supplyAmount','amount','dlvrReqDtlAmt','dlvrReqAmt','reqAmt'])if(typeof r[k]==='number'&&(!Number.isFinite(r[k])||Math.abs(r[k])>Number.MAX_SAFE_INTEGER))throw Error('SOURCE_NUMBER_OUT_OF_RANGE');
 const [request_no,change_order,sequence]=identity(r);const quantity=decimal(pick(r,'prdctQty','dlvrReqQty','reqQty','quantity','qty'));
 const unit_price=decimal(pick(r,'prdctUprc','unitPric','unitPrice','cntrctUnitPric','cntrctPrce','prc'));
 const sourceAmount=pick(r,'prdctAmt','supplyAmount','amount','dlvrReqDtlAmt');
 const amount=sourceAmount!==''?roundedProduct(sourceAmount):roundedProduct(unit_price,quantity);
 return {row:{source_key:JSON.stringify([request_no,change_order,sequence]),source_date:candidate,date_basis:dateText.length>=8?'source':'query_date',category,is_active:1,inactive_reason:'',change_rank:(change_order.replace(/\D/g,'').replace(/^0+/,'')||'0'),vendor_bizno:pick(r,'cntrctCorpBizno','corpBizno','vendorBizno','bizno','bizrno').replace(/\D/g,''),contract_no:pick(r,'cntrctNo','contractNo'),detail_code:code,detail_name:pick(r,'dtilPrdctClsfcNoNm','dtilPrdctClsfcNm','detailPrdctNm','detailItemName'),request_no,change_order,sequence,request_name:pick(r,'dlvrReqNm','deliveryReqNm','dlvrReqSj'),final_flag:pick(r,'fnlDlvrReqYn','finalDeliveryReqYn'),item_id:pick(r,'prdctIdntNo','itemId','productId'),item_name:pick(r,'prdctIdntNoNm','prdctIdntNm','prdctNm','itemName'),model:pick(r,'modelNm','modelName','prdctSpecNm','specNm'),agency:pick(r,'dminsttNm','demandInsttNm','demandOrgNm','demandOrgName','orderInsttNm','insttNm'),agency_code:pick(r,'dminsttCd','demandInsttCd'),region:canonicalRegion(pick(r,'dminsttRgnNm','demandRegion','demandRegionName','regionName','areaNm','sidoNm'))||canonicalRegion(pick(r,'dminsttNm','demandInsttNm','demandOrgNm','demandOrgName','orderInsttNm','insttNm')),vendor:pick(r,'corpNm','cntrctCorpNm','entrpsNm','vendorNm','vendorName','supplierNm','supplierName','cntrctCorpName'),quantity,unit_price,amount,request_total:roundedProduct(pick(r,'dlvrReqAmt','reqAmt')),raw:JSON.stringify(r)}};
}
export function mixedFixture(day,page,size=FAST_PAGE_SIZE,total=2105){
 const start=(page-1)*size;const regions=['서울특별시','부산광역시','인천광역시','경기도','강원특별자치도','충청남도','전북특별자치도','경상남도','제주특별자치도'];
 const items=Array.from({length:Math.max(0,Math.min(size,total-start))},(_,i)=>{const n=start+i+1,k=n%5;const code=k===0?'8010160401':k===1?'3911152601':'3911160301';return {dlvrReqNo:`SYNTHETIC-${day}-${String(n).padStart(6,'0')}`,dlvrReqChgOrd:'000',prdctSno:'1',dlvrReqRcptDate:day,dtilPrdctClsfcNo:code,dtilPrdctClsfcNoNm:k===0?'LED 조명 설계 용역(제외 시험)':k===1?'조명용 등주':'LED 가로등기구',prdctIdntNo:'S-'+n,prdctNm:k===0?'합성 용역':k===1?'합성 등주':'합성 LED 기구',modelNm:`TEST-${n%12+1}`,dminsttNm:`${regions[n%9]} 합성 기관 ${n%17+1}`,dminsttCd:'SYN-'+n%9+'-'+n%17,dminsttRgnNm:regions[n%9],corpNm:`합성 공급업체 ${n%5+1}`,prdctQty:String(n%20+1),prdctUprc:'123400',prdctAmt:String((n%20+1)*123400),dlvrReqAmt:'987654321',fnlDlvrReqYn:'Y',isSynthetic:true};});
 return {response:{header:{resultCode:'00'},body:{pageNo:page,numOfRows:size,totalCount:total,items}}};
}
export function csvCell(v){const s=String(v??'');return '"'+(/^[=+\-@\t\r]/.test(s)?"'"+s:s).replaceAll('"','""')+'"';}
