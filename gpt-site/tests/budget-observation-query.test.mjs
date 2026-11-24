import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync, readdirSync, existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {registerHooks} from 'node:module';
import {
  budgetObservationPlan, budgetObservationPage, budgetObservationBaselines, budgetObservationEnrich, OBSERVATION_PAGE_BYTES,
  OBSERVATION_BASELINE_CHUNK, OBSERVATION_BASELINE_BYTES,
} from '../lib/budget-observation-query.mjs';
import {normalizeBudget} from '../lib/budget-domain.mjs';

const env = {DB: null};
globalThis.__g2bObservationEnv = new Proxy(env, {get(target, key) {
  if (key === 'G2B_PUBLIC_AUTH_ENABLED') return undefined;
  if (key !== 'DB') throw Error('OBSERVATION_MUST_NOT_READ_CONFIG_OR_SECRETS');
  return target.DB;
}});
registerHooks({resolve(spec, ctx, next) {
  if (spec === 'cloudflare:workers') return {url: 'data:text/javascript,export const env=globalThis.__g2bObservationEnv', shortCircuit: true};
  if (spec.startsWith('.') && !/\.(ts|mjs|js)$/.test(spec) && ctx.parentURL?.startsWith('file:')) {
    const url = new URL(spec + '.ts', ctx.parentURL);
    if (existsSync(fileURLToPath(url))) return next(url.href, ctx);
  }
  return next(spec, ctx);
}});
const {budgetObservationSearch} = await import('../lib/budget-observation-service.ts');
const {GET} = await import('../app/api/budget/observations/route.ts');
const params = (extra = {}) => new URLSearchParams({mode: 'synthetic', service: 'QWGJK', year: '2026', from: '2026-01-01', to: '2026-12-31', ...extra});
function setup(t, hook = () => {}) {
  const db = new DatabaseSync(':memory:');
  db.exec('PRAGMA foreign_keys=ON');
  for (const file of readdirSync(new URL('../drizzle/', import.meta.url)).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL('../drizzle/' + file, import.meta.url), 'utf8'));
  const reads = [];
  env.DB = {prepare(sql) {
    assert.match(sql, /^\s*(WITH|SELECT)\b/, 'service executes reads only');
    let args = [];
    const query = {bind(...values) {args = values; return query;}, async first() {
      const rows = db.prepare(sql).all(...args); reads.push({sql, args, count: rows.length});
      assert.ok(rows.length <= 1); hook(sql, db, reads); return rows[0] ?? null;
    }, async all() {
      const rows = db.prepare(sql).all(...args); reads.push({sql, args, count: rows.length});
      if (sql.includes('evidence AS')) {
        assert.ok(rows.length <= OBSERVATION_BASELINE_CHUNK);
        assert.ok(rows.reduce((sum, row) => sum + Buffer.byteLength(row.previous_json || ''), 0) <= OBSERVATION_BASELINE_CHUNK * OBSERVATION_BASELINE_BYTES);
      } else {
        assert.ok(rows.length <= 100);
        assert.ok(rows.reduce((sum, row) => sum + Buffer.byteLength(row.row_json || ''), 0) <= OBSERVATION_PAGE_BYTES);
      }
      hook(sql, db, reads); return {results: rows};
    }, async run() {throw Error('OBSERVATION_MUST_NOT_WRITE');}};
    return query;
  }, async batch() {throw Error('OBSERVATION_MUST_NOT_WRITE');}};
  const fetch = globalThis.fetch;
  globalThis.fetch = () => {throw Error('OBSERVATION_MUST_NOT_CALL_PROVIDER');};
  t.after(() => {db.close(); env.DB = null; globalThis.fetch = fetch;});
  return {db, reads};
}
async function insert(db, key, date, patch = {}, extra = {}, storage = {}) {
  const year = storage.year || 2026, service = storage.service || 'QWGJK';
  const source = {fyr: String(year), wa_laf_cd: '11', wa_laf_hg_nm: '서울특별시', laf_cd: 'ORG-1', laf_hg_nm: '서울특별시 중구', dept_cd: 'D1', dept_nm: '도로과', dbiz_cd: key, dbiz_nm: '합성 조명 ' + key, acnt_dv_cd: '01', acnt_dv_nm: '일반회계', bdg_cash_amt: '1000', ep_amt: '100', fld_cd: key, exe_ymd: date.replaceAll('-', ''), ...patch};
  const row = {...await normalizeBudget(source, {service, fiscal_year: year, snapshot_date: date}), ...extra};
  db.prepare('INSERT INTO budget_revisions(mode,service,source_key,payload_hash,row_json,first_seen,first_snapshot) VALUES(?,?,?,?,?,?,?) ON CONFLICT(mode,service,source_key,payload_hash) DO NOTHING').run(storage.mode || 'synthetic', service, row.source_key, row.payload_hash, JSON.stringify(row), storage.seen || date + 'T00:00:00Z', date);
  if (service === 'QWGJK' && !storage.legacyOnly) db.prepare('INSERT INTO budget_observations(mode,service,source_key,snapshot_date,payload_hash,fiscal_year,first_seen,last_seen,last_session) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING').run(storage.mode || 'synthetic', service, row.source_key, date, row.payload_hash, year, storage.seen || date + 'T00:00:00Z', storage.lastSeen || date + 'T00:00:00Z', storage.session || 'fixture');
  return row;
}

