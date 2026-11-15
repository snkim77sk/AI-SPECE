import{patternRegionWhere}from'./institution-lineage.mjs';
import{boundedJsonQuery,unpackBounded}from'./matching-query.mjs';
import{INSTITUTION_PAGE,INSTITUTION_BYTES}from'./institution-storage.mjs';
export const EVENT_CURSOR_FIELDS=['region_key','org_key','category','fiscal_year','budget_key','kind_order','source_event_key'];
export const GROUP_CURSOR_FIELDS=['region_key','org_key','category','fiscal_year'];
export function eventCursor(event){return EVENT_CURSOR_FIELDS.map(k=>event[k]);}
export function institutionEventQuery(runId,cursor){
 const args=[runId],where=['run_id=?','eligible=1'];
 if(cursor){if(!Array.isArray(cursor)||cursor.length!==7)throw Error('INVALID_PATTERN_EVENT_CURSOR');where.push(`(${EVENT_CURSOR_FIELDS.join(',')})>(${EVENT_CURSOR_FIELDS.map(()=>'?').join(',')})`);args.push(...cursor);}
 const select=`SELECT source_event_key AS source_key,${EVENT_CURSOR_FIELDS.filter(k=>k!=='source_event_key').join(',')},source_event_key,json_object('kind',kind,'source_event_key',source_event_key,'region_key',region_key,'org_key',org_key,'category',category,'fiscal_year',fiscal_year,'budget_key',budget_key,'kind_order',kind_order,'row_json',row_json,'eligible',eligible) row_json FROM institution_pattern_events INDEXED BY idx_institution_event_page WHERE ${where.join(' AND ')}`;
 return{sql:boundedJsonQuery('',select,EVENT_CURSOR_FIELDS.join(','),INSTITUTION_PAGE,INSTITUTION_BYTES),args};
}
export function institutionFrame(select){return boundedJsonQuery('',select,'source_key',INSTITUTION_PAGE,INSTITUTION_BYTES);}
export function institutionUnpack(rows){try{return unpackBounded(rows,INSTITUTION_PAGE);}catch(e){if(e.message==='MATCH_INPUT_RECORD_TOO_LARGE')throw Error('PATTERN_INPUT_RECORD_TOO_LARGE');throw e;}}
const resultFilters=params=>['region','org','category','year'].map(k=>params.get(k)||'');
export function encodePatternCursor(run,row,params){return JSON.stringify({v:2,run:run.id,scope:run.scope_hash,filters:resultFilters(params),generation:run.generation,key:GROUP_CURSOR_FIELDS.map(k=>row[k])});}
export function institutionResultQuery(run,params){
 const limit=50,args=[run.id],where=['run_id=?'];
 for(const[k,column]of[['region','region_key'],['org','org_key'],['category','category'],['year','fiscal_year']])if(params.get(k)){const value=params.get(k);if(value.length>1024)throw Error('INVALID_PATTERN_FILTER');if(k==='year'&&(!/^\d{4}$/.test(value)||Number(value)<2026||Number(value)>2100))throw Error('INVALID_PATTERN_YEAR');if(k==='category'&&!['LIGHTING','POLE'].includes(value))throw Error('INVALID_PATTERN_CATEGORY');if(k==='region')patternRegionWhere(value,where,args);else{where.push(column+'=?');args.push(k==='year'?Number(value):value);}}
 const encoded=params.get('after');if(encoded){if(encoded.length>16384)throw Error('INVALID_PATTERN_CURSOR');let c;try{c=JSON.parse(encoded);}catch{throw Error('INVALID_PATTERN_CURSOR');}if(c.v!==2||c.run!==run.id||c.scope!==run.scope_hash||JSON.stringify(c.filters)!==JSON.stringify(resultFilters(params))||c.generation!==run.generation||!Array.isArray(c.key)||c.key.length!==4||c.key.some((v,i)=>i===3?!Number.isSafeInteger(v):typeof v!=='string'||v.length>1024))throw Error('INVALID_PATTERN_CURSOR');where.push(`(${GROUP_CURSOR_FIELDS.join(',')})>(?,?,?,?)`);args.push(...c.key);}
 const columns=GROUP_CURSOR_FIELDS.join(',');return{sql:`WITH picked AS(SELECT ${columns},row_json FROM institution_pattern_years WHERE ${where.join(' AND ')} ORDER BY ${columns} LIMIT ${limit+1}),framed AS(SELECT *,ROW_NUMBER() OVER(ORDER BY ${columns}) ordinal,COUNT(*) OVER() candidate_count,SUM(length(CAST(row_json AS BLOB))) OVER(ORDER BY ${columns}) prefix_bytes FROM picked) SELECT ${columns},candidate_count,ordinal,prefix_bytes,CASE WHEN prefix_bytes<=${INSTITUTION_BYTES} THEN row_json ELSE NULL END row_json FROM framed WHERE ordinal<=${limit} AND (prefix_bytes<=${INSTITUTION_BYTES} OR ordinal=1) ORDER BY ordinal`,args,limit};
}
