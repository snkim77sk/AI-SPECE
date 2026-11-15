import {database} from './g2b-service';
import {kstDate} from './collector.mjs';
import {digest} from './budget-domain.mjs';
import {
  budgetHistoryPlan, parseHistoryCursor, budgetHistoryPage, unpackHistoryPage,
  budgetHistoryBaselines, budgetHistoryEnrich, historyCursor,
  HISTORY_BASELINE_CHUNK, HISTORY_MAX_ROWS, HISTORY_PAGE_BYTES,
} from './budget-history-query.mjs';

type Epoch = {revision: string};
type HistoryRow = Record<string, unknown>;
type Evidence = {ordinal: number; same_date_ambiguous: number; previous_json: string | null};

/** Stored QWGJK unique states only. No provider calls, credentials, current-row
 * lookup, writes, or materialized full dataset. The dedicated epoch includes
 * history-only inserts (the current-budget/matching epoch does not). */
export async function budgetHistorySearch(params: URLSearchParams) {
  const plan = budgetHistoryPlan(params, kstDate()), db = database();
  const readEpoch = () => db.prepare("SELECT COALESCE((SELECT CAST(revision AS TEXT) FROM budget_history_epochs WHERE mode=?),'0') revision").bind(plan.mode).first<Epoch>();
  const before = await readEpoch();
  if (!before || !/^(0|[1-9]\d*)$/.test(before.revision)) throw Error('BUDGET_HISTORY_SOURCE_NOT_FOUND');
  const scope = await digest(plan.signature);
  const cursor = parseHistoryCursor(params.get('cursor'), scope, before.revision);
  const query = budgetHistoryPage(plan, cursor);
  const data = (await db.prepare(query.sql).bind(...query.args).all()).results;
  const page = unpackHistoryPage(data, plan.limit), rows: HistoryRow[] = [];
  for (let index = 0; index < page.rows.length; index += HISTORY_BASELINE_CHUNK) {
    const chunk = page.rows.slice(index, index + HISTORY_BASELINE_CHUNK);
    const baseline = budgetHistoryBaselines(plan, chunk);
    const evidence = (await db.prepare(baseline.sql).bind(...baseline.args).all()).results as Evidence[];
    if (evidence.length !== chunk.length || evidence.some((item, i) => item.ordinal !== i)) throw Error('BUDGET_HISTORY_INVALID_EVIDENCE');
    rows.push(...chunk.map((row: HistoryRow, i: number) => budgetHistoryEnrich(row, evidence[i], plan.from)));
  }
  const after = await readEpoch();
  if (after?.revision !== before.revision) throw Error('BUDGET_HISTORY_SOURCE_CHANGED_RETRY');
  const last = page.rows.at(-1);
  return {
    mode: plan.mode, service: 'QWGJK', year: plan.year, from: plan.from, to: plan.to, rows,
    next: !page.done && last ? historyCursor(last, scope, before.revision) : null,
    limit: plan.limit, maxPageRows: HISTORY_MAX_ROWS, maxPageBytes: HISTORY_PAGE_BYTES,
    sourceRevision: before.revision, source: 'STORED_UNIQUE_STATE_HISTORY',
    order: 'FIRST_SNAPSHOT_DESC_KEY_HASH', filterScope: 'EACH_STORED_REVISION',
    baseline: 'PREVIOUS_STRICTLY_EARLIER_DISTINCT_STORED_STATE',
    missingMoney: 'NULL_NOT_ZERO', invalidMoney: 'EXCLUDED_FROM_DELTAS',
    completeDailyTimeline: false, repeatedStatesCollapsed: true, dateOnlyObservationsDeduplicated: true,
    firstSnapshotMeaning: 'SNAPSHOT_WHEN_THIS_UNIQUE_STATE_WAS_FIRST_STORED_NOT_EARLIEST_KNOWN_OCCURRENCE',
    sameDateOrderKnown: false, sourceCompletenessVerified: false, providerCalls: 0, source_io_performed: false,
  };
}
