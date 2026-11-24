import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync, readdirSync, existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {registerHooks} from 'node:module';
import {budgetStructurePlan, budgetStructureResult, STRUCTURE_PAGE_LIMIT, STRUCTURE_ROW_BYTES, STRUCTURE_PAGE_BYTES} from '../lib/budget-structure.mjs';

const env = {DB: null};
globalThis.__g2bStructureEnv = new Proxy(env, {get(target, key) {
  if (key === 'G2B_PUBLIC_AUTH_ENABLED') return undefined;
  if (key !== 'DB') throw Error('STRUCTURE_MUST_NOT_READ_CONFIG_OR_SECRETS');
  return target.DB;
}});
registerHooks({resolve(spec, ctx, next) {
  if (spec === 'cloudflare:workers') return {url: 'data:text/javascript,export const env=globalThis.__g2bStructureEnv', shortCircuit: true};
  if (spec.startsWith('.') && !/\.(ts|mjs|js)$/.test(spec) && ctx.parentURL?.startsWith('file:')) {
    const url = new URL(spec + '.ts', ctx.parentURL);
    if (existsSync(fileURLToPath(url))) return next(url.href, ctx);
  }
  return next(spec, ctx);
}});
const {budgetStructure} = await import('../lib/budget-structure-service.ts');
const {GET} = await import('../app/api/budget/structure/route.ts');
const {normalizeBudget} = await import('../lib/budget-domain.mjs');
const params = (key, extra = {}) => new URLSearchParams({mode: 'synthetic', key, ...extra});

function setup(t, hook = () => {}) {
  const db = new DatabaseSync(':memory:');
  for (const file of readdirSync(new URL('../drizzle/', import.meta.url)).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL('../drizzle/' + file, import.meta.url), 'utf8'));
  const reads = [];
  env.DB = {prepare(sql) {
    assert.match(sql, /^\s*(WITH|SELECT)\b/);
    assert.doesNotMatch(sql, /\b(budget_revisions|budget_sessions|source_usage)\b/i);
    let args = [];
    const query = {bind(...values) {args = values; return query;}, async first() {
      const rows = db.prepare(sql).all(...args);
      assert.ok(rows.length <= 1, 'Source identity and epoch reads are single-row');
      reads.push({sql, args, count: rows.length});
      hook(sql, db, reads);
      return rows[0] ?? null;
    }, async all() {
      assert.match(sql, /ORDER BY source_key COLLATE BINARY ASC LIMIT \d+\), framed AS/);
      const candidateLimit = Number(sql.match(/LIMIT (\d+)\), framed AS/)[1]);
      assert.ok(candidateLimit <= STRUCTURE_PAGE_LIMIT + 1);
      const rows = db.prepare(sql).all(...args);
      assert.ok(rows.length <= STRUCTURE_PAGE_LIMIT, 'Never transfer all projects');
      const bytes = rows.reduce((total, row) => total + Buffer.byteLength(row.row_json || '', 'utf8'), 0);
      assert.ok(bytes <= STRUCTURE_PAGE_BYTES, 'D1 bounds total row_json bytes before Worker parsing');
      reads.push({sql, args, count: rows.length, bytes});
      hook(sql, db, reads);
      return {results: rows};
    }, async run() {throw Error('STRUCTURE_MUST_NOT_WRITE');}};
    return query;
  }, async batch() {throw Error('STRUCTURE_MUST_NOT_WRITE');}};
  const fetch = globalThis.fetch;
  globalThis.fetch = () => {throw Error('STRUCTURE_MUST_NOT_CALL_PROVIDER');};
  t.after(() => {db.close(); env.DB = null; globalThis.fetch = fetch;});
  return {db, reads};
}

