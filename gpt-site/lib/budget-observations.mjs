import {rowChunks} from './g2b-sql.mjs';

/**
 * Persist only dates actually observed by new QWGJK page commits. A revision is
 * one deduplicated payload, not evidence that it occurred on every intervening
 * day. Multiple payloads on one date are retained without claiming their order.
 *
 * Run these statements after revision inserts and before the receipt/checkpoint
 * in the same atomic batch. The caller supplies its unchanged lease/page fence.
 * Compact key-only JSON chunks avoid sending or storing another raw payload.
 * Completion evidence remains in budget_sessions, separate from observations.
 */
export function budgetObservationPlan(session, rows, stamp, guard, fence) {
  if (session.service !== 'QWGJK') return [];
  return rowChunks(rows.map(({source_key, payload_hash}) => ({source_key, payload_hash}))).map(chunk => ({
    sql: `INSERT INTO budget_observations(mode,service,source_key,snapshot_date,payload_hash,fiscal_year,first_seen,last_seen,last_session)
      SELECT ?,?,json_extract(value,'$.source_key'),?,json_extract(value,'$.payload_hash'),?,?,?,?
      FROM json_each(?) WHERE ${guard}
      ON CONFLICT(mode,service,source_key,snapshot_date,payload_hash) DO UPDATE SET
        first_seen=MIN(budget_observations.first_seen,excluded.first_seen),
        last_seen=MAX(budget_observations.last_seen,excluded.last_seen),
        last_session=CASE WHEN excluded.last_seen>=budget_observations.last_seen THEN excluded.last_session ELSE budget_observations.last_session END
      WHERE excluded.first_seen<budget_observations.first_seen OR excluded.last_seen>budget_observations.last_seen
        OR (excluded.last_seen=budget_observations.last_seen AND excluded.last_session<>budget_observations.last_session)`,
    params: [session.mode, session.service, session.snapshot_date, session.fiscal_year, stamp, stamp, session.id, chunk, ...fence],
  }));
}
