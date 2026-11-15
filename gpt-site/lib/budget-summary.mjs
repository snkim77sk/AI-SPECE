import {BUDGET_BASIS, budgetQuery} from './budget-query.mjs';

export const SUMMARY_MAX_DIGITS = 72;
export const SUMMARY_LIMB_DIGITS = 9;
export const SUMMARY_LIMB_BASE = 1000000000n;
// Every signed limb SUM, including intermediate sums, stays exactly representable
// by both SQLite INTEGER and the D1 JavaScript-number result boundary.
export const SUMMARY_MAX_ROWS = Number(BigInt(Number.MAX_SAFE_INTEGER) / (SUMMARY_LIMB_BASE - 1n));
const j = key => `json_extract(row_json,'$.${key}')`;
const moneyFields = ['budget', 'executed', 'remaining', 'sales_ready_remaining'];
const countFields = ['project_count', 'missing_budget_count', 'missing_executed_count', 'missing_remaining_count', 'invalid_amount_count', 'unexecuted_count', 'partial_count', 'full_count', 'sales_ready_count'];
const positive = x => `(${x} IS NOT NULL AND ${x}<>'0' AND substr(${x},1,1)<>'-')`;
const canonical = x => `(typeof(${x})='text' AND instr(${x},char(0))=0 AND (${x}='0' OR (substr(${x},1,1) BETWEEN '1' AND '9' AND ${x} NOT GLOB '*[^0-9]*') OR (substr(${x},1,1)='-' AND substr(${x},2,1) BETWEEN '1' AND '9' AND substr(${x},2) NOT GLOB '*[^0-9]*')))`;

/** Same predicates as the current list/export; never apply its keyset cursor. */
export function budgetSummaryPlan(input, today) {
  const params = new URLSearchParams(input);
  if (params.has('key')) throw Error('CURRENT_BUDGET_SUMMARY_ONLY');
  if (params.get('service') === 'AIDFA') throw Error('DETAIL_BUDGET_SUMMARY_ONLY');
  params.delete('cursor');
  params.delete('sort');
  return budgetQuery(params, today);
}

function prefix(plan, guarded = false) {
  const guard = guarded ? ' AND (SELECT CAST(revision AS TEXT) FROM budget_epochs WHERE mode=?)=?' : '';
  // Valid normalized amounts are canonical signed integer strings. Defend against
  // malformed stored money too, rather than SQLite's permissive numeric casts.
  // A one-element json_each evaluates row validity once without materializing
  // the full selection; otherwise CTE flattening repeats it for every use.
  return `WITH selected AS (
    SELECT category, ${j('invalid_amount')} invalid_value,
      ${j('budget_amount')} raw_budget, ${j('appropriation_amount')} raw_appropriation,
      ${j('executed_amount')} raw_executed, ${j('remaining_amount')} raw_remaining,
      ${BUDGET_BASIS} basis_value
    FROM budget_rows WHERE ${plan.where.join(' AND ')}${guard}
  ), checked AS (
    SELECT selected.*, validity.value invalid
    FROM selected CROSS JOIN json_each(json_array(CASE WHEN COALESCE(invalid_value,0)<>0 OR ${['budget', 'appropriation', 'executed', 'remaining'].map(k => `(raw_${k} IS NOT NULL AND NOT ${canonical('raw_' + k)})`).join(' OR ')} THEN 1 ELSE 0 END)) validity
  ), money AS (
    SELECT category, invalid_value, invalid,
      CASE WHEN invalid=0 THEN basis_value END budget,
      CASE WHEN invalid=0 THEN raw_executed END executed,
      CASE WHEN invalid=0 THEN raw_remaining END remaining
    FROM checked
  ), classified AS (
    SELECT *,
      CASE WHEN ${positive('budget')} AND executed='0' THEN 1 ELSE 0 END unexecuted,
      CASE WHEN ${positive('budget')} AND ${positive('executed')} AND (length(executed),executed)<(length(budget),budget) THEN 1 ELSE 0 END partial,
      CASE WHEN ${positive('budget')} AND ${positive('executed')} AND (length(executed),executed)>=(length(budget),budget) THEN 1 ELSE 0 END fully_executed,
      CASE WHEN invalid_value=0 AND category IN ('LIGHTING','POLE') AND ${positive('remaining')} THEN 1 ELSE 0 END sales_ready
    FROM money
  ), amounts AS (
    SELECT *, CASE WHEN sales_ready=1 THEN remaining END sales_ready_remaining FROM classified
  )`;
}
const count = (condition, alias) => `CAST(COALESCE(SUM(CASE WHEN ${condition} THEN 1 ELSE 0 END),0) AS TEXT) ${alias}`;

/** One aggregate row, irrespective of the filtered population's size. */
export function budgetSummaryPreflight(plan) {
  return `${prefix(plan)} SELECT CAST(COUNT(*) AS TEXT) project_count,
    ${count('budget IS NULL', 'missing_budget_count')},
    ${count('executed IS NULL', 'missing_executed_count')},
    ${count('remaining IS NULL', 'missing_remaining_count')},
    ${count('invalid=1', 'invalid_amount_count')},
    ${count('unexecuted=1', 'unexecuted_count')},
    ${count('partial=1', 'partial_count')},
    ${count('fully_executed=1', 'full_count')},
    ${count('sales_ready=1', 'sales_ready_count')},
    COALESCE(MAX(MAX(${['budget', 'executed', 'remaining'].map(k => `COALESCE(length(ltrim(${k},'-')),0)`).join(',')})),0) max_digits
    FROM amounts`;
}