async function insert(db, key, patch = {}, storage = {}, extra = {}) {
  const service = storage.service || 'QWGJK', year = storage.year || 2026;
  const raw = {
    fyr: String(year), wa_laf_cd: '11', wa_laf_hg_nm: '서울특별시', laf_cd: 'ORG-1', laf_hg_nm: '서울특별시 중구',
    dept_cd: 'D1', dept_nm: '도로과', dbiz_cd: key, dbiz_nm: '합성 조명 ' + key,
    fld_cd: 'F1', fld_nm: '교통 및 물류', sect_cd: 'S1', sect_nm: '도로', acnt_dv_cd: '01', acnt_dv_nm: '일반회계',
    bdg_cash_amt: '1000', cpl_amt: '900', ep_amt: '100', biz_bdg_tott_amt: '999999', ...patch,
  };
  const row = {...await normalizeBudget(raw, {service, fiscal_year: year, snapshot_date: year + '-11-01'}), ...extra};
  db.prepare('INSERT INTO budget_rows(mode,service,source_key,payload_hash,snapshot_date,fiscal_year,org,region,dept,project,category,row_json,last_session,is_active,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').run(storage.mode || 'synthetic', service, row.source_key, row.payload_hash, storage.snapshot || year + '-11-01', year, row.org, row.region, row.dept, row.project, row.category, JSON.stringify(row), 'fixture', storage.active ?? 1, '2026-11-07T00:00:00Z');
  return row;
}
const appropriation = (db, patch = {}, storage = {}, extra = {}) => insert(db, 'AIDFA', patch, {...storage, service: 'AIDFA'}, extra);

test('all exact codes link despite differing labels and preserve identities and every evidence dimension', async t => {
  const {db, reads} = setup(t);
  const source = await appropriation(db);
  const project = await insert(db, 'EXACT', {fld_nm: '다른 분야명', sect_nm: '다른 부문명', acnt_dv_nm: '다른 회계명', laf_hg_nm: '기관 별칭'}, {snapshot: '2026-11-06'});
  const result = await budgetStructure(params(source.source_key));
  assert.equal(result.rows.length, 1);
  const row = result.rows[0];
  assert.equal(row.source_key, project.source_key);
  assert.equal(row.payload_hash, project.payload_hash);
  assert.equal(row.appropriation_source_key, source.source_key);
  assert.equal(row.snapshot_date, '2026-11-06');
  assert.equal(row.updated_at, '2026-11-07T00:00:00Z');
  assert.equal(row.raw, undefined);
  assert.equal(row.dept_code, 'D1');
  for (const field of ['org_code', 'field', 'section', 'account']) assert.equal(row.match_evidence[field].basis, 'CODE');
  assert.deepEqual(row.match_evidence.fiscal_year, {basis: 'EXACT', appropriation: 2026, detail: 2026});
  assert.deepEqual(row.match_evidence.field.appropriation_ref, {code: 'appropriation.field_code', name: 'appropriation.field_name'});
  assert.equal(result.appropriation.field_name, '교통 및 물류');
  assert.equal(row.match_evidence.org_code.appropriation_ref, 'appropriation.org_code');
  assert.equal(row.match_evidence.field.detail_name, '다른 분야명');
  assert.equal(result.appropriation.service, 'AIDFA');
  assert.equal(result.detailService, 'QWGJK');
  assert.equal(result.sourceCompletenessVerified, false);
  assert.equal(result.providerCalls, 0);
  assert.equal(result.source_io_performed, false);
  assert.equal(result.sourceRevision, '0');
  assert.equal(result.next, null);
  assert.equal(result.linkable, true);
  assert.deepEqual(result.missingDimensions, []);
  assert.equal(reads.length, 4);
});

test('a conflicting code in any dimension NEVER falls back to the same name', async t => {
  const {db} = setup(t), source = await appropriation(db);
  await insert(db, 'field-conflict', {fld_cd: 'F2'});
  await insert(db, 'section-conflict', {sect_cd: 'S2'});
  await insert(db, 'account-conflict', {acnt_dv_cd: '02'});
  await insert(db, 'all-conflict', {fld_cd: 'F2', sect_cd: 'S2', acnt_dv_cd: '02'});
  assert.equal((await budgetStructure(params(source.source_key))).rows.length, 0);
});

test('missing codes on either side permit only nonempty exact trimmed names', async t => {
  const {db} = setup(t);
  const source = await appropriation(db, {fld_cd: '', sect_cd: ' ', acnt_dv_cd: null, fld_nm: '\u00a0교통 및 물류\u3000'});
  await insert(db, 'detail-coded');
  await insert(db, 'both-codeless', {fld_cd: '', sect_cd: '', acnt_dv_cd: ''});
  await insert(db, 'internal-whitespace-different', {fld_nm: '교통및물류'});
  await insert(db, 'fuzzy-name', {sect_nm: '도로 시설'});
  await insert(db, 'empty-name', {acnt_dv_nm: ''});
  const result = await budgetStructure(params(source.source_key));
  assert.equal(result.rows.length, 2);
  for (const row of result.rows) for (const d of ['field', 'section', 'account']) assert.equal(row.match_evidence[d].basis, 'NAME');
  // Reverse direction: the appropriation has codes, projects do not.
  const coded = await appropriation(db, {fld_cd: 'F9'});
  const reverse = await budgetStructure(params(coded.source_key));
  assert.equal(reverse.rows.length, 1);
  assert.equal(reverse.rows[0].project_code, 'both-codeless');
});