// No current budget_rows are needed: inactive/only-historical projects remain searchable.
test('cross-project observation is key-free and previous state before date-start contributes only to exact deltas', async t => {
  const {db} = setup(t);
  const old = await insert(db, 'A', '2026-01-02', {bdg_cash_amt: '9223372036854775809', ep_amt: '1', dbiz_nm: 'old state'});
  await insert(db, 'A', '2026-01-05', {bdg_cash_amt: '9223372036854775910', ep_amt: '4'});
  await insert(db, 'B', '2026-01-05', {bdg_cash_amt: '100', ep_amt: '50'});
  const result = await budgetObservationSearch(params({from: '2026-01-04', to: '2026-01-05', q: '조명'}));
  assert.equal(result.rows.length, 2);
  const a = result.rows.find(row => row.source_key === old.source_key);
  assert.equal(a.budget_change, '101'); assert.equal(a.executed_change, '3'); assert.equal(a.remaining_change, '98');
  assert.equal(a.previous_snapshot_date, '2026-01-02'); assert.equal(a.baseline_before_start, true);
  assert.equal(a.effective_budget_amount, '9223372036854775910');
  assert.equal(result.rows.find(row => row.source_key !== old.source_key).delta_status, 'NO_PREVIOUS_OBSERVATION');
  assert.equal(db.prepare('SELECT COUNT(*) n FROM budget_rows').get().n, 0);
  assert.equal(result.source, 'STORED_DAILY_OBSERVATIONS'); assert.equal(result.completeDailyPresence, false);
  assert.equal(result.providerCalls, 0); assert.equal(result.source_io_performed, false);
  assert.ok(result.rows.every(row => !Object.hasOwn(row, 'raw')));
});

