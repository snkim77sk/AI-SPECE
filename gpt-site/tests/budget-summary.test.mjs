import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync, readdirSync, existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {registerHooks} from 'node:module';
import {budgetQuery} from '../lib/budget-query.mjs';
import {budgetSummaryPlan, budgetSummaryPreflight, budgetSummarySums, budgetSummaryResult, summaryMoneyUnavailable, SUMMARY_MAX_ROWS, SUMMARY_MAX_DIGITS} from '../lib/budget-summary.mjs';

const env = {DB: null};
globalThis.__g2bSummaryEnv = new Proxy(env, {get(target, key) {
  if (key === 'G2B_PUBLIC_AUTH_ENABLED') return undefined;
  if (key !== 'DB') throw Error('SUMMARY_MUST_NOT_READ_CONFIG_OR_SECRETS');
  return target.DB;
}});
registerHooks({resolve(spec, ctx, next) {
  if (spec === 'cloudflare:workers') return {url: 'data:text/javascript,export const env=globalThis.__g2bSummaryEnv', shortCircuit: true};
  if (spec.startsWith('.') && !/\.(ts|mjs|js)$/.test(spec) && ctx.parentURL?.startsWith('file:')) {
    const url = new URL(spec + '.ts', ctx.parentURL);
    if (existsSync(fileURLToPath(url))) return next(url.href, ctx);
  }
  return next(spec, ctx);
}});
const {budgetSummary} = await import('../lib/budget-summary-service.ts');
const {GET} = await import('../app/api/budget/summary/route.ts');
const {normalizeBudget} = await import('../lib/budget-domain.mjs');
const params = (extra = {}) => new URLSearchParams({mode: 'synthetic', service: 'QWGJK', year: '2026', ...extra});

function setup(t, hook = () => {}) {
  const db = new DatabaseSync(':memory:');
  for (const file of readdirSync(new URL('../drizzle/', import.meta.url)).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL('../drizzle/' + file, import.meta.url), 'utf8'));
  const reads = [];
  env.DB = {prepare(sql) {
    assert.match(sql, /^\s*(WITH|SELECT)\b/);
    let args = [];
    const query = {bind(...values) {args = values; return query;}, async first() {
      reads.push({sql, args});
      const rows = db.prepare(sql).all(...args);
      assert.ok(rows.length <= 1, 'D1 returns at most one aggregate/epoch row, not projects');
      hook(sql, db, reads);
      return rows[0] ?? null;
    }, async all() {throw Error('SUMMARY_MUST_NOT_MATERIALIZE_ROWS');}, async run() {throw Error('SUMMARY_MUST_NOT_WRITE');}};
    return query;
  }, async batch() {throw Error('SUMMARY_MUST_NOT_WRITE');}};
  const fetch = globalThis.fetch;
  globalThis.fetch = () => {throw Error('SUMMARY_MUST_NOT_CALL_PROVIDER');};
  t.after(() => {db.close(); env.DB = null; globalThis.fetch = fetch;});
  return {db, reads};
}
async function insert(db, key, patch = {}, extra = {}, storage = {}) {
  const service = storage.service || 'QWGJK', year = storage.year || 2026;
  const source = {fyr: String(year), wa_laf_cd: '11', wa_laf_hg_nm: '서울특별시', laf_cd: 'ORG-1', laf_hg_nm: '서울특별시 중구', dept_cd: 'D1', dept_nm: '도로과', dbiz_cd: key, dbiz_nm: '합성 조명 ' + key, acnt_dv_cd: '01', bdg_cash_amt: '1000', ep_amt: '100', biz_bdg_tott_amt: '999999', fld_cd: key, ...patch};
  const row = {...await normalizeBudget(source, {service, fiscal_year: year, snapshot_date: year + '-11-01'}), ...extra};
  db.prepare('INSERT INTO budget_rows(mode,service,source_key,payload_hash,snapshot_date,fiscal_year,org,region,dept,project,category,row_json,last_session,is_active,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').run(storage.mode || 'synthetic', service, row.source_key, row.payload_hash, year + '-11-01', year, row.org, row.region, row.dept, row.project, row.category, JSON.stringify(row), 'fixture', storage.active ?? 1, '2026-11-06T00:00:00Z');
  return row;
}

function knownTotal(rows, field) {
  const values = rows.filter(r => !r.invalid_amount).map(r => field === 'budget' ? r.budget_amount ?? r.appropriation_amount : r[field + '_amount']).filter(v => v !== null && v !== undefined);
  return values.length || !rows.length ? values.reduce((a, b) => a + BigInt(b), 0n).toString() : null;
}

