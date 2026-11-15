import {budgetQuery} from './budget-query.mjs';
import {validDay} from './collector.mjs';
import {boundedJsonQuery, unpackBounded} from './matching-query.mjs';

export const HISTORY_PAGE_SIZE = 50;
export const HISTORY_MAX_ROWS = 100;
export const HISTORY_PAGE_BYTES = 1048576;
export const HISTORY_BASELINE_CHUNK = 10;
export const HISTORY_BASELINE_BYTES = 65536;
export const HISTORY_ORDER = 'first_snapshot DESC,source_key ASC,payload_hash ASC';
const amountFields = ['budget_amount', 'appropriation_amount', 'executed_amount', 'remaining_amount'];
const j = key => `json_extract(row_json,'$.${key}')`;

/** Revision predicates use each stored state's own attributes, never today's
 * active rows. Reuse the list's institution/code/text selectors without its
 * current-row flag, date order, or cursor. */
export function budgetHistoryPlan(input, today) {
  const p = new URLSearchParams(input);
  const allowed = new Set(['mode', 'service', 'year', 'from', 'to', 'limit', 'key', 'cursor', 'category', 'region', 'org', 'dept', 'orgKey', 'deptKey', 'q', 'preset_q', 'incheonScope', 'execution', 'view']);
  for (const key of p.keys()) if (!allowed.has(key)) throw Error('UNSUPPORTED_HISTORY_FILTER');
  for (const key of allowed) if (p.getAll(key).length > 1) throw Error('DUPLICATE_HISTORY_FILTER');
  if (p.has('view') && !['', 'all', 'targets'].includes(p.get('view'))) throw Error('UNSUPPORTED_HISTORY_FILTER');
  if (p.has('mode') && !['live', 'synthetic'].includes(p.get('mode'))) throw Error('INVALID_MODE');
  if (p.has('service') && p.get('service') !== 'QWGJK') throw Error('DETAIL_BUDGET_HISTORY_ONLY');
  p.set('service', 'QWGJK');
  p.delete('cursor');
  p.delete('sort');
  const currentFilters = new URLSearchParams(p); currentFilters.delete('from'); currentFilters.delete('to');
  const plan = budgetQuery(currentFilters, today);
  plan.where = plan.where.filter(x => x !== 'is_active=1');
  const from = p.get('from') || `${plan.year}-01-01`;
  const to = p.get('to') || `${plan.year}-12-31`;
  if (!validDay(from) || !validDay(to) || from < '2026-01-01' || to > '2100-12-31' || from > to) throw Error('INVALID_HISTORY_DATE_RANGE');
  const limitText = p.get('limit') || String(HISTORY_PAGE_SIZE);
  if (!/^\d{1,3}$/.test(limitText)) throw Error('INVALID_HISTORY_LIMIT');
  const limit = Number(limitText);
  if (limit < 1 || limit > HISTORY_MAX_ROWS) throw Error('INVALID_HISTORY_LIMIT');
  const key = p.get('key');
  if (key !== null) {
    if (!key || new TextEncoder().encode(key).length > 8192) throw Error('INVALID_HISTORY_KEY');
    plan.where.push('source_key=?');
    plan.args.push(key);
  }
  plan.where.push('first_snapshot>=?', 'first_snapshot<=?');
  plan.args.push(from, to);
  return {...plan, from, to, limit, signature: JSON.stringify([plan.where, plan.args])};
}

export function parseHistoryCursor(token, scope, revision) {
  if (!token) return null;
  if (token.length > 16384) throw Error('INVALID_HISTORY_CURSOR');
  let cursor;
  try { cursor = JSON.parse(token); } catch { throw Error('INVALID_HISTORY_CURSOR'); }
  if (!cursor || cursor.v !== 1 || cursor.scope !== scope || !/^[a-f0-9]{64}$/.test(cursor.scope) ||
      typeof cursor.revision !== 'string' || !/^(0|[1-9]\d*)$/.test(cursor.revision) ||
      typeof cursor.key !== 'string' || !cursor.key || new TextEncoder().encode(cursor.key).length > 8192 ||
      typeof cursor.hash !== 'string' || !/^[a-f0-9]{64}$/.test(cursor.hash) ||
      typeof cursor.date !== 'string' || !validDay(cursor.date)) throw Error('INVALID_HISTORY_CURSOR');
  if (cursor.revision !== revision) throw Error('BUDGET_HISTORY_SOURCE_CHANGED_RETRY');
  return cursor;
}

export function historyCursor(row, scope, revision) {
  return JSON.stringify({v: 1, scope, revision, date: row.first_snapshot, key: row.source_key, hash: row.payload_hash});
}

/** The outer window is at most limit+1 rows. Both row count and UTF-8 bytes are
 * bounded in SQL before transferring data to the Worker. */
export function budgetHistoryPage(plan, cursor = null) {
  const where = [...plan.where], args = [...plan.args];
  if (cursor) {
    // Explicit upper date bound keeps later pages from rescanning newer dates.
    where.push('first_snapshot<=?', '(first_snapshot<? OR (first_snapshot=? AND (source_key,payload_hash)>(?,?)))');
    args.push(cursor.date, cursor.date, cursor.date, cursor.key, cursor.hash);
  }
  const prefix = `WITH revision_scope AS (
    SELECT mode,service,source_key,payload_hash,row_json,first_seen,first_snapshot,
      ${['fiscal_year', 'category', 'region', 'org', 'dept', 'project'].map(k => `${j(k)} AS ${k}`).join(',')}
    FROM budget_revisions
  )`;
  const detail = `json_set(json_remove(row_json,'$.raw'),'$.source_key',source_key,'$.payload_hash',payload_hash,'$.first_snapshot',first_snapshot,'$.first_seen',first_seen,'$.snapshot_date',first_snapshot)`;
  const select = `SELECT source_key,payload_hash,first_snapshot,${detail} row_json FROM revision_scope WHERE ${where.join(' AND ')}`;
  return {sql: boundedJsonQuery(prefix, select, HISTORY_ORDER, plan.limit, HISTORY_PAGE_BYTES), args};
}