test('blank alias codes fall through explicitly supported aliases, numeric codes remain strings', async t => {
  const {db} = setup(t);
  const patch = {fld_cd: '\t ', field_code: 123, sect_cd: '', part_cd: 456, acnt_dv_cd: 0, dept_cd: '', departmentCode: 789};
  const source = await appropriation(db, patch);
  await insert(db, 'aliases', patch);
  const {rows} = await budgetStructure(params(source.source_key));
  assert.equal(rows.length, 1);
  assert.equal(rows[0].field_code, '123');
  assert.equal(rows[0].section_code, '456');
  assert.equal(rows[0].account_code, '0');
  assert.equal(rows[0].dept_code, '789');
  assert.equal(rows[0].match_evidence.account.basis, 'CODE');
});

test('missing org codes never use normalized regional fallback or names; missing dimensions stay unlinked', async t => {
  const {db, reads} = setup(t);
  const source = await appropriation(db, {laf_cd: ''});
  assert.equal(source.org_code, '11', 'Existing normalization has regional fallback');
  await insert(db, 'also-no-org', {laf_cd: ''});
  await insert(db, 'region-as-org', {laf_cd: '11'});
  const result = await budgetStructure(params(source.source_key));
  assert.deepEqual(result.missingDimensions, ['org_code']);
  assert.equal(result.appropriation.org_code, '');
  assert.equal(result.linkable, false);
  assert.equal(result.rows.length, 0);
  assert.equal(reads.length, 3, 'No detail query when source cannot link');
  const coded = await appropriation(db);
  assert.equal((await budgetStructure(params(coded.source_key))).rows.length, 0);
  const noField = await appropriation(db, {fld_cd: '', fld_nm: ''});
  assert.deepEqual((await budgetStructure(params(noField.source_key))).missingDimensions, ['field']);
  await insert(db, 'missing-section', {sect_cd: '', sect_nm: ''});
  assert.equal((await budgetStructure(params(coded.source_key))).rows.length, 0);
});

test('same org/year/mode and current active QWGJK only; dates do not imply historical snapshots', async t => {
  const {db} = setup(t), source = await appropriation(db);
  await insert(db, 'current');
  await insert(db, 'other-org', {laf_cd: 'ORG-2'});
  await insert(db, 'other-year', {}, {year: 2027});
  await insert(db, 'inactive', {}, {active: 0});
  await insert(db, 'live', {}, {mode: 'live'});
  await appropriation(db, {fld_cd: 'unrelated'});
  assert.deepEqual((await budgetStructure(params(source.source_key))).rows.map(r => r.project_code), ['current']);
  await assert.rejects(budgetStructure(params(source.source_key, {mode: 'live'})), /BUDGET_STRUCTURE_NOT_FOUND/);
  const live = await appropriation(db, {}, {mode: 'live'});
  assert.deepEqual((await budgetStructure(params(live.source_key, {mode: 'live'}))).rows.map(r => r.project_code), ['live']);
  await assert.rejects(budgetStructure(params(source.source_key, {year: '2027'})), /BUDGET_STRUCTURE_YEAR_MISMATCH/);
  db.prepare("UPDATE budget_rows SET is_active=0 WHERE mode='synthetic' AND service='AIDFA' AND source_key=?").run(source.source_key);
  await assert.rejects(budgetStructure(params(source.source_key)), /BUDGET_STRUCTURE_NOT_FOUND/);
});