test('full filtered summary counts all 237 rows, ignores pagination/sort and keeps D1 transfer bounded', async t => {
  const {db, reads} = setup(t);
  for (let i = 0; i < 237; i++) await insert(db, String(i));
  const result = await budgetSummary(params({cursor: 'not even a cursor', sort: 'BUDGET', limit: '1', offset: '200'}));
  assert.equal(result.project_count, 237);
  assert.equal(result.budget_total, '237000');
  assert.equal(result.executed_total, '23700');
  assert.equal(result.remaining_total, '213300');
  assert.equal(result.partial_count, 237);
  assert.equal(result.sales_ready_count, 237);
  assert.equal(result.sales_ready_remaining, '213300');
  assert.equal(result.moneyComplete, true);
  assert.equal(result.scope, 'FULL_FILTERED_CURRENT');
  assert.equal(result.sourceCompletenessVerified, false);
  assert.equal(result.sourceRevision, '0');
  assert.equal(result.providerCalls, 0);
  assert.equal(reads.length, 4);
  assert.ok(reads.every(r => !/ORDER BY|LIMIT|OFFSET|SELECT \*/.test(r.sql.replaceAll('SELECT *,', 'SELECT '))));
});

test('same current predicates cover region, names, code selectors, query, category, view, execution and mode/year', async t => {
  const {db} = setup(t);
  await insert(db, 'zero', {ep_amt: '0'});
  await insert(db, 'partial');
  await insert(db, 'full', {ep_amt: '1000'});
  await insert(db, 'over', {ep_amt: '1200'});
  await insert(db, 'pole', {dbiz_nm: '합성 가로등주 설치', dept_cd: 'D2', dept_nm: '설치과'});
  await insert(db, 'other', {dbiz_nm: '도서관 운영', laf_cd: 'ORG-2', laf_hg_nm: '도서관'});
  await insert(db, 'alias', {laf_hg_nm: '중구'});
  await insert(db, 'busan', {wa_laf_cd: '26', wa_laf_hg_nm: '부산광역시'});
  await insert(db, 'wild', {dbiz_nm: 'literal 100%_\\ 조명'});
  await insert(db, 'live', {}, {}, {mode: 'live'});
  await insert(db, 'old-year', {}, {}, {year: 2027});
  await insert(db, 'inactive', {}, {}, {active: 0});
  await insert(db, 'structural', {}, {}, {service: 'AIDFA'});
  const orgKey = JSON.stringify(['code', '11', 'ORG-1']), deptKey = JSON.stringify(['code', 'D1']);
  const cases = [{}, {mode: 'live'}, {year: '2027'}, {category: 'POLE'}, {region: '부산광역시'}, {org: '서울특별시 중구'}, {dept: '설치과'}, {orgKey, org: 'conflicting ignored'}, {orgKey, deptKey, dept: 'conflicting ignored'}, {q: '100%_\\'}, {q: '합성 조명'}, {view: 'targets'}, {execution: 'UNEXECUTED'}, {execution: 'PARTIAL'}, {execution: 'FULL'}, {view: 'targets', execution: 'PARTIAL', orgKey, deptKey}];
  for (const filters of cases) {
    const p = params(filters), plan = budgetQuery(p, '2026-11-07');
    const rows = db.prepare(`SELECT row_json FROM budget_rows WHERE ${plan.where.join(' AND ')}`).all(...plan.args).map(r => JSON.parse(r.row_json));
    const summary = await budgetSummary(p);
    assert.equal(summary.project_count, rows.length, JSON.stringify(filters));
    assert.equal(summary.budget_total, knownTotal(rows, 'budget'), JSON.stringify(filters));
    assert.equal(summary.executed_total, knownTotal(rows, 'executed'), JSON.stringify(filters));
    assert.equal(summary.remaining_total, knownTotal(rows, 'remaining'), JSON.stringify(filters));
  }
  const all = await budgetSummary(params());
  for (const [execution, key] of [['UNEXECUTED', 'unexecuted_count'], ['PARTIAL', 'partial_count'], ['FULL', 'full_count']]) assert.equal(all[key], (await budgetSummary(params({execution}))).project_count);
  const targets = await budgetSummary(params({view: 'targets'}));
  assert.equal(all.sales_ready_count, targets.project_count);
  assert.equal(all.sales_ready_remaining, targets.remaining_total);
});

