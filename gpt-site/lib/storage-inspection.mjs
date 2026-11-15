/** Fixed SQL catalog. No identifier from a request is interpolated into SQL.
 * Aggregate scalar metadata inside D1; never return/copy stored source payloads. */
export const STORAGE_TABLES=[
 {id:'deliveries',label:'납품 원문',from:'delivery_rows r',mode:'r.mode',payload:'r.raw',day:'r.source_date',active:'r.is_active=1',basis:'ORIGINAL_DELIVERY_JSON'},
 {id:'budgets',label:'현재 예산 원문',from:'budget_rows r',mode:'r.mode',payload:'r.row_json',day:'r.snapshot_date',active:'r.is_active=1',basis:'NORMALIZED_BUDGET_JSON_INCLUDING_RAW'},
 {id:'revisions',label:'예산 고유 상태 이력',from:'budget_revisions r',mode:'r.mode',payload:'r.row_json',day:'r.first_snapshot',basis:'UNIQUE_BUDGET_JSON_INCLUDING_RAW'},
 {id:'observations',label:'날짜별 관측 참조',from:'budget_observations r',mode:'r.mode',payload:"r.service||r.source_key||r.snapshot_date||r.payload_hash||r.first_seen||r.last_seen||r.last_session",day:'r.snapshot_date',basis:'SELECTED_REFERENCE_TEXT_NOT_JSON'},
 {id:'match_budgets',label:'매칭 예산 사본',from:'matching_budgets r JOIN matching_jobs j ON j.id=r.job_id',mode:'j.mode',payload:'r.row_json',basis:'FROZEN_MATCHING_BUDGET_JSON'},
 {id:'match_requests',label:'매칭 요구건 사본',from:'matching_requests r JOIN matching_jobs j ON j.id=r.job_id',mode:'j.mode',payload:'r.row_json',basis:'FROZEN_MATCHING_REQUEST_JSON'},
 {id:'match_results',label:'매칭 결과 근거',from:'matching_results r JOIN matching_jobs j ON j.id=r.job_id',mode:'j.mode',payload:'r.row_json',basis:'FROZEN_MATCHING_EVIDENCE_JSON'},
 {id:'institution_events',label:'기관 분석 중간 색인',from:'institution_pattern_events r JOIN institution_pattern_runs j ON j.id=r.run_id',mode:'j.mode',payload:'r.row_json',basis:'INSTITUTION_EVENT_JSON'},
 {id:'institution_years',label:'기관별 연도 근거',from:'institution_pattern_years r JOIN institution_pattern_runs j ON j.id=r.run_id',mode:'j.mode',payload:'r.row_json',basis:'INSTITUTION_YEAR_JSON'},
 {id:'institution_aliases',label:'기관 연결 별칭',from:'institution_pattern_aliases r JOIN institution_pattern_runs j ON j.id=r.run_id',mode:'j.mode',payload:'r.region_key||r.category||r.alias||r.org_key',basis:'SELECTED_ALIAS_TEXT_NOT_JSON'},
 {id:'future_rank_rows',label:'미래 검토 정렬 근거',from:'future_rank_rows r JOIN future_rank_runs j ON j.id=r.run_id',mode:'j.mode',payload:'r.row_json',basis:'CAPPED_FUTURE_RANK_EVIDENCE_JSON'},
 {id:'future_rank_keys',label:'미래 검토 정렬키',from:'future_rank_rows r JOIN future_rank_runs j ON j.id=r.run_id',mode:'j.mode',payload:'r.rank_key',basis:'EXACT_FUTURE_RANK_KEY_TEXT_NOT_INDEX_BYTES'},
];
export function storageInspectionQuery(mode,id,cutoff){
 if(!['live','synthetic'].includes(mode))throw Error('INVALID_MODE');const spec=STORAGE_TABLES.find(s=>s.id===id);if(!spec)throw Error('INVALID_STORAGE_TABLE');if(typeof cutoff!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(cutoff))throw Error('INVALID_STORAGE_CUTOFF');
 // Dates were normalized at ingestion. Unknown/malformed dates are held and
 // reported separately; age alone never confers permission or eligibility.
 const known=spec.day?`length(${spec.day})=10 AND ${spec.day} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' AND date(${spec.day})=${spec.day}`:'0',old=spec.day?`(${known}) AND ${spec.day}<?`:'0',active=spec.active||'0',bytes=`length(CAST(COALESCE(${spec.payload},'') AS BLOB))`;
 return{spec,args:[...(spec.day?[cutoff]:[]),mode],sql:`SELECT CAST(COUNT(*) AS TEXT) rows,CAST(COALESCE(SUM(${bytes}),0) AS TEXT) selected_payload_bytes,CAST(COALESCE(MAX(${bytes}),0) AS TEXT) largest_selected_payload_bytes,CAST(COALESCE(SUM(CASE WHEN ${old} THEN 1 ELSE 0 END),0) AS TEXT) before_window_rows,CAST(COALESCE(SUM(CASE WHEN ${known} THEN 0 ELSE 1 END),0) AS TEXT) no_comparable_date_rows,CAST(COALESCE(SUM(CASE WHEN ${active} THEN 1 ELSE 0 END),0) AS TEXT) active_rows FROM ${spec.from} WHERE ${spec.mode}=?`};
}