export function unpackHistoryPage(data, limit) {
  try { return unpackBounded(data, limit); } catch { throw Error('BUDGET_HISTORY_RECORD_TOO_LARGE'); }
}

/** Only two earlier distinct states per visible row are read. The second detects
 * an ambiguous previous date; its payload hash is never treated as temporal
 * order. Predicates/date-start from the visible search deliberately do not
 * filter these baselines. No per-project full history is materialized. */
export function budgetHistoryBaselines(plan, rows) {
  if (!rows.length || rows.length > HISTORY_BASELINE_CHUNK) throw Error('INVALID_HISTORY_BASELINE_CHUNK');
  const targets = JSON.stringify(rows.map(row => ({key: row.source_key, date: row.first_snapshot, hash: row.payload_hash})));
  const amounts = `json_object(${[...amountFields, 'invalid_amount'].flatMap(k => [`'${k}'`, j(k)]).join(',')})`;
  return {sql: `WITH targets AS (
    SELECT CAST(key AS INTEGER) ordinal,json_extract(value,'$.key') source_key,
      json_extract(value,'$.date') first_snapshot,json_extract(value,'$.hash') payload_hash FROM json_each(?)
  ), evidence AS MATERIALIZED (
    SELECT t.ordinal,
      EXISTS(SELECT 1 FROM budget_revisions r WHERE r.mode=? AND r.service='QWGJK'
        AND r.source_key=t.source_key AND r.first_snapshot=t.first_snapshot AND r.payload_hash<>t.payload_hash
        AND json_extract(r.row_json,'$.fiscal_year')=?) same_date_ambiguous,
      (SELECT json_group_array(json(state_json)) FROM (
        SELECT json_object('first_snapshot',first_snapshot,'payload_hash',payload_hash,'amounts',${amounts}) state_json
        FROM budget_revisions WHERE mode=? AND service='QWGJK' AND source_key=t.source_key
          AND first_snapshot<t.first_snapshot AND json_extract(row_json,'$.fiscal_year')=?
        ORDER BY first_snapshot DESC,payload_hash ASC LIMIT 2
      )) previous_json
    FROM targets t
  ) SELECT ordinal,same_date_ambiguous,
    CASE WHEN length(CAST(previous_json AS BLOB))<=${HISTORY_BASELINE_BYTES} THEN previous_json ELSE NULL END previous_json
    FROM evidence ORDER BY ordinal`, args: [targets, plan.mode, plan.year, plan.mode, plan.year]};
}

function normalizedAmounts(row) {
  const invalid = (row.invalid_amount !== null && row.invalid_amount !== undefined && row.invalid_amount !== 0) ||
    amountFields.some(k => row[k] !== null && row[k] !== undefined &&
      (typeof row[k] !== 'string' || row[k].length > 8194 || !/^(0|-?[1-9]\d*)$/.test(row[k])));
  const values = Object.fromEntries(amountFields.map(k => [k, invalid ? null : row[k] ?? null]));
  return {...values, effective_budget_amount: values.budget_amount ?? values.appropriation_amount, invalid};
}

export function budgetHistoryEnrich(row, evidence, from) {
  if (!evidence || evidence.previous_json === null) throw Error('BUDGET_HISTORY_BASELINE_TOO_LARGE');
  const previous = JSON.parse(evidence.previous_json);
  if (!Array.isArray(previous) || previous.length > 2) throw Error('BUDGET_HISTORY_INVALID_EVIDENCE');
  const current = normalizedAmounts(row), prior = previous[0], earlier = prior ? normalizedAmounts(prior.amounts) : null;
  const currentAmbiguous = evidence.same_date_ambiguous === 1;
  const previousAmbiguous = previous.length > 1 && previous[0].first_snapshot === previous[1].first_snapshot;
  let status = currentAmbiguous ? 'AMBIGUOUS_CURRENT_DATE' : previousAmbiguous ? 'AMBIGUOUS_PREVIOUS_DATE' : !prior ? 'NO_PREVIOUS_STATE' : current.invalid || earlier.invalid ? 'INVALID_AMOUNT' : 'AVAILABLE';
  const canCompare = status === 'AVAILABLE';
  const delta = key => canCompare && current[key] !== null && earlier[key] !== null ? (BigInt(current[key]) - BigInt(earlier[key])).toString() : null;
  const changes = {budget_change: delta('effective_budget_amount'), executed_change: delta('executed_amount'), remaining_change: delta('remaining_amount')};
  if (canCompare && Object.values(changes).some(x => x === null)) status = 'MISSING_AMOUNT';
  const {invalid, ...amounts} = current;
  return {...row, ...amounts, invalid_amount: invalid ? 1 : 0, ...changes,
    delta_status: status, current_date_ambiguous: currentAmbiguous, previous_date_ambiguous: previousAmbiguous,
    previous_first_snapshot: prior?.first_snapshot ?? null,
    previous_payload_hash: prior && !previousAmbiguous ? prior.payload_hash : null,
    baseline_before_start: !!prior && prior.first_snapshot < from,
  };
}
