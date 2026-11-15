export const budgetLane=(service,historyOnly=false)=>service==='QWGJK'&&historyOnly?'QWGJK_HISTORY':service;
export const BUDGET_CREATE=`INSERT INTO budget_sessions(id,mode,service,fiscal_year,snapshot_date,history_only,state,page,page_size,source_total,fetched,saw_full,fixture_total,max_payload,lease_until,created_at,updated_at)
 SELECT ?,?,?,?,?,?,'ready',1,?,NULL,0,0,?,0,0,?,?
 WHERE NOT EXISTS(SELECT 1 FROM budget_sessions WHERE mode=? AND service=? AND fiscal_year=? AND history_only=? AND state NOT IN ('complete','cancelled'))
 AND (?=0 OR NOT EXISTS(SELECT 1 FROM budget_controls WHERE mode=? AND lane=? AND state IN ('paused','cancelled')))`;
export const BUDGET_CREATE_CONTROL=`INSERT INTO budget_controls(mode,lane,state,updated_at) SELECT ?,?,'ready',? WHERE EXISTS(SELECT 1 FROM budget_sessions WHERE id=?) ON CONFLICT(mode,lane) DO UPDATE SET state='ready',updated_at=excluded.updated_at`;
export const BUDGET_STATE_CONTROL=`INSERT INTO budget_controls(mode,lane,state,updated_at) SELECT mode,CASE WHEN service='QWGJK' AND history_only=1 THEN 'QWGJK_HISTORY' ELSE service END,state,updated_at FROM budget_sessions WHERE id=? AND updated_at=? AND state=? ON CONFLICT(mode,lane) DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at`;