test('money strings beyond int64, null/zero and invalid metadata are unchanged, never structural substitutes', async t => {
  const {db} = setup(t), source = await appropriation(db, {biz_bdg_tott_amt: '9'.repeat(70)});
  const big = await insert(db, 'big', {bdg_cash_amt: '90071992547409931234567890', ep_amt: '0'});
  await insert(db, 'missing', {bdg_cash_amt: '', cpl_amt: '', ep_amt: ''});
  await insert(db, 'invalid', {bdg_cash_amt: 'bad-value', ep_amt: '0'});
  const result = await budgetStructure(params(source.source_key));
  assert.equal(result.appropriation.budget_amount, '9'.repeat(70));
  assert.equal(result.appropriation.executed_amount, null);
  const byCode = Object.fromEntries(result.rows.map(r => [r.project_code, r]));
  for (const field of ['budget_amount', 'appropriation_amount', 'executed_amount', 'remaining_amount', 'invalid_amount']) assert.equal(byCode.big[field], big[field]);
  assert.equal(byCode.missing.budget_amount, null);
  assert.equal(byCode.missing.executed_amount, null);
  assert.equal(byCode.missing.remaining_amount, null);
  assert.equal(byCode.invalid.invalid_amount, 1);
  assert.equal(byCode.invalid.budget_amount, null);
  assert.equal(byCode.invalid.executed_amount, '0');
  assert.equal(byCode.invalid.remaining_amount, null);
  assert.equal(result.amountSemantics, 'AIDFA_STRUCTURE_TOTAL_IS_NOT_QWGJK_PROJECT_AMOUNT');
});

test('100-row bound and source-key pagination traverse 237 exact links without duplicates or all-row transfer', async t => {
  const {db, reads} = setup(t), source = await appropriation(db);
  for (let i = 0; i < 237; i++) await insert(db, 'project-' + String(i).padStart(4, '0'));
  let cursor, total = 0;
  const seen = new Set(), sizes = [];
  do {
    const result = await budgetStructure(params(source.source_key, cursor ? {cursor} : {}));
    sizes.push(result.rows.length);
    assert.equal(result.limit, 100);
    for (const row of result.rows) { assert.ok(!seen.has(row.source_key)); seen.add(row.source_key); total++; }
    cursor = result.next;
  } while (cursor);
  assert.equal(total, 237);
  assert.deepEqual(sizes, [100, 100, 37]);
  assert.deepEqual(reads.filter(r => r.sql.includes("service='QWGJK'")).map(r => r.count), [100, 100, 37]);
  assert.ok(reads.every(r => !/\bOFFSET\b|SELECT \*/.test(r.sql)));
  const first = await budgetStructure(params(source.source_key, {limit: '1'}));
  assert.equal(first.rows.length, 1);
  assert.equal(first.hasMore, true);
  assert.equal((await budgetStructure(params(source.source_key, {limit: '2', cursor: first.next}))).rows.length, 2);
});

test('epochs fence each source/detail read and continuation, including revisions above JS-safe integers', async t => {
  for (const phase of ['epoch', 'source', 'details']) {
    let changed = false;
    const {db} = setup(t, sql => {
      const trigger = phase === 'epoch' ? sql.startsWith('SELECT CAST(revision') : sql.includes(`service='${phase === 'source' ? 'AIDFA' : 'QWGJK'}'`);
      if (trigger && !changed) { changed = true; db.exec("UPDATE budget_epochs SET revision=revision+1 WHERE mode='synthetic'"); }
    });
    const source = await appropriation(db);
    await insert(db, '1');
    await assert.rejects(budgetStructure(params(source.source_key)), /BUDGET_STRUCTURE_SOURCE_CHANGED_RETRY/);
  }
  const {db} = setup(t), source = await appropriation(db);
  for (const key of ['1', '2']) await insert(db, key);
  db.exec("UPDATE budget_epochs SET revision=9007199254740993 WHERE mode='synthetic'");
  const first = await budgetStructure(params(source.source_key, {limit: '1'}));
  assert.equal(first.sourceRevision, '9007199254740993');
  db.exec("UPDATE budget_epochs SET revision=revision+1 WHERE mode='live'");
  assert.equal((await budgetStructure(params(source.source_key, {cursor: first.next}))).rows.length, 1);
  db.exec("UPDATE budget_epochs SET revision=revision+1 WHERE mode='synthetic'");
  await assert.rejects(budgetStructure(params(source.source_key, {cursor: first.next})), /BUDGET_STRUCTURE_SOURCE_CHANGED_RETRY/);
});