test('null, zero, invalid and appropriation fallback remain distinct without fabricated unspent amounts', async t => {
  const {db} = setup(t);
  await insert(db, 'missing', {bdg_cash_amt: '', ep_amt: ''});
  await insert(db, 'zero', {bdg_cash_amt: '0', cpl_amt: '9000', ep_amt: '0'});
  await insert(db, 'fallback', {bdg_cash_amt: '', cpl_amt: '500', ep_amt: '0'});
  await insert(db, 'unexecuted', {ep_amt: '0'});
  await insert(db, 'unknown-execution', {ep_amt: ''});
  await insert(db, 'invalid-budget', {bdg_cash_amt: 'not-money', cpl_amt: '800', ep_amt: '0'});
  await insert(db, 'invalid-unused-appropriation', {cpl_amt: 'invalid'});
  await insert(db, 'negative-budget', {bdg_cash_amt: '-10', ep_amt: '0'});
  await insert(db, 'negative-execution', {bdg_cash_amt: '100', ep_amt: '-5'});
  const result = await budgetSummary(params());
  assert.equal(result.project_count, 9);
  assert.equal(result.budget_total, '2590');
  assert.equal(result.executed_total, '-5');
  assert.equal(result.remaining_total, '1595');
  assert.equal(result.invalid_amount_count, 2);
  assert.equal(result.missing_budget_count, 3);
  assert.equal(result.missing_executed_count, 4);
  assert.equal(result.missing_remaining_count, 4);
  assert.equal(result.unexecuted_count, 2);
  assert.equal(result.partial_count, 0);
  assert.equal(result.full_count, 0);
  assert.equal(result.execution_unknown_count, 7);
  assert.equal(result.sales_ready_count, 3);
  assert.equal(result.sales_ready_remaining, '1605');
  assert.equal(result.moneyComplete, false);
  assert.equal(result.moneyUnavailable, null);
  const unknown = await budgetSummary(params({q: 'missing'}));
  assert.equal(unknown.budget_total, null);
  assert.equal(unknown.executed_total, null);
  assert.equal(unknown.remaining_total, null);
  assert.equal(unknown.unexecuted_count, 0);
  const zero = await budgetSummary(params({q: 'zero'}));
  assert.equal(zero.budget_total, '0');
  assert.equal(zero.executed_total, '0');
  assert.equal(zero.remaining_total, '0');
  const empty = await budgetSummary(params({q: 'not-found'}));
  assert.equal(empty.project_count, 0);
  assert.equal(empty.budget_total, '0');
  assert.equal(empty.moneyComplete, true);
});

test('signed limb sums stay exact beyond JS-safe and int64 money with carries and cancellation up to 72 digits', async t => {
  const {db} = setup(t), values = ['1', '-1', '0', '999999999', '1000000000', '-1000000001', '9007199254740993', '9223372036854775808', '-9223372036854775809', '9'.repeat(72), '-' + '9'.repeat(71), '1' + '0'.repeat(71)];
  let budget = 0n, executed = 0n, remaining = 0n;
  for (const [i, value] of values.entries()) {
    const spending = value === '9'.repeat(72) ? '0' : String(i % 2 ? -i : i);
    const row = await insert(db, String(i), {bdg_cash_amt: value, ep_amt: spending});
    budget += BigInt(value); executed += BigInt(spending); remaining += BigInt(row.remaining_amount);
  }
  const result = await budgetSummary(params());
  assert.equal(result.budget_total, budget.toString());
  assert.equal(result.executed_total, executed.toString());
  assert.equal(result.remaining_total, remaining.toString());
  assert.equal(result.moneyUnavailable, null);
  assert.equal(result.moneyComplete, true);
  // Every width around limb boundaries, each isolated and then combined.
  for (const digits of [1, 8, 9, 10, 17, 18, 19, 26, 27, 28, 62, 63, 64, 71, 72]) {
    const value = '-' + '123456789'.repeat(8).slice(0, digits);
    await insert(db, 'width-' + digits, {bdg_cash_amt: value, ep_amt: '0'});
    assert.equal((await budgetSummary(params({q: 'width-' + digits}))).budget_total, value);
  }
});

test('oversized canonical money fails closed for totals but preserves exact filters/counts and does not issue sums', async t => {
  const {db, reads} = setup(t);
  await insert(db, 'huge', {bdg_cash_amt: '9'.repeat(SUMMARY_MAX_DIGITS + 1), ep_amt: '0'});
  await insert(db, 'normal');
  const result = await budgetSummary(params());
  assert.equal(result.project_count, 2);
  assert.equal(result.unexecuted_count, 1);
  assert.equal(result.partial_count, 1);
  assert.equal(result.sales_ready_count, 2);
  assert.equal(result.moneyUnavailable, 'AMOUNT_EXCEEDS_SUMMARY_DIGIT_LIMIT');
  for (const k of ['budget_total', 'executed_total', 'remaining_total', 'sales_ready_remaining']) assert.equal(result[k], null);
  assert.equal(result.moneyComplete, false);
  assert.equal(reads.length, 3);
  assert.ok(reads.every(r => !r.sql.includes('_limb_')));
  assert.equal((await budgetSummary(params({q: 'normal'}))).budget_total, '1000');
});

