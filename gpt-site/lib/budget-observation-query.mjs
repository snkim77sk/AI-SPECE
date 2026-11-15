import {
  budgetHistoryPlan, parseHistoryCursor, historyCursor, budgetHistoryEnrich,
  HISTORY_PAGE_SIZE, HISTORY_MAX_ROWS, HISTORY_PAGE_BYTES,
  HISTORY_BASELINE_CHUNK, HISTORY_BASELINE_BYTES,
} from './budget-history-query.mjs';
import {boundedJsonQuery, unpackBounded} from './matching-query.mjs';

export const OBSERVATION_PAGE_SIZE = HISTORY_PAGE_SIZE;
export const OBSERVATION_MAX_ROWS = HISTORY_MAX_ROWS;
export const OBSERVATION_PAGE_BYTES = HISTORY_PAGE_BYTES;
export const OBSERVATION_BASELINE_CHUNK = HISTORY_BASELINE_CHUNK;
export const OBSERVATION_BASELINE_BYTES = HISTORY_BASELINE_BYTES;
export const OBSERVATION_ORDER = 'snapshot_date DESC,source_key ASC,payload_hash ASC';
const join = 'r.mode=o.mode AND r.service=o.service AND r.source_key=o.source_key AND r.payload_hash=o.payload_hash';
const j = key => `json_extract(r.row_json,'$.${key}')`;

function observationError(error) {
  return error instanceof Error ? Error(error.message.replaceAll('HISTORY', 'OBSERVATION')) : error;
}

/** Same validated selectors as stored history/current lists, but dates and year
 * come from actual observation records, not a unique state's first_snapshot. */
export function budgetObservationPlan(input, today) {
  try {
    const plan = budgetHistoryPlan(input, today);
    plan.where = plan.where.map(value => value === 'first_snapshot>=?' ? 'snapshot_date>=?' : value === 'first_snapshot<=?' ? 'snapshot_date<=?' : value);
    plan.signature = JSON.stringify(['DAILY_OBSERVATIONS_V1', plan.where, plan.args]);
    return plan;
  } catch (error) { throw observationError(error); }
}

export function parseObservationCursor(token, scope, revision) {
  try { return parseHistoryCursor(token, scope, revision); } catch (error) { throw observationError(error); }
}
export function observationCursor(row, scope, revision) {
  return historyCursor({...row, first_snapshot: row.snapshot_date}, scope, revision);
}

/** CROSS JOIN fixes observation-first loop order: bounded indexed observation
 * selection with one PK revision lookup, never a global revision join/sort.
 * Window framing materializes no more than limit+1 selected rows. */
export function budgetObservationPage(plan, cursor = null) {
  const where = [...plan.where], args = [...plan.args];
  if (cursor) {
    where.push('snapshot_date<=?', '(snapshot_date<? OR (snapshot_date=? AND (source_key,payload_hash)>(?,?)))');
    args.push(cursor.date, cursor.date, cursor.date, cursor.key, cursor.hash);
  }
  // Project-detail links have an exact key. Seek that key before its date
  // range instead of scanning every project in the selected year/date window.
  const index = plan.where.includes('source_key=?') ? 'idx_budget_observation_baseline' : 'idx_budget_observation_page';
  const prefix = `WITH observation_scope AS (
    SELECT o.mode,o.service,o.source_key,o.payload_hash,o.fiscal_year,o.snapshot_date,
      o.first_seen observation_first_seen,o.last_seen observation_last_seen,
      o.last_session observation_last_session,r.row_json,
      ${['category', 'region', 'org', 'dept', 'project'].map(k => `${j(k)} AS ${k}`).join(',')}
    FROM budget_observations o INDEXED BY ${index}
    CROSS JOIN budget_revisions r ON ${join}
  )`;
  const detail = `json_set(json_remove(row_json,'$.raw','$.first_snapshot','$.first_seen'),
    '$.source_key',source_key,'$.payload_hash',payload_hash,'$.fiscal_year',fiscal_year,
    '$.snapshot_date',snapshot_date,'$.observation_first_seen',observation_first_seen,
    '$.observation_last_seen',observation_last_seen,'$.observation_last_session',observation_last_session)`;
  const select = `SELECT source_key,payload_hash,snapshot_date,${detail} row_json FROM observation_scope WHERE ${where.join(' AND ')}`;
  return {sql: boundedJsonQuery(prefix, select, OBSERVATION_ORDER, plan.limit, OBSERVATION_PAGE_BYTES), args};
}

export function unpackObservationPage(data, limit) {
  try { return unpackBounded(data, limit); } catch { throw Error('BUDGET_OBSERVATION_RECORD_TOO_LARGE'); }
}

/** At most two earlier observations per displayed row. Repeated hashes on
 * different days remain separate observations. The second row detects an
 * ambiguous previous day; it never establishes within-day temporal order. */
export function budgetObservationBaselines(plan, rows) {
  if (!rows.length || rows.length > OBSERVATION_BASELINE_CHUNK) throw Error('INVALID_OBSERVATION_BASELINE_CHUNK');
  const targets = JSON.stringify(rows.map(row => ({key: row.source_key, date: row.snapshot_date, hash: row.payload_hash})));
  const amounts = `json_object(${['budget_amount', 'appropriation_amount', 'executed_amount', 'remaining_amount', 'invalid_amount'].flatMap(k => [`'${k}'`, j(k)]).join(',')})`;
  // first_snapshot is only an internal compatibility key consumed by the pure
  // history arithmetic helper. Public results rename it previous_snapshot_date.
  return {sql: `WITH targets AS (
    SELECT CAST(key AS INTEGER) ordinal,json_extract(value,'$.key') source_key,
      json_extract(value,'$.date') snapshot_date,json_extract(value,'$.hash') payload_hash FROM json_each(?)
  ), evidence AS MATERIALIZED (
    SELECT t.ordinal,
      EXISTS(SELECT 1 FROM budget_observations o INDEXED BY idx_budget_observation_baseline
        WHERE o.mode=? AND o.service='QWGJK' AND o.source_key=t.source_key
          AND o.snapshot_date=t.snapshot_date AND o.payload_hash<>t.payload_hash AND o.fiscal_year=?) same_date_ambiguous,
      (SELECT json_group_array(json(state_json)) FROM (
        SELECT json_object('first_snapshot',o.snapshot_date,'payload_hash',o.payload_hash,'amounts',${amounts}) state_json
        FROM budget_observations o INDEXED BY idx_budget_observation_baseline
        CROSS JOIN budget_revisions r ON ${join}
        WHERE o.mode=? AND o.service='QWGJK' AND o.source_key=t.source_key
          AND o.snapshot_date<t.snapshot_date AND o.fiscal_year=?
        ORDER BY o.snapshot_date DESC,o.payload_hash ASC LIMIT 2
      )) previous_json
    FROM targets t
  ) SELECT ordinal,same_date_ambiguous,
    CASE WHEN length(CAST(previous_json AS BLOB))<=${OBSERVATION_BASELINE_BYTES} THEN previous_json ELSE NULL END previous_json
    FROM evidence ORDER BY ordinal`, args: [targets, plan.mode, plan.year, plan.mode, plan.year]};
}

export function budgetObservationEnrich(row, evidence, from) {
  try {
    const enriched = budgetHistoryEnrich(row, evidence, from);
    const {previous_first_snapshot, ...result} = enriched;
    return {...result, previous_snapshot_date: previous_first_snapshot,
      delta_status: result.delta_status === 'NO_PREVIOUS_STATE' ? 'NO_PREVIOUS_OBSERVATION' : result.delta_status};
  } catch (error) { throw observationError(error); }
}