test('scope-bound cursors, input validation and missing sources fail explicitly before broad reads', async t => {
  const {db, reads} = setup(t);
  for (const extra of [{key: ''}, {mode: 'typo'}, {service: 'QWGJK'}, {year: '2025'}, {year: '2026x'}, {limit: '101'}, {limit: '0'}, {limit: '1.1'}, {cursor: '{}'}, {cursor: 'null'}, {cursor: 'bad'}]) await assert.rejects(budgetStructure(params('key', extra)));
  assert.equal(reads.length, 0);
  const source = await appropriation(db);
  await insert(db, '1'); await insert(db, '2');
  const first = await budgetStructure(params(source.source_key, {limit: '1'}));
  for (const patch of [{mode: 'live'}, {sourceKey: 'other-source'}, {afterKey: ''}, {revision: 1}, {v: 2}]) {
    const cursor = JSON.stringify({...JSON.parse(first.next), ...patch});
    await assert.rejects(budgetStructure(params(source.source_key, {cursor})), /INVALID_BUDGET_STRUCTURE_CURSOR/);
  }
  await assert.rejects(budgetStructure(params("' OR 1=1 --")), /BUDGET_STRUCTURE_NOT_FOUND/);
  db.exec("DELETE FROM budget_epochs WHERE mode='synthetic'");
  await assert.rejects(budgetStructure(params(source.source_key)), /BUDGET_STRUCTURE_SOURCE_UNAVAILABLE/);
});

test('SQL removes raw before transfer and fails closed on oversized public rows', async t => {
  const {db} = setup(t), source = await appropriation(db, {irrelevant_raw: 'x'.repeat(STRUCTURE_ROW_BYTES + 1)});
  await insert(db, '1', {irrelevant_raw: 'x'.repeat(STRUCTURE_ROW_BYTES + 1)});
  const result = await budgetStructure(params(source.source_key));
  assert.equal(result.rows.length, 1);
  assert.ok(JSON.stringify(result).length < 10000);
  await insert(db, '2', {}, {}, {unrecognized_extra: 'x'.repeat(STRUCTURE_ROW_BYTES + 1)});
  const first = await budgetStructure(params(source.source_key));
  assert.equal(first.rows.length, 1);
  assert.equal(first.hasMore, true);
  await assert.rejects(budgetStructure(params(source.source_key, {cursor: first.next})), /BUDGET_STRUCTURE_ROW_TOO_LARGE/);
});

test('wide UTF-8 rows stop at the total byte budget and paginate every matching identity exactly once', async t => {
  const {db, reads} = setup(t), source = await appropriation(db);
  // These are all individually valid, but only three fit in each 1 MiB page.
  const padding = '가'.repeat(90000), expected = [];
  for (let i = 0; i < 11; i++) expected.push((await insert(db, 'wide-' + String(i).padStart(2, '0'), {}, {}, {stored_note: padding})).source_key);
  db.exec("UPDATE budget_epochs SET revision=9007199254740993 WHERE mode='synthetic'");
  let cursor = null;
  const seen = [], sizes = [];
  do {
    const result = await budgetStructure(params(source.source_key, cursor ? {cursor} : {}));
    assert.equal(result.maxPageBytes, STRUCTURE_PAGE_BYTES);
    assert.equal(result.sourceRevision, '9007199254740993');
    sizes.push(result.rows.length);
    for (const row of result.rows) {
      seen.push(row.source_key);
      assert.equal(row.stored_note, padding);
      assert.equal(row.match_evidence.field.basis, 'CODE');
    }
    if (result.hasMore) {
      assert.ok(result.rows.length < result.limit, 'Byte-short pages still provide continuation');
      const token = JSON.parse(result.next);
      assert.equal(token.afterKey, result.rows.at(-1).source_key);
      assert.equal(token.sourceKey, source.source_key);
      assert.equal(token.revision, '9007199254740993');
    }
    cursor = result.next;
  } while (cursor);
  assert.deepEqual(seen, expected);
  assert.equal(new Set(seen).size, 11);
  assert.deepEqual(sizes, [3, 3, 3, 2]);
  const detailReads = reads.filter(r => r.sql.includes("service='QWGJK'"));
  assert.deepEqual(detailReads.map(r => r.count), sizes);
  assert.ok(detailReads.every(r => r.bytes <= STRUCTURE_PAGE_BYTES));
  const first = await budgetStructure(params(source.source_key));
  db.exec("UPDATE budget_epochs SET revision=revision+1 WHERE mode='synthetic'");
  await assert.rejects(budgetStructure(params(source.source_key, {cursor: first.next})), /BUDGET_STRUCTURE_SOURCE_CHANGED_RETRY/);
});