function unsignedCount(value) {
  if (typeof value !== 'string' || !/^(0|[1-9]\d*)$/.test(value)) throw Error('BUDGET_SUMMARY_INVALID_AGGREGATE');
  return BigInt(value);
}
const publicCount = value => value <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(value) : value.toString();

export function summaryMoneyUnavailable(preflight) {
  const rows = unsignedCount(preflight.project_count);
  if (!Number.isSafeInteger(preflight.max_digits) || preflight.max_digits < 0) throw Error('BUDGET_SUMMARY_INVALID_AGGREGATE');
  if (preflight.max_digits > SUMMARY_MAX_DIGITS) return 'AMOUNT_EXCEEDS_SUMMARY_DIGIT_LIMIT';
  if (rows > BigInt(SUMMARY_MAX_ROWS)) return 'ROW_COUNT_EXCEEDS_EXACT_SUMMARY_LIMIT';
  return null;
}

/** Only nine-digit limbs are cast, never a whole money amount. The epoch guard
 * keeps the preflight's row/digit safety bounds valid for this SQL snapshot. */
export function budgetSummarySums(plan, preflight) {
  if (summaryMoneyUnavailable(preflight)) throw Error('BUDGET_SUMMARY_UNSAFE_AGGREGATE');
  const limbs = Math.max(1, Math.ceil(preflight.max_digits / SUMMARY_LIMB_DIGITS));
  const columns = moneyFields.flatMap((field, index) => Array.from({length: limbs}, (_, i) => {
    // json_each exposes each validated amount once. It prevents SQLite's CTE
    // flattening from repeating JSON validation/extraction for every limb.
    const digits = "ltrim(amount.value,'-')";
    const limb = `CASE WHEN length(${digits})>${i * SUMMARY_LIMB_DIGITS} THEN CAST(substr(${digits},max(1,length(${digits})-${(i + 1) * SUMMARY_LIMB_DIGITS}+1),min(${SUMMARY_LIMB_DIGITS},length(${digits})-${i * SUMMARY_LIMB_DIGITS})) AS INTEGER) ELSE 0 END`;
    return `COALESCE(SUM(CASE WHEN amount.key=${index} THEN (CASE WHEN substr(amount.value,1,1)='-' THEN -1 ELSE 1 END)*(${limb}) ELSE 0 END),0) ${field}_limb_${i}`;
  }));
  return {sql: `${prefix(plan, true)} SELECT ${columns.join(',\n')} FROM amounts CROSS JOIN json_each(json_array(budget,executed,remaining,sales_ready_remaining)) amount`, limbs};
}

/** Recombine bounded SQL integer limbs with BigInt, exposing only JSON strings. */
export function budgetSummaryResult(plan, preflight, aggregate, limbs, revision) {
  const raw = Object.fromEntries(countFields.map(k => [k, unsignedCount(preflight[k])]));
  for (const value of Object.values(raw)) if (value > raw.project_count) throw Error('BUDGET_SUMMARY_INVALID_AGGREGATE');
  const known = raw.unexecuted_count + raw.partial_count + raw.full_count;
  if (known > raw.project_count) throw Error('BUDGET_SUMMARY_INVALID_AGGREGATE');
  const moneyUnavailable = summaryMoneyUnavailable(preflight);
  const totals = {};
  for (const field of moneyFields) {
    let result = null;
    if (!moneyUnavailable) {
      if (!aggregate || !Number.isInteger(limbs) || limbs < 1 || limbs > SUMMARY_MAX_DIGITS / SUMMARY_LIMB_DIGITS) throw Error('BUDGET_SUMMARY_INVALID_AGGREGATE');
      let sum = 0n;
      for (let i = limbs - 1; i >= 0; i--) {
        const limb = aggregate[`${field}_limb_${i}`];
        if (!Number.isSafeInteger(limb)) throw Error('BUDGET_SUMMARY_INVALID_AGGREGATE');
        sum = sum * SUMMARY_LIMB_BASE + BigInt(limb);
      }
      const available = field === 'sales_ready_remaining' ? raw.sales_ready_count : raw.project_count - raw[`missing_${field}_count`];
      // Empty selections have a known empty sum. Missing amounts in actual rows
      // do not become zero. Partial known sums are labelled moneyComplete=false.
      if (available > 0n || raw.project_count === 0n || field === 'sales_ready_remaining') result = sum.toString();
    }
    totals[field === 'sales_ready_remaining' ? field : `${field}_total`] = result;
  }
  return {
    mode: plan.mode, service: plan.service, year: plan.year,
    scope: 'FULL_FILTERED_CURRENT', source: 'STORED_CURRENT_ONLY', sourceRevision: revision,
    ...Object.fromEntries(Object.entries(raw).map(([k, value]) => [k, publicCount(value)])),
    execution_unknown_count: publicCount(raw.project_count - known), ...totals,
    moneyUnavailable,
    moneyComplete: !moneyUnavailable && ['budget', 'executed', 'remaining'].every(k => raw[`missing_${k}_count`] === 0n),
    maxAmountDigits: SUMMARY_MAX_DIGITS, maxExactSummaryRows: SUMMARY_MAX_ROWS,
    missingMoney: 'NULL_NOT_ZERO', invalidMoney: 'EXCLUDED_FROM_MONEY_AND_EXECUTION_COUNTS',
    moneyTotals: 'SUM_OF_AVAILABLE_VALID_AMOUNTS', countEncoding: 'SAFE_NUMBER_OR_DECIMAL_STRING',
    sourceCompletenessVerified: false, providerCalls: 0, source_io_performed: false,
  };
}
