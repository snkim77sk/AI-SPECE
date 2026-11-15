export const DELIVERY_REGIONS=['서울특별시','부산광역시','대구광역시','인천광역시','광주광역시','대전광역시','울산광역시','세종특별자치시','경기도','강원특별자치도','충청북도','충청남도','전북특별자치도','전라남도','경상북도','경상남도','제주특별자치도'];
export function deliveryLocation(path,search,initialTab='data'){
 const p=new URLSearchParams(search),allowed=['data','smart','vendors','agencies','collect','scope'];
 const tab=path==='/smartled'?'smart':path==='/vendors'?(p.get('group')==='agency'?'agencies':'vendors'):path==='/settings'?(p.get('section')==='collection'?'collect':'scope'):path==='/shopping'?'data':allowed.includes(p.get('tab'))?(p.get('tab')||initialTab):initialTab;
 return {tab,mode:'live',category:p.has('category')?(['LIGHTING','POLE'].includes(p.get('category'))?(p.get('category')||''):''):path==='/shopping'?'LIGHTING':'',region:p.has('region')?(p.get('region')||''):['data','smart','vendors'].includes(tab)?'인천광역시':'',q:p.get('q')||'',from:p.get('from')||p.get('start_date')||'',to:p.get('to')||p.get('end_date')||'',view:p.get('view')==='history'?'history':'current',kind:tab==='smart'?(p.get('kind')==='CONTROL'?'CONTROL':'FIXTURE'):'',size:['50','100','250'].includes(p.get('size'))?(p.get('size')||'50'):'50',reviewLimit:p.get('reviewLimit')||p.get('limit')||'200'};
}

export function deliveryQueryUrl(href, params, tab, reviewLimit) {
 const url=new URL(href);
 for(const key of ['mode','q','category','region','view','size','from','to','start_date','end_date','dataset','kind','reviewLimit','limit'])url.searchParams.delete(key);
 for(const [key,value]of params)url.searchParams.set(key,value);
 if(tab==='vendors')url.searchParams.set('reviewLimit',reviewLimit);
 return url;
}