test('missing, explicit zero, invalid, and appropriation fallback never silently become zero', async t => {
  const {db} = setup(t);
  const cases = [
    ['missing', {bdg_cash_amt: '', ep_amt: ''}, {bdg_cash_amt: '', ep_amt: ''}, {}, 'MISSING_AMOUNT'],
    ['zero', {bdg_cash_amt: '0', ep_amt: '0'}, {bdg_cash_amt: '0', ep_amt: '0'}, {}, 'AVAILABLE'],
    ['fallback', {bdg_cash_amt: '', cpl_amt: '400', ep_amt: '10'}, {bdg_cash_amt: '', cpl_amt: '600', ep_amt: '20'}, {}, 'AVAILABLE'],
    ['invalid', {bdg_cash_amt: 'bad', cpl_amt: '400', ep_amt: '10'}, {bdg_cash_amt: '100', ep_amt: '20'}, {}, 'INVALID_AMOUNT'],
    ['malformed', {}, {}, {executed_amount: '12.3'}, 'INVALID_AMOUNT'],
    ['numeric', {}, {}, {budget_amount: 9007199254740992}, 'INVALID_AMOUNT'],
    ['partial', {bdg_cash_amt: '100', ep_amt: ''}, {bdg_cash_amt: '200', ep_amt: ''}, {}, 'MISSING_AMOUNT'],
    ['huge', {bdg_cash_amt: '-' + '9'.repeat(400), ep_amt: '0'}, {bdg_cash_amt: '9'.repeat(400), ep_amt: '0'}, {}, 'AVAILABLE'],
  ];
  const keys = {};
  for (const [key, old, next, extra] of cases) {
    keys[key] = (await insert(db, key, '2026-01-01', {...old, revision_marker: 'first'})).source_key;
    await insert(db, key, '2026-01-02', {...next, revision_marker: 'second'}, extra);
  }
  const result = await budgetObservationSearch(params({from: '2026-01-02', to: '2026-01-02'}));
  const byKey = Object.fromEntries(Object.entries(keys).map(([key, identity]) => [key, result.rows.find(row => row.source_key === identity)]));
  for (const [key,,,, status] of cases) assert.equal(byKey[key].delta_status, status, key);
  for (const key of ['missing', 'invalid', 'malformed', 'numeric']) {
    assert.equal(byKey[key].budget_change, null, key); assert.equal(byKey[key].executed_change, null, key); assert.equal(byKey[key].remaining_change, null, key);
  }
  assert.equal(byKey.zero.budget_change, '0'); assert.equal(byKey.zero.effective_budget_amount, '0');
  assert.equal(byKey.fallback.effective_budget_amount, '600'); assert.equal(byKey.fallback.budget_change, '200');
  assert.equal(byKey.partial.budget_change, '100'); assert.equal(byKey.partial.executed_change, null);
  assert.equal(byKey.huge.budget_change, (2n * BigInt('9'.repeat(400))).toString());
});

test('date ties are never ordered using first_seen or payload hash for deltas', async t => {
  const {db} = setup(t);
  await insert(db, 'tie', '2026-01-01', {ep_amt: '1'}, {}, {seen: '2026-12-31T00:00:00Z'});
  await insert(db, 'tie', '2026-01-02', {ep_amt: '2'}, {}, {seen: '2026-01-01T00:00:00Z'});
  await insert(db, 'tie', '2026-01-02', {ep_amt: '3'}, {}, {seen: '2026-02-01T00:00:00Z'});
  await insert(db, 'tie', '2026-01-03', {ep_amt: '4'});
  const result = await budgetObservationSearch(params({from: '2026-01-02', to: '2026-01-03'}));
  assert.deepEqual(result.rows.map(row => row.delta_status), ['AMBIGUOUS_PREVIOUS_DATE', 'AMBIGUOUS_CURRENT_DATE', 'AMBIGUOUS_CURRENT_DATE']);
  assert.ok(result.rows.every(row => row.budget_change === null && row.executed_change === null && row.remaining_change === null));
  assert.equal(result.rows[0].previous_payload_hash, null);
});

