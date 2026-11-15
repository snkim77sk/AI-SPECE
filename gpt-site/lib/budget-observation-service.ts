import {database} from './g2b-service';
import {kstDate} from './collector.mjs';
import {digest} from './budget-domain.mjs';
import {
  budgetObservationPlan, parseObservationCursor, budgetObservationPage, unpackObservationPage,
  budgetObservationBaselines, budgetObservationEnrich, observationCursor,
  OBSERVATION_BASELINE_CHUNK, OBSERVATION_MAX_ROWS, OBSERVATION_PAGE_BYTES,
} from './budget-observation-query.mjs';

type Epoch = {revision: string};
type ObservationRow = Record<string, unknown>;
type Evidence = {ordinal: number; same_date_ambiguous: number; previous_json: string | null};

/** Reads recorded QWGJK observations only, including history-only collection.
 * No backfill from unique states, provider calls, credentials, or DB writes. */
export async function budgetObservationSearch(params: URLSearchParams) {
  const plan = budgetObservationPlan(params, kstDate()), db = database();
  const readEpoch = () => db.prepare("SELECT COALESCE((SELECT CAST(revision AS TEXT) FROM budget_observation_epochs WHERE mode=?),'0') revision").bind(plan.mode).first<Epoch>();
  const before = await readEpoch();
  if (!before || !/^(0|[1-9]\d*)$/.test(before.revision)) throw Error('BUDGET_OBSERVATION_SOURCE_NOT_FOUND');
  const scope = await digest(plan.signature);
  const cursor = parseObservationCursor(params.get('cursor'), scope, before.revision);
  const query = budgetObservationPage(plan, cursor);
  const data = (await db.prepare(query.sql).bind(...query.args).all()).results;
  const page = unpackObservationPage(data, plan.limit), rows: ObservationRow[] = [];
  for (let index = 0; index < page.rows.length; index += OBSERVATION_BASELINE_CHUNK) {
    const chunk = page.rows.slice(index, index + OBSERVATION_BASELINE_CHUNK);
    const baseline = budgetObservationBaselines(plan, chunk);
    const evidence = (await db.prepare(baseline.sql).bind(...baseline.args).all()).results as Evidence[];
    if (evidence.length !== chunk.length || evidence.some((item, i) => item.ordinal !== i)) throw Error('BUDGET_OBSERVATION_INVALID_EVIDENCE');
    rows.push(...chunk.map((row: ObservationRow, i: number) => budgetObservationEnrich(row, evidence[i], plan.from)));
  }
  const after = await readEpoch();
  if (after?.revision !== before.revision) throw Error('BUDGET_OBSERVATION_SOURCE_CHANGED_RETRY');
  const last = page.rows.at(-1);
  return {
    mode: plan.mode, service: 'QWGJK', year: plan.year, from: plan.from, to: plan.to, rows,
    next: !page.done && last ? observationCursor(last, scope, before.revision) : null,
    limit: plan.limit, maxPageRows: OBSERVATION_MAX_ROWS, maxPageBytes: OBSERVATION_PAGE_BYTES,
    sourceRevision: before.revision, source: 'STORED_DAILY_OBSERVATIONS',
    order: 'SNAPSHOT_DATE_DESC_KEY_HASH', filterScope: 'EACH_OBSERVED_REVISION',
    baseline: 'PREVIOUS_STRICTLY_EARLIER_OBSERVED_DAY',
    missingMoney: 'NULL_NOT_ZERO', invalidMoney: 'EXCLUDED_FROM_DELTAS',
    completeDailyPresence: false, completeIntradayTimeline: false,
    crossDateRepeatedStatesPreserved: true, sameDateSameStateDeduplicated: true,
    observationDateMeaning: 'SOURCE_SNAPSHOT_DATE_NOT_UNIQUE_STATE_FIRST_SNAPSHOT',
    legacyUniqueStatesBackfilled: false, sameDateOrderKnown: false,
    sourceCompletenessVerified: false, providerCalls: 0, source_io_performed: false,
  };
}
