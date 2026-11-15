import {database} from './g2b-service';
import {kstDate} from './collector.mjs';
import {budgetSummaryPlan, budgetSummaryPreflight, budgetSummarySums, budgetSummaryResult, summaryMoneyUnavailable} from './budget-summary.mjs';

type SummaryAggregate = Record<string, number | string>;

/** Stored current QWGJK only: no source I/O, configuration/key lookup or writes. */
export async function budgetSummary(params: URLSearchParams) {
  const plan = budgetSummaryPlan(params, kstDate()), db = database();
  const readEpoch = () => db.prepare('SELECT CAST(revision AS TEXT) revision FROM budget_epochs WHERE mode=?').bind(plan.mode).first<{revision: string}>();
  const before = await readEpoch();
  if (!before || !/^(0|[1-9]\d*)$/.test(before.revision)) throw Error('BUDGET_SUMMARY_SOURCE_NOT_FOUND');
  const preflight = await db.prepare(budgetSummaryPreflight(plan)).bind(...plan.args).first<SummaryAggregate>();
  if (!preflight) throw Error('BUDGET_SUMMARY_INVALID_AGGREGATE');
  let aggregate: SummaryAggregate | null = null, limbs = 0;
  if (!summaryMoneyUnavailable(preflight)) {
    const query = budgetSummarySums(plan, preflight);
    limbs = query.limbs;
    aggregate = await db.prepare(query.sql).bind(...plan.args, plan.mode, before.revision).first<SummaryAggregate>();
  }
  const after = await readEpoch();
  if (after?.revision !== before.revision) throw Error('BUDGET_SUMMARY_SOURCE_CHANGED_RETRY');
  return budgetSummaryResult(plan, preflight, aggregate, limbs, before.revision);
}