test('pagination crosses projects/dates/hash ties once without offsets or a full population transfer', async t => {
  const {db, reads} = setup(t);
  for (let i = 0; i < 237; i++) await insert(db, String(i).padStart(3, '0'), `2026-01-${String(1 + i % 5).padStart(2, '0')}`);
  await insert(db, '000', '2026-01-01', {ep_amt: '77'});
  const expected = db.prepare("SELECT source_key,snapshot_date,payload_hash FROM budget_observations WHERE mode='synthetic' ORDER BY snapshot_date DESC,source_key,payload_hash").all().map(row => row.source_key + row.snapshot_date + row.payload_hash);
  const seen = []; let cursor, pages = 0;
  do {
    const result = await budgetObservationSearch(params({limit: '17', ...(cursor ? {cursor} : {})}));
    seen.push(...result.rows.map(row => row.source_key + row.snapshot_date + row.payload_hash)); cursor = result.next; pages++;
    assert.ok(result.rows.length <= 17); assert.ok(pages < 20);
  } while (cursor);
  assert.equal(seen.length, 238); assert.equal(new Set(seen).size, 238); assert.deepEqual(seen, expected);
  assert.ok(reads.every(read => !/\bOFFSET\b|\bLAG\(/i.test(read.sql)));
  assert.ok(reads.filter(read => read.sql.includes('picked AS')).every(read => read.count <= 17));
});

test('selectors are evaluated on stored revisions and isolate modes, services and years', async t => {
  const {db} = setup(t);
  await insert(db, 'base', '2026-01-01', {inst_nm: '시립기관', fld_nm: '문화', sect_nm: '체육'});
  await insert(db, 'wild', '2026-01-01', {dbiz_nm: 'literal 100%_\\ 조명'});
  await insert(db, 'other', '2026-01-01', {laf_cd: 'OTHER', laf_hg_nm: '별도 기관', dept_cd: 'D2', dept_nm: '시설과'});
  await insert(db, 'incheon', '2026-01-01', {wa_laf_cd: '28', wa_laf_hg_nm: '인천광역시', inst_nm: '인천경제자유구역청'});
  await insert(db, 'gwangju', '2026-01-01', {wa_laf_hg_nm: '광주광역시'});
  await insert(db, 'jeonnam', '2026-01-01', {wa_laf_hg_nm: '전라남도'});
  await insert(db, 'live', '2026-01-01', {}, {}, {mode: 'live'});
  await insert(db, 'aidfa', '2026-01-01', {}, {}, {service: 'AIDFA'});
  await insert(db, 'next-year', '2027-01-01', {}, {}, {year: 2027});
  const orgKey = JSON.stringify(['code', '11', 'ORG-1']), deptKey = JSON.stringify(['code', 'D1']);
  for (const [filter, count] of [
    [{}, 6], [{mode: 'live'}, 1], [{year: '2027', from: '2027-01-01', to: '2027-12-31'}, 1],
    [{orgKey, deptKey}, 4], [{region: '서울특별시', org: '서울특별시 중구', dept: '도로과'}, 2],
    [{q: '시립기관 문화 체육'}, 1], [{q: '100%_\\'}, 1], [{region: '전남광주통합특별시'}, 2],
    [{region: '인천광역시', incheonScope: 'INCHEON_IFEZ'}, 1],
    [{orgKey: JSON.stringify(['base', '서울특별시', '중구'])}, 2], [{category: 'LIGHTING'}, 6],
  ]) assert.equal((await budgetObservationSearch(params(filter))).rows.length, count, JSON.stringify(filter));
  const result = await budgetObservationSearch(params({region: '전남광주통합특별시'}));
  assert.deepEqual(result.rows.map(row => row.region).sort(), ['광주광역시', '전라남도']);
});

test('epoch change during page/baseline read rejects partial results; unrelated mode is isolated', async t => {
  let changed = false;
  const {db} = setup(t, (sql, database) => {
    if (sql.includes('evidence AS') && !changed) { changed = true; database.exec("UPDATE budget_observation_epochs SET revision=revision+1 WHERE mode='synthetic'"); }
  });
  await insert(db, 'one', '2026-01-01');
  await assert.rejects(budgetObservationSearch(params()), /BUDGET_OBSERVATION_SOURCE_CHANGED_RETRY/);
  const first = await budgetObservationSearch(params());
  await insert(db, 'live', '2026-01-01', {}, {}, {mode: 'live'});
  assert.equal((await budgetObservationSearch(params())).sourceRevision, first.sourceRevision);
});

test('UTF-8 page byte bound continues without dropping records; one oversized row fails explicitly', async t => {
  const {db} = setup(t);
  for (let i = 0; i < 18; i++) await insert(db, 'bytes-' + i, '2026-01-01', {}, {note: '한'.repeat(30000)});
  const seen = new Set(); let cursor, pages = 0;
  do {
    const result = await budgetObservationSearch(params(cursor ? {cursor} : {})); pages++;
    for (const row of result.rows) seen.add(row.source_key); cursor = result.next;
  } while (cursor);
  assert.equal(pages, 2); assert.equal(seen.size, 18);
  await insert(db, 'too-large', '2026-01-02', {}, {note: '한'.repeat(400000)});
  await assert.rejects(budgetObservationSearch(params()), /BUDGET_OBSERVATION_RECORD_TOO_LARGE/);
});

test('bounded baseline overflow fails rather than returning a made-up delta', async t => {
  const {db} = setup(t);
  await insert(db, 'huge-base', '2026-01-01', {}, {budget_amount: '9'.repeat(OBSERVATION_BASELINE_BYTES + 1)});
  await insert(db, 'huge-base', '2026-01-02', {ep_amt: '2'});
  await assert.rejects(budgetObservationSearch(params({from: '2026-01-02'})), /BUDGET_OBSERVATION_BASELINE_TOO_LARGE/);
});

test('query planner uses indexed date-page range and bounded baseline lookups, not global observation windows', async t => {
  const {db} = setup(t);
  const row = await insert(db, 'one', '2026-01-01');
  const plan = budgetObservationPlan(params(), '2026-11-07'), query = budgetObservationPage(plan);
  const explain = db.prepare('EXPLAIN QUERY PLAN ' + query.sql).all(...query.args).map(row => row.detail).join('\n');
  assert.match(explain, /idx_budget_observation_page/);
  assert.doesNotMatch(query.sql, /PARTITION BY|\bLAG\(|\bOFFSET\b/i);
  assert.match(query.sql, /LIMIT 51/);
  const baseline = budgetObservationBaselines(plan, [{...row, snapshot_date: '2026-01-02'}]);
  const baselineExplain = db.prepare('EXPLAIN QUERY PLAN ' + baseline.sql).all(...baseline.args).map(row => row.detail).join('\n');
  assert.match(baselineExplain, /idx_budget_observation_baseline/);
  assert.match(baselineExplain, /SEARCH r USING INDEX sqlite_autoindex_budget_revisions/);
  assert.doesNotMatch(baselineExplain, /SCAN budget_observations|SCAN r\b|SCAN o\b/);
  assert.match(baseline.sql, /LIMIT 2/);
});

test('invalid inputs, changed filters and cursor edits fail closed with no provider/key dependency', async t => {
  const {db} = setup(t); await insert(db, 'one', '2026-01-01'); await insert(db, 'two', '2026-01-01');
  for (const extra of [{mode: 'other'}, {service: 'AIDFA'}, {year: '2025'}, {from: '2026-02-30'}, {from: '2026-03-01', to: '2026-02-01'}, {limit: '101'}, {limit: '0'}, {offset: '50'}, {sort: 'BUDGET'}, {view: 'other'}, {q: 'x'.repeat(101)}, {q: 'a b c d e f g h i'}, {deptKey: '["code","D1"]'}]) await assert.rejects(budgetObservationSearch(params(extra)));
  const duplicated = params(); duplicated.append('mode', 'live'); await assert.rejects(budgetObservationSearch(duplicated), /DUPLICATE_OBSERVATION_FILTER/);
  const first = await budgetObservationSearch(params({limit: '1'}));
  await assert.rejects(budgetObservationSearch(params({cursor: first.next, q: 'changed'})), /INVALID_OBSERVATION_CURSOR/);
  const malformed = JSON.parse(first.next); malformed.hash = 'x';
  await assert.rejects(budgetObservationSearch(params({cursor: JSON.stringify(malformed)})), /INVALID_OBSERVATION_CURSOR/);
  const response = await GET(new Request('https://example.test/api/budget/observations?' + params({cursor: first.next, q: 'changed'})));
  assert.equal(response.status, 400); assert.equal(response.headers.get('Cache-Control'), 'no-store');
  const success = await GET(new Request('https://example.test/api/budget/observations?' + params()));
  assert.equal(success.status, 200); assert.equal((await success.json()).providerCalls, 0);
  await insert(db, 'three', '2026-01-01');
  const stale = await GET(new Request('https://example.test/api/budget/observations?' + params({cursor: first.next})));
  assert.equal(stale.status, 409);
});

test('delta helper preserves current zero, suppresses invalid prior numeric input and keeps null fields null', () => {
  const row = {budget_amount: '0', appropriation_amount: '900', executed_amount: null, remaining_amount: null, invalid_amount: 0};
  const previous_json = JSON.stringify([{first_snapshot: '2026-01-01', payload_hash: 'x', amounts: {budget_amount: '100', executed_amount: '1', remaining_amount: '99', invalid_amount: 0}}]);
  const out = budgetObservationEnrich(row, {same_date_ambiguous: 0, previous_json}, '2026-01-02');
  assert.equal(out.effective_budget_amount, '0'); assert.equal(out.budget_change, '-100'); assert.equal(out.executed_change, null);
  const malformed = JSON.stringify([{first_snapshot: '2026-01-01', amounts: {budget_amount: 10, invalid_amount: 0}}]);
  assert.equal(budgetObservationEnrich(row, {same_date_ambiguous: 0, previous_json: malformed}, '2026-01-02').delta_status, 'INVALID_AMOUNT');
});


test('an absent dedicated epoch is virtual zero, including existing historical data before migration', async t => {
  const {db} = setup(t);
  db.exec("DELETE FROM budget_observation_epochs");
  const empty = await budgetObservationSearch(params());
  assert.equal(empty.sourceRevision, '0'); assert.deepEqual(empty.rows, []);
  await insert(db, 'legacy', '2026-01-01', {}, {}, {legacyOnly: true});
  db.exec("DELETE FROM budget_observation_epochs");
  const existing = await budgetObservationSearch(params());
  assert.equal(existing.sourceRevision, '0'); assert.equal(existing.rows.length, 0);
  await insert(db, 'new', '2026-01-02');
  assert.equal((await budgetObservationSearch(params())).sourceRevision, '1');
});


test('different-day A→B→A and date-only repeats survive; observation dates override unique-state first_snapshot', async t => {
  const {db} = setup(t);
  const a = await insert(db, 'ABA', '2026-01-05', {ep_amt: '10'}, {}, {seen: '2026-02-01T00:00:00Z'});
  const b = await insert(db, 'ABA', '2026-01-06', {ep_amt: '20'});
  await insert(db, 'ABA', '2026-01-07', {ep_amt: '10'}, {}, {seen: '2026-02-02T00:00:00Z', lastSeen: '2026-02-02T02:00:00Z'});
  await insert(db, 'ABA', '2026-01-08', {ep_amt: '10'});
  await insert(db, 'ABA', '2026-01-02', {ep_amt: '10'}); // Later-discovered earlier observation.
  assert.equal(db.prepare('SELECT COUNT(*) n FROM budget_revisions').get().n, 2);
  assert.equal(db.prepare('SELECT COUNT(*) n FROM budget_observations').get().n, 5);
  assert.equal(db.prepare('SELECT first_snapshot FROM budget_revisions WHERE payload_hash=?').get(a.payload_hash).first_snapshot, '2026-01-05');
  const result = await budgetObservationSearch(params());
  assert.deepEqual(result.rows.map(row => row.snapshot_date), ['2026-01-08', '2026-01-07', '2026-01-06', '2026-01-05', '2026-01-02']);
  assert.deepEqual(result.rows.map(row => row.executed_change), ['0', '-10', '10', '0', null]);
  assert.equal(result.rows[1].previous_payload_hash, b.payload_hash);
  assert.equal(result.rows[1].observation_first_seen, '2026-02-02T00:00:00Z');
  assert.equal(result.rows[1].observation_last_seen, '2026-02-02T02:00:00Z');
  assert.ok(result.rows.every(row => !Object.hasOwn(row, 'first_snapshot') && !Object.hasOwn(row, 'previous_first_snapshot') && !Object.hasOwn(row, 'raw')));
  assert.equal(result.completeDailyPresence, false); assert.equal(result.completeIntradayTimeline, false);
  assert.equal(result.crossDateRepeatedStatesPreserved, true); assert.equal(result.sameDateSameStateDeduplicated, true);
  assert.equal(result.legacyUniqueStatesBackfilled, false);
  assert.equal((await budgetObservationSearch(params({from: '2026-01-07', to: '2026-01-07'}))).rows[0].executed_change, '-10');
  const observed = []; let cursor;
  do {
    const page = await budgetObservationSearch(params({limit: '1', ...(cursor ? {cursor} : {})}));
    observed.push(...page.rows.map(row => row.snapshot_date)); cursor = page.next;
  } while (cursor);
  assert.deepEqual(observed, result.rows.map(row => row.snapshot_date));
});

test('same-day repeated hash collapses without claiming every intraday transition, and hidden ties suppress deltas', async t => {
  const {db} = setup(t);
  await insert(db, 'intraday', '2026-01-01', {ep_amt: '1'});
  await insert(db, 'intraday', '2026-01-02', {ep_amt: '2', dbiz_nm: 'visible 조명'});
  await insert(db, 'intraday', '2026-01-02', {ep_amt: '3', dbiz_nm: 'hidden state'});
  await insert(db, 'intraday', '2026-01-02', {ep_amt: '2', dbiz_nm: 'visible 조명'});
  await insert(db, 'intraday', '2026-01-03', {ep_amt: '4', dbiz_nm: 'visible 조명'});
  assert.equal(db.prepare('SELECT COUNT(*) n FROM budget_observations').get().n, 4);
  const result = await budgetObservationSearch(params({q: 'visible'}));
  assert.equal(result.rows.length, 2);
  assert.deepEqual(result.rows.map(row => row.delta_status), ['AMBIGUOUS_PREVIOUS_DATE', 'AMBIGUOUS_CURRENT_DATE']);
  assert.ok(result.rows.every(row => row.executed_change === null));
  assert.equal(result.rows[0].previous_payload_hash, null);
});

test('observation mutations and referenced revision edits invalidate cursors while untouched revisions do not', async t => {
  const {db} = setup(t);
  const one = await insert(db, 'one', '2026-01-01');
  await insert(db, 'two', '2026-01-01');
  const first = await budgetObservationSearch(params({limit: '1'}));
  const legacy = await insert(db, 'legacy-only', '2026-01-01', {}, {}, {legacyOnly: true});
  db.prepare("UPDATE budget_revisions SET first_seen='changed' WHERE payload_hash=?").run(legacy.payload_hash);
  assert.equal((await budgetObservationSearch(params({limit: '1', cursor: first.next}))).sourceRevision, first.sourceRevision);
  db.prepare("UPDATE budget_revisions SET row_json=json_set(row_json,'$.budget_amount','9999') WHERE payload_hash=?").run(one.payload_hash);
  await assert.rejects(budgetObservationSearch(params({cursor: first.next})), /BUDGET_OBSERVATION_SOURCE_CHANGED_RETRY/);
  const next = await budgetObservationSearch(params({limit: '1'}));
  db.prepare("UPDATE budget_observations SET last_seen='2026-02-01T01:00:00Z' WHERE payload_hash=?").run(one.payload_hash);
  await assert.rejects(budgetObservationSearch(params({cursor: next.next})), /BUDGET_OBSERVATION_SOURCE_CHANGED_RETRY/);
  const last = await budgetObservationSearch(params({limit: '1'}));
  db.prepare('DELETE FROM budget_observations WHERE payload_hash=?').run(one.payload_hash);
  await assert.rejects(budgetObservationSearch(params({cursor: last.next})), /BUDGET_OBSERVATION_SOURCE_CHANGED_RETRY/);
});

test('source fence rejects a referenced revision changing between the page and baseline reads', async t => {
  let changed = false;
  const {db} = setup(t, (sql, database) => {
    if (sql.includes('picked AS') && !changed) {
      changed = true;
      database.exec("UPDATE budget_revisions SET row_json=json_set(row_json,'$.executed_amount','888') WHERE mode='synthetic'");
    }
  });
  await insert(db, 'race', '2026-01-01');
  await insert(db, 'race', '2026-01-02', {ep_amt: '200'});
  await assert.rejects(budgetObservationSearch(params()), /BUDGET_OBSERVATION_SOURCE_CHANGED_RETRY/);
});


test('key-filtered first and continuation pages seek the exact project before its date range', async t => {
  const {db} = setup(t);
  const row = await insert(db, 'target', '2026-01-10');
  const plan = budgetObservationPlan(params({key: row.source_key}), '2026-11-07');
  for (const cursor of [null, {date: '2026-01-10', key: row.source_key, hash: row.payload_hash}]) {
    const query = budgetObservationPage(plan, cursor);
    const explain = db.prepare('EXPLAIN QUERY PLAN ' + query.sql).all(...query.args).map(row => row.detail).join('\n');
    assert.match(explain, /SEARCH o USING INDEX idx_budget_observation_baseline \(mode=\? AND service=\? AND source_key=\? AND snapshot_date[<>]=?\?/);
    assert.doesNotMatch(explain, /idx_budget_observation_page|SCAN o\b|SCAN budget_observations|SCAN r\b/);
    assert.match(explain, /SEARCH r USING INDEX sqlite_autoindex_budget_revisions/);
    assert.match(query.sql, /LIMIT 51/);
  }
  const broad = budgetObservationPage(budgetObservationPlan(params(), '2026-11-07'));
  const broadExplain = db.prepare('EXPLAIN QUERY PLAN ' + broad.sql).all(...broad.args).map(row => row.detail).join('\n');
  assert.match(broadExplain, /SEARCH o USING INDEX idx_budget_observation_page/);
  assert.doesNotMatch(broadExplain, /idx_budget_observation_baseline/);
});

test('project-only observation paging preserves dates, hashes, filters, baselines and mode isolation', async t => {
  const {db} = setup(t);
  for (let i = 0; i < 240; i++) await insert(db, 'unrelated-' + i, '2026-01-07');
  let target;
  for (let day = 1; day <= 17; day++) target = await insert(db, 'target', '2026-01-' + String(day).padStart(2, '0'), {ep_amt: String(day)});
  await insert(db, 'target', '2026-01-06', {ep_amt: '60'});
  await insert(db, 'target', '2026-01-09', {ep_amt: '900'}, {}, {mode: 'live'});
  const from = '2026-01-03', to = '2026-01-10';
  const expected = db.prepare("SELECT source_key,snapshot_date,payload_hash FROM budget_observations WHERE mode='synthetic' AND service='QWGJK' AND fiscal_year=2026 AND source_key=? AND snapshot_date BETWEEN ? AND ? ORDER BY snapshot_date DESC,source_key,payload_hash").all(target.source_key, from, to);
  const filters = {key: target.source_key, from, to, limit: '3', q: 'target', region: '서울특별시', orgKey: '["code","11","ORG-1"]', deptKey: '["code","D1"]'};
  const rows = []; let cursor, pages = 0;
  do {
    const result = await budgetObservationSearch(params({...filters, ...(cursor ? {cursor} : {})}));
    rows.push(...result.rows); cursor = result.next; pages++;
    assert.ok(result.rows.length <= 3); assert.ok(pages <= 3);
  } while (cursor);
  const identity = row => [row.source_key, row.snapshot_date, row.payload_hash].join('|');
  assert.equal(pages, 3); assert.equal(rows.length, 9);
  assert.deepEqual(rows.map(identity), expected.map(identity));
  assert.equal(new Set(rows.map(identity)).size, rows.length);
  assert.ok(rows.every(row => row.source_key === target.source_key && row.snapshot_date >= from && row.snapshot_date <= to));
  const firstInRange = rows.find(row => row.snapshot_date === from);
  assert.equal(firstInRange.previous_snapshot_date, '2026-01-02');
  assert.equal(firstInRange.baseline_before_start, true); assert.equal(firstInRange.executed_change, '1');
  assert.ok(rows.filter(row => row.snapshot_date === '2026-01-06').every(row => row.delta_status === 'AMBIGUOUS_CURRENT_DATE'));
  assert.equal(rows.find(row => row.snapshot_date === '2026-01-07').delta_status, 'AMBIGUOUS_PREVIOUS_DATE');
  const empty = await budgetObservationSearch(params({key: 'does-not-exist'}));
  assert.deepEqual(empty.rows, []); assert.equal(empty.next, null);
});
