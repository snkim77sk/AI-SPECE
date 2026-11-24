import {validDay} from './collector.mjs';

/** Existing receipts only: no cache writes, migrations or provider requests. */
export const SYNC_SOURCE_SUMMARY_SQL=`WITH dataset_totals AS (
 SELECT dataset,COUNT(*) pages,SUM(source_rows) source_rows,SUM(matched_rows) matched_rows,
  MAX(source_updated_at) source_updated_at,MAX(received_at) received_at,MAX(scope) latest_scope,
  MAX(CASE WHEN matched_rows>0 THEN scope END) latest_nonempty_scope
 FROM server_sync_pages GROUP BY dataset
), latest_page AS (
 SELECT p.dataset,p.scope,p.generation,p.source_status,p.source_saved_count,p.source_total
 FROM dataset_totals t
 JOIN server_sync_pages p ON p.id=(
  SELECT r.id FROM server_sync_pages r WHERE r.dataset=t.dataset AND r.scope=t.latest_scope
  ORDER BY r.source_updated_at DESC,r.page_no DESC,r.received_at DESC,r.id DESC LIMIT 1
 )
), latest_intake AS (
 SELECT l.dataset,COUNT(*) latest_scope_received_pages,SUM(r.source_rows) latest_scope_received_rows,
  MAX(r.received_at) latest_scope_received_at
 FROM latest_page l JOIN server_sync_pages r
  ON r.dataset=l.dataset AND r.scope=l.scope AND r.generation=l.generation
 GROUP BY l.dataset
)
SELECT t.*,l.source_status latest_source_status,l.source_saved_count latest_saved_count,l.source_total latest_source_total,
 i.latest_scope_received_pages,i.latest_scope_received_rows,i.latest_scope_received_at
FROM dataset_totals t JOIN latest_page l USING(dataset) JOIN latest_intake i USING(dataset)`;

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
  const received=checkedDay?count(source?.latest_scope_received_rows):null;
  const receivedAt=received!==null&&typeof source?.latest_scope_received_at==='string'&&Number.isFinite(Date.parse(source.latest_scope_received_at))?source.latest_scope_received_at:null;
  const pending=saved!==null&&received!==null&&saved>=received?saved-received:null;
  const partial=!!source&&(['RUNNING','INCOMPLETE','FAILED'].includes(source.latest_source_status)||saved!==null&&total!==null&&saved<total);
  let href=null;
  if(dataDay){
   const params=new URLSearchParams({mode:'live',from:'',to:dataDay,region:'',category:''});
   if(dataset==='budget'){params.set('service','QWGJK');params.set('year',dataDay.slice(0,4));}
   href=(dataset==='shopping_delivery'?'/shopping':'/budget')+'?'+params;
  }
  return {dataset,name:dataset==='shopping_delivery'?'계약·납품':'상세 예산',dataDay,checkedDay,saved,total,received,receivedAt,pending,partial,emptyAfterData:!!dataDay&&!!checkedDay&&checkedDay>dataDay,href};
 });
}