test('malformed stored numeric forms are invalid, not SQLite coerced or zero, including embedded NUL', async t => {
  const {db} = setup(t);
  for (const [i, amount] of ['01', '-0', '+1', '1.2', '1e3', 'not-money', ' 1', '', 7, '1\u0000garbage'].entries()) await insert(db, String(i), {}, {budget_amount: amount});
  const result = await budgetSummary(params());
  assert.equal(result.project_count, 10);
  assert.equal(result.invalid_amount_count, 10);
  assert.equal(result.missing_budget_count, 10);
  assert.equal(result.budget_total, null);
  assert.equal(result.executed_total, null);
  assert.equal(result.remaining_total, null);
  assert.equal(result.execution_unknown_count, 10);
  assert.equal(result.sales_ready_count, 0);
});

test('source epochs changing after preflight or after sums reject mixed snapshots; guard prevents unsafe follow-on reads', async t => {
  for (const after of ['project_count', '_limb_']) {
    const {db, reads} = setup(t, (sql, database) => {
      if (sql.includes(after)) database.exec("UPDATE budget_epochs SET revision=revision+1 WHERE mode='synthetic'");
    });
    await insert(db, '1');
    await assert.rejects(budgetSummary(params()), /BUDGET_SUMMARY_SOURCE_CHANGED_RETRY/);
    assert.equal(reads.length, 4);
  }
});

test('missing epoch fails explicitly, revision is lossless text, AIDFA/history remain separate and filters validate', async t => {
  const {db, reads} = setup(t);
  await assert.rejects(budgetSummary(params({service: 'AIDFA'})), /DETAIL_BUDGET_SUMMARY_ONLY/);
  await assert.rejects(budgetSummary(params({key: 'history-key'})), /CURRENT_BUDGET_SUMMARY_ONLY/);
  await assert.rejects(budgetSummary(params({year: '2025'})), /INVALID_YEAR/);
  await assert.rejects(budgetSummary(params({execution: 'fake'})), /INVALID_EXECUTION_FILTER/);
  await assert.rejects(budgetSummary(params({orgKey: '{}'})), /INVALID_BUDGET_SELECTOR/);
  assert.equal(reads.length, 0);
  db.exec("UPDATE budget_epochs SET revision=9007199254740993 WHERE mode='synthetic'");
  assert.equal((await budgetSummary(params())).sourceRevision, '9007199254740993');
  db.exec("DELETE FROM budget_epochs WHERE mode='synthetic'");
  await assert.rejects(budgetSummary(params()), /BUDGET_SUMMARY_SOURCE_NOT_FOUND/);
});

test('pure safety preflight refuses unsafe count bound without losing accurate counts', () => {
  const plan = budgetSummaryPlan(params(), '2026-11-07');
  const empty = {project_count: '0', missing_budget_count: '0', missing_executed_count: '0', missing_remaining_count: '0', invalid_amount_count: '0', unexecuted_count: '0', partial_count: '0', full_count: '0', sales_ready_count: '0', max_digits: 1};
  assert.equal(summaryMoneyUnavailable({...empty, project_count: String(SUMMARY_MAX_ROWS)}), null);
  for (const count of [BigInt(SUMMARY_MAX_ROWS) + 1n, 9007199254740993n]) {
    const stats = {...empty, project_count: count.toString()};
    assert.equal(summaryMoneyUnavailable(stats), 'ROW_COUNT_EXCEEDS_EXACT_SUMMARY_LIMIT');
    assert.throws(() => budgetSummarySums(plan, stats), /UNSAFE_AGGREGATE/);
    const summary = budgetSummaryResult(plan, stats, null, 0, '1');
    assert.equal(summary.project_count, count > BigInt(Number.MAX_SAFE_INTEGER) ? count.toString() : Number(count));
    assert.equal(summary.budget_total, null);
  }
  const query = budgetSummarySums(plan, empty);
  assert.ok(query.sql.length < 15000);
  assert.ok(budgetSummaryPreflight(plan).length < 15000);
  assert.throws(() => summaryMoneyUnavailable({...empty, project_count: 1}), /INVALID_AGGREGATE/);
  assert.throws(() => summaryMoneyUnavailable({...empty, max_digits: NaN}), /INVALID_AGGREGATE/);
  const aggregate = {budget_limb_0: Number.MAX_SAFE_INTEGER + 1, executed_limb_0: 0, remaining_limb_0: 0, sales_ready_remaining_limb_0: 0};
  assert.throws(() => budgetSummaryResult(plan, empty, aggregate, 1, '0'), /INVALID_AGGREGATE/);
});