test('an oversized first eligible row returns explicit failure, never false EOF or silently skipped links', async t => {
  const {db, reads} = setup(t), source = await appropriation(db);
  await insert(db, '00-too-wide', {}, {}, {stored_note: '가'.repeat(STRUCTURE_PAGE_BYTES / 3 + 1)});
  await insert(db, '01-normal');
  await assert.rejects(budgetStructure(params(source.source_key)), /BUDGET_STRUCTURE_ROW_TOO_LARGE/);
  const detail = reads.find(r => r.sql.includes("service='QWGJK'"));
  assert.equal(detail.count, 1);
  assert.equal(detail.bytes, 0, 'Only metadata/null sentinel crosses the D1 boundary');
  const response = await GET(new Request('https://example.invalid/api/budget/structure?' + params(source.source_key)));
  assert.equal(response.status, 503);
  assert.equal((await response.json()).error, 'BUDGET_STRUCTURE_ROW_TOO_LARGE');
});

test('wide source aliases remain complete once and do not amplify per-row evidence across 100 exact code matches', async t => {
  const {db} = setup(t), name = 'x'.repeat(900000);
  const source = await appropriation(db, {fld_nm: '', field_name: name});
  for (let i = 0; i < 100; i++) await insert(db, 'tiny-' + String(i).padStart(3, '0'));
  const result = await budgetStructure(params(source.source_key));
  assert.equal(result.rows.length, 100);
  assert.equal(result.next, null);
  assert.equal(result.appropriation.field_name, name, 'Preserve full source evidence; never truncate');
  for (const row of result.rows) {
    for (const dimension of ['field', 'section', 'account']) {
      const evidence = row.match_evidence[dimension];
      assert.equal(evidence.basis, 'CODE');
      assert.deepEqual(evidence.appropriation_ref, {code: `appropriation.${dimension}_code`, name: `appropriation.${dimension}_name`});
      assert.equal(evidence.appropriation_code, undefined);
      assert.equal(evidence.appropriation_name, undefined);
      const resolve = path => path.split('.').reduce((value, key) => value[key], result);
      assert.equal(resolve(evidence.appropriation_ref.code), evidence.detail_code);
      assert.equal(resolve(evidence.appropriation_ref.name), result.appropriation[dimension + '_name']);
    }
    assert.equal(row.match_evidence.field.detail_name, '교통 및 물류');
    assert.equal(row.match_evidence.org_code.appropriation_ref, 'appropriation.org_code');
    assert.equal(row.appropriation_source_key, source.source_key);
  }
  const serialized = JSON.stringify(result);
  assert.equal(serialized.split(name).length - 1, 1, 'Source alias is serialized once, not once per linked project');
  assert.ok(Buffer.byteLength(serialized, 'utf8') < 4 * 1024 * 1024, 'Response remains bounded with a near-limit source and 100 details');
});

test('response independently rejects impossible evidence and route uses no-store plus meaningful statuses', async t => {
  const {db} = setup(t), source = await appropriation(db);
  await insert(db, '1');
  const result = await budgetStructure(params(source.source_key)), row = result.rows[0], plan = budgetStructurePlan(params(source.source_key));
  assert.throws(() => budgetStructureResult(plan, result.appropriation, [{...row, field_code: 'conflict'}], '0'), /INVALID_MATCH/);
  assert.throws(() => budgetStructureResult(plan, result.appropriation, [{...row, org_code: ''}], '0'), /INVALID_MATCH/);
  const call = extra => GET(new Request('https://example.invalid/api/budget/structure?' + params(source.source_key, extra)));
  const success = await call({});
  assert.equal(success.status, 200);
  assert.equal(success.headers.get('Cache-Control'), 'no-store');
  assert.equal((await success.json()).rows.length, 1);
  assert.equal((await call({key: 'not-found'})).status, 404);
  assert.equal((await call({service: 'QWGJK'})).status, 400);
  const cursor = JSON.stringify({v: 1, mode: 'synthetic', sourceKey: source.source_key, afterKey: row.source_key, revision: '100'});
  assert.equal((await call({cursor})).status, 409);
  db.exec("DELETE FROM budget_epochs WHERE mode='synthetic'");
  assert.equal((await call({})).status, 503);
});
