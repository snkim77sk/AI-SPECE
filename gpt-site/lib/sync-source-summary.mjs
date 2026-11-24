import {validDay} from './collector.mjs';

/** Existing receipts only: no cache writes, migrations or provider requests. */
export const SYNC_SOURCE_SUMMARY_SQL=`SELECT dataset,COUNT(*) pages,SUM(source_rows) source_rows,
 SUM(matched_rows) matched_rows,MAX(source_updated_at) source_updated_at,MAX(received_at) received_at,
 MAX(scope) latest_scope,MAX(CASE WHEN matched_rows>0 THEN scope END) latest_nonempty_scope,
 (SELECT source_status FROM server_sync_pages p WHERE p.dataset=s.dataset ORDER BY scope DESC,source_updated_at DESC,page_no DESC LIMIT 1) latest_source_status,
 (SELECT source_saved_count FROM server_sync_pages p WHERE p.dataset=s.dataset ORDER BY scope DESC,source_updated_at DESC,page_no DESC LIMIT 1) latest_saved_count,
 (SELECT source_total FROM server_sync_pages p WHERE p.dataset=s.dataset ORDER BY scope DESC,source_updated_at DESC,page_no DESC LIMIT 1) latest_source_total
 FROM server_sync_pages s GROUP BY dataset`;

const day=(scope,dataset)=>{
 const value=typeof scope==='string'?(dataset==='budget'?scope.split(':')[1]:scope):null;
 return value&&validDay(value)?value:null;
};
const count=value=>Number.isSafeInteger(value)&&value>=0?value:null;
export function syncAvailability(sync){
 return ['shopping_delivery','budget'].map(dataset=>{
  const source=Array.isArray(sync?.sources)?sync.sources.find(s=>s.dataset===dataset):null;
  const dataDay=day(source?.latest_nonempty_scope,dataset),checkedDay=day(source?.latest_scope,dataset);
  const saved=count(source?.latest_saved_count),total=count(source?.latest_source_total);
  const partial=!!source&&(['RUNNING','INCOMPLETE','FAILED'].includes(source.latest_source_status)||saved!==null&&total!==null&&saved<total);
  let href=null;
  if(dataDay){
   const params=new URLSearchParams({mode:'live',from:'',to:dataDay,region:'',category:''});
   if(dataset==='budget'){params.set('service','QWGJK');params.set('year',dataDay.slice(0,4));}
   href=(dataset==='shopping_delivery'?'/shopping':'/budget')+'?'+params;
  }
  return {dataset,name:dataset==='shopping_delivery'?'계약·납품':'상세 예산',dataDay,checkedDay,saved,total,partial,emptyAfterData:!!dataDay&&!!checkedDay&&checkedDay>dataDay,href};
 });
}