test('GET is read-only/no-store, reports unsupported source, and uses conflict status on concurrent source change', async t => {
  const {db} = setup(t);
  await insert(db, '1');
  const response = await GET(new Request('https://example.test/api/budget/summary?' + params()));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('Cache-Control'), 'no-store');
  assert.equal((await response.json()).project_count, 1);
  const unsupported = await GET(new Request('https://example.test/api/budget/summary?' + params({service: 'AIDFA'})));
  assert.equal(unsupported.status, 400);
  assert.equal((await unsupported.json()).error, 'DETAIL_BUDGET_SUMMARY_ONLY');
  assert.equal(unsupported.headers.get('Cache-Control'), 'no-store');
  setup(t, (sql, database) => {if (sql.includes('project_count')) database.exec("UPDATE budget_epochs SET revision=revision+1 WHERE mode='synthetic'");});
  const conflict = await GET(new Request('https://example.test/api/budget/summary?' + params()));
  assert.equal(conflict.status, 409);
  assert.equal((await conflict.json()).error, 'BUDGET_SUMMARY_SOURCE_CHANGED_RETRY');
});

test('deterministic mixed-width property check preserves exact signed totals and cancellation', async t => {
  const {db} = setup(t), rows = [];
  for (let i = 0; i < 216; i++) {
    const width = i % 72 + 1;
    const absolute = '123456789'.repeat(8).slice(0, width);
    const value = i % 3 ? '-' + absolute : absolute;
    rows.push(await insert(db, String(i), {bdg_cash_amt: value, ep_amt: '0'}));
  }
  const result = await budgetSummary(params());
  assert.equal(result.project_count, 216);
  assert.equal(result.budget_total, knownTotal(rows, 'budget'));
  assert.equal(result.executed_total, '0');
  assert.equal(result.remaining_total, knownTotal(rows, 'remaining'));
  assert.equal(result.moneyUnavailable, null);
});

test('source guard suppresses new out-of-bound amounts inserted between preflight and sums', async t => {
  let sumRow;
  const {db} = setup(t, (sql, database) => {
    if (sql.includes('project_count')) {
      database.exec(`UPDATE budget_rows SET row_json=json_set(row_json,'$.budget_amount','${'9'.repeat(80)}'); UPDATE budget_epochs SET revision=revision+1 WHERE mode='synthetic';`);
    }
    if (sql.includes('_limb_')) sumRow = database.prepare(sql).get('synthetic', 'QWGJK', 2026, 'synthetic', '0');
  });
  await insert(db, 'one');
  await assert.rejects(budgetSummary(params()), /BUDGET_SUMMARY_SOURCE_CHANGED_RETRY/);
  assert.ok(Object.values(sumRow).every(value => value === 0));
});

test('maximum safe signed limb bound remains exact and contains no materializing query plan', t => {
  const {db} = setup(t), plan = budgetSummaryPlan(params(), '2026-11-07');
  const stats = {project_count: String(SUMMARY_MAX_ROWS), missing_budget_count: '0', missing_executed_count: '0', missing_remaining_count: '0', invalid_amount_count: '0', unexecuted_count: '0', partial_count: '0', full_count: '0', sales_ready_count: '0', max_digits: 9};
  const limit = BigInt(SUMMARY_MAX_ROWS) * 999999999n;
  assert.ok(limit <= BigInt(Number.MAX_SAFE_INTEGER));
  assert.ok(limit + 999999999n > BigInt(Number.MAX_SAFE_INTEGER));
  const aggregate = {budget_limb_0: Number(limit), executed_limb_0: Number(-limit), remaining_limb_0: 0, sales_ready_remaining_limb_0: 0};
  const result = budgetSummaryResult(plan, stats, aggregate, 1, '0');
  assert.equal(result.budget_total, limit.toString());
  assert.equal(result.executed_total, (-limit).toString());
  for (const [query, args] of [[budgetSummaryPreflight(plan), plan.args], [budgetSummarySums(plan, stats).sql, [...plan.args, 'synthetic', '0']]]) {
    const explain = db.prepare('EXPLAIN QUERY PLAN ' + query).all(...args);
    assert.ok(explain.every(row => !/MATERIALIZE|TEMP B-TREE/.test(row.detail)), JSON.stringify(explain));
  }
});
