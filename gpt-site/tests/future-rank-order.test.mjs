import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { FUTURE_RANK_VERSION, FUTURE_RANK_MAX_ROWS, FUTURE_RANK_MAX_BYTES,
  FUTURE_RANK_AMOUNT_DIGITS, FUTURE_RANK_TEXT_BYTES, futureAmountOrderKey,
  futureRankKey, compareFutureRankRows, prepareFutureRankRow,
  verifyFutureRankRow, mergeFutureRankRows } from '../lib/future-rank-order.mjs';

const row = (source_key, extra = {}) => ({ source_key, historical_evidence_score: 60,
  budget_amount: '100', appropriation_amount: null, org_name: '기관', project_name: '사업', ...extra });
const ids = rows => rows.map(r => r.source_key);
const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const binary = (a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b));
function oracle(a, b) {
  const aa = a.budget_amount ?? a.appropriation_amount ?? null;
  const bb = b.budget_amount ?? b.appropriation_amount ?? null;
  return b.historical_evidence_score - a.historical_evidence_score ||
    compare(aa === null, bb === null) ||
    (aa === null || bb === null ? 0 : compare(BigInt(bb), BigInt(aa))) ||
    binary(b.org_name ?? '', a.org_name ?? '') || binary(b.project_name ?? '', a.project_name ?? '') ||
    binary(a.source_key, b.source_key);
}

test('amount keys exactly order signed integers, huge text, zero and missing separately', () => {
  const amounts = [null, '-1000', '-100', '-99', '-10', '-2', '-1', '0', '1', '2', '9', '10', '99', '100',
    '9007199254740992', '9007199254740993', '9223372036854775808', '9'.repeat(201), '1' + '0'.repeat(201),
    '-' + '9'.repeat(201), '-' + '1' + '0'.repeat(201)];
  const expected = amounts.slice().sort((a, b) => compare(a === null, b === null) ||
    (a === null || b === null ? 0 : compare(BigInt(b), BigInt(a))));
  const actual = amounts.slice().sort((a, b) => {
    const x = futureAmountOrderKey(a), y = futureAmountOrderKey(b);
    return x.missing - y.missing || compare(x.key, y.key);
  });
  assert.deepEqual(actual, expected);
  assert.deepEqual(futureAmountOrderKey(null), { missing: 1, key: '' });
  for (const value of ['-0', '+000', '000', 0, -0])
    assert.deepEqual(futureAmountOrderKey(value), futureAmountOrderKey('0'));
  assert.deepEqual(futureAmountOrderKey('-00120'), futureAmountOrderKey('-120'));
  assert.deepEqual(futureAmountOrderKey('+00120'), futureAmountOrderKey('120'));
  assert.deepEqual(futureAmountOrderKey(Number.MAX_SAFE_INTEGER), futureAmountOrderKey('9007199254740991'));
  for (const sign of ['', '-']) assert.equal(futureAmountOrderKey(sign + '9'.repeat(FUTURE_RANK_AMOUNT_DIGITS)).missing, 0);
  assert.throws(() => futureAmountOrderKey('9'.repeat(FUTURE_RANK_AMOUNT_DIGITS + 1)), /AMOUNT_DIGIT_LIMIT/);
  assert.throws(() => futureAmountOrderKey('0'.repeat(FUTURE_RANK_AMOUNT_DIGITS + 1)), /AMOUNT_DIGIT_LIMIT/);
});

test('unknown, nonintegral and unsafe monetary inputs fail, including unused fallback values', () => {
  for (const amount of ['', ' ', ' 1', '1 ', '1.0', '1e3', '1,000', 'NaN', 'unknown', '+', '--1', true, {}, [], 1n])
    assert.throws(() => futureAmountOrderKey(amount), /INVALID_AMOUNT/);
  for (const amount of [Number.MAX_SAFE_INTEGER + 1, -Number.MAX_SAFE_INTEGER - 1, 1.5, Infinity, NaN])
    assert.throws(() => futureAmountOrderKey(amount), /UNSAFE_AMOUNT_NUMBER/);
  assert.throws(() => futureRankKey(row('A', { appropriation_amount: 'unknown' })), /INVALID_AMOUNT/);
  for (const invalid_amount of [1, true, null, '0', 2])
    assert.throws(() => futureRankKey(row('A', { invalid_amount })), /INVALID_AMOUNT_FLAG/);
});

test('score is required and bounded; zero does not use appropriation fallback', () => {
  for (const score of [undefined, null, '', '60', -1, 101, 0.5, NaN, Infinity]) {
    assert.throws(() => futureRankKey(row('A', { historical_evidence_score: score })), /INVALID_SCORE/);
  }
  const rows = [row('missing', { budget_amount: null }), row('negative', { budget_amount: '-1' }),
    row('zero', { budget_amount: '0', appropriation_amount: '999999999999999999999999' }),
    row('fallback', { budget_amount: null, appropriation_amount: '1' }),
    row('score', { budget_amount: null, historical_evidence_score: 100 }),
    row('lowest-score', { budget_amount: '9'.repeat(100), historical_evidence_score: 0 })];
  assert.deepEqual(ids(rows.slice().sort(compareFutureRankRows)), ['score', 'fallback', 'zero', 'negative', 'missing', 'lowest-score']);
  const absent = { ...row('A') }; delete absent.budget_amount;
  assert.equal(futureRankKey(absent), futureRankKey(row('A', { budget_amount: null })));
});

test('UTF-8/BINARY descending names and ascending identity include prefixes and supplementary Unicode', () => {
  const words = ['', 'a', 'aa', 'a/', 'a:g', '\u007f', 'é', 'e\u0301', '가', '\ue000', '𐀀', '😀'];
  for (const property of ['org_name', 'project_name', 'source_key']) {
    const rows = words.filter(w => property !== 'source_key' || w).map((word, i) => row('K' + i, { [property]: word }));
    assert.deepEqual(ids(rows.slice().sort(compareFutureRankRows)), ids(rows.slice().sort(oracle)));
  }
  // UTF-16 would put the supplementary character before U+E000; SQLite does not.
  assert.deepEqual(ids([row('\ue000'), row('𐀀')].sort(compareFutureRankRows)), ['\ue000', '𐀀']);
  assert.deepEqual(ids([row('x', { org_name: '\ue000' }), row('y', { org_name: '𐀀' })].sort(compareFutureRankRows)), ['y', 'x']);
  const names = [row('A', { org_name: ' a' }), row('B', { org_name: 'a' })];
  assert.deepEqual(ids(names.sort(compareFutureRankRows)), ['B', 'A']);
  for (const value of ['\ud800', '\udc00', 'x\ud800x', 'x\0y']) {
    assert.throws(() => futureRankKey(row(value)), /INVALID_SOURCE_KEY/);
    assert.throws(() => futureRankKey(row('A', { org_name: value })), /INVALID_ORG/);
  }
  assert.throws(() => futureRankKey(row('')), /INVALID_SOURCE_KEY/);
  assert.throws(() => futureRankKey(row('  ')), /INVALID_SOURCE_KEY/);
  assert.throws(() => futureRankKey(row('A', { org_name: 1 })), /INVALID_ORG/);
  assert.throws(() => futureRankKey(row('x'.repeat(FUTURE_RANK_TEXT_BYTES + 1))), /TEXT_BYTE_LIMIT/);
  assert.throws(() => futureRankKey(row('가'.repeat(Math.floor(FUTURE_RANK_TEXT_BYTES / 3) + 1))), /TEXT_BYTE_LIMIT/);
});

test('prepare and verify preserve all source fields and reject stored identity/version/key tampering', () => {
  const source = row('A', { budget_amount: '+0009007199254740993', source_layer: 'APPROPRIATION',
    fiscal_year: 2027, raw: { nested: [null, false, { abc: '\n한글😀' }] }, user_note: 'unchanged', invalid_amount: 0 });
  const original = structuredClone(source), prepared = prepareFutureRankRow(source);
  assert.equal(prepared.version, FUTURE_RANK_VERSION);
  assert.equal(prepared.source_key, 'A');
  assert.equal(prepared.rank_key, futureRankKey(source));
  assert.deepEqual(verifyFutureRankRow(prepared), original);
  assert.deepEqual(JSON.parse(prepared.row_json), original);
  assert.deepEqual(source, original);
  assert.equal(prepared.byteLength, Buffer.byteLength(JSON.stringify([[source, prepared.rank_key]])));
  assert.throws(() => verifyFutureRankRow({ ...prepared, version: 'future-rank-v2' }), /VERSION_MISMATCH/);
  assert.throws(() => verifyFutureRankRow({ ...prepared, source_key: 'B' }), /SOURCE_IDENTITY_MISMATCH/);
  assert.throws(() => verifyFutureRankRow({ ...prepared, rank_key: prepared.rank_key + '0' }), /ORDER_KEY_MISMATCH/);
  assert.throws(() => verifyFutureRankRow({ ...prepared, row_json: prepared.row_json.replace('unchanged', 'updated') + ' ' }), /NONCANONICAL_STORED_JSON/);
  assert.throws(() => verifyFutureRankRow({ ...prepared, row_json: '{' }), /INVALID_STORED_JSON/);
  assert.throws(() => verifyFutureRankRow({ ...prepared, row_json: '[]' }), /ROW_OBJECT_REQUIRED/);
  assert.throws(() => verifyFutureRankRow({ ...prepared, row_json: JSON.stringify(source) }), /NONCANONICAL_STORED_JSON/);
  // A rank verifier is not a cryptographic payload-integrity check.
  assert.equal(verifyFutureRankRow({ ...prepared, row_json: prepared.row_json.replace('unchanged', 'updated') }).user_note, 'updated');
});

test('ordinary unknown JSON fields survive, but JSON coercion, omission, getters and excessive structures fail', () => {
  const invalid = [undefined, NaN, Infinity, -0, Number.MAX_SAFE_INTEGER + 1, 1n, () => 1, Symbol('x'),
    new Date(), new Map(), new Set(), /abc/];
  for (const extra of invalid) assert.throws(() => prepareFutureRankRow(row('A', { extra })), /FUTURE_RANK_/);
  const cycle = {}; cycle.self = cycle;
  assert.throws(() => prepareFutureRankRow(row('A', { cycle })), /CYCLIC_INPUT/);
  assert.throws(() => prepareFutureRankRow(row('A', { extra: Array(3) })), /NON_JSON_ARRAY/);
  assert.throws(() => prepareFutureRankRow(row('A', { extra: [1, , 2] })), /NON_JSON_ARRAY/);
  assert.throws(() => prepareFutureRankRow(row('A', { extra: '\ud800' })), /INVALID_JSON_UNICODE/);
  const getter = row('A'); let called = 0;
  Object.defineProperty(getter, 'budget_amount', { enumerable: true, get() { called++; return '100'; } });
  assert.throws(() => prepareFutureRankRow(getter), /NON_JSON_PROPERTY/);
  assert.throws(() => futureRankKey(getter), /NON_JSON_PROPERTY/);
  assert.equal(called, 0);
  const nonEnumerable = row('A'); Object.defineProperty(nonEnumerable, 'hidden', { value: 1 });
  assert.throws(() => prepareFutureRankRow(nonEnumerable), /NON_JSON_PROPERTY/);
  assert.throws(() => prepareFutureRankRow(row('A', { [Symbol('x')]: 'hidden' })), /NON_JSON_PROPERTY/);
  let deep = {}; for (let i = 0; i < 17; i++) deep = { deep };
  assert.throws(() => prepareFutureRankRow(row('A', { deep })), /JSON_DEPTH_LIMIT/);
  assert.throws(() => prepareFutureRankRow(row('A', { items: Array(4097).fill(0) })), /JSON_LIST_LIMIT/);
  assert.throws(() => prepareFutureRankRow(row('A', { fields: Object.fromEntries(Array.from({ length: 257 }, (_, i) => ['k' + i, i])) })), /JSON_FIELD_LIMIT/);
});

test('bounded merge retains the exact 500 best supplied rows without changing inputs or claiming coverage', () => {
  const selected = Array.from({ length: 500 }, (_, i) => row('S' + i, { budget_amount: String(i * 2) }));
  const incoming = Array.from({ length: 500 }, (_, i) => row('I' + i, { budget_amount: String(i * 2 + 1) }));
  const before = JSON.stringify([selected, incoming]);
  const result = mergeFutureRankRows(selected, incoming);
  assert.equal(result.rows.length, FUTURE_RANK_MAX_ROWS);
  assert.deepEqual(ids(result.rows), ids([...selected, ...incoming].sort(oracle).slice(0, 500)));
  assert.equal(result.coverage, 'SUPPLIED_ROWS_ONLY');
  assert.equal(result.consideredRows, 1000); assert.equal(result.omittedRows, 500);
  assert.equal(Object.hasOwn(result, 'globalRanking'), false);
  assert.equal(Object.hasOwn(result, 'sourceExhausted'), false);
  assert.equal(JSON.stringify([selected, incoming]), before);
  assert.equal(result.rows[0], incoming[499]);
  assert.deepEqual(result.rankKeys, result.rows.map(futureRankKey));
  assert.equal(result.inputBytes, Buffer.byteLength(JSON.stringify([...selected, ...incoming].map(r => [r, futureRankKey(r)]))));
  assert.equal(result.outputBytes, Buffer.byteLength(JSON.stringify(result.rows.map(r => [r, futureRankKey(r)]))));
  assert.deepEqual(ids(mergeFutureRankRows(incoming.slice().reverse(), selected.slice().reverse()).rows), ids(result.rows));
  assert.deepEqual(mergeFutureRankRows().rows, []);
});

test('duplicates and conflicts fail even among omitted rows, not silently deduplicated', () => {
  const a = row('A'), sameDifferentPropertyOrder = Object.fromEntries(Object.entries(a).reverse());
  assert.throws(() => mergeFutureRankRows([a], [sameDifferentPropertyOrder]), /DUPLICATE_SOURCE_IDENTITY/);
  for (const change of [{ budget_amount: '101' }, { org_name: 'other' }, { historical_evidence_score: 1 }, { extra: 'changed' }, { service: 'AIDFA' }])
    assert.throws(() => mergeFutureRankRows([a], [{ ...a, ...change }]), /CONFLICTING_SOURCE_IDENTITY/);
  assert.throws(() => mergeFutureRankRows([row('winner', { historical_evidence_score: 100 })], [a, a], { limit: 1 }), /DUPLICATE_SOURCE_IDENTITY/);
  assert.equal(compareFutureRankRows(a, { ...a }), 0);
  const distinct = [row('b'), row('a')];
  assert.deepEqual(ids(mergeFutureRankRows([], distinct).rows), ['a', 'b']);
});

test('row and exact UTF-8 framed-byte caps fail explicitly, including bad rank losers', () => {
  for (const limit of [0, -1, 501, 1.1, '5', null])
    assert.throws(() => mergeFutureRankRows([], [], { limit }), /INVALID_LIMIT/);
  for (const byteLimit of [0, 1, FUTURE_RANK_MAX_BYTES + 1, 1.1, '100'])
    assert.throws(() => mergeFutureRankRows([], [], { byteLimit }), /INVALID_BYTE_LIMIT/);
  for (const args of [[Array(501).fill(row('A')), []], [[], Array(501).fill(row('A'))], [null, []], [[], {}]])
    assert.throws(() => mergeFutureRankRows(...args), /INPUT_ROW_LIMIT/);
  const rows = [row('가', { extra: '\0\n😀' }), row('나')];
  const exact = mergeFutureRankRows([], rows).inputBytes;
  assert.doesNotThrow(() => mergeFutureRankRows([], rows, { byteLimit: exact }));
  assert.throws(() => mergeFutureRankRows([], rows, { byteLimit: exact - 1 }), /BYTE_LIMIT/);
  const one = prepareFutureRankRow(rows[0]);
  assert.equal(prepareFutureRankRow(rows[0], { byteLimit: one.byteLength }).byteLength, one.byteLength);
  assert.throws(() => prepareFutureRankRow(rows[0], { byteLimit: one.byteLength - 1 }), /BYTE_LIMIT/);
  const winner = row('winner', { historical_evidence_score: 100 });
  assert.throws(() => mergeFutureRankRows([winner], [row('loser', { extra: 'x'.repeat(FUTURE_RANK_MAX_BYTES) })], { limit: 1 }), /BYTE_LIMIT/);
  assert.throws(() => mergeFutureRankRows([winner], [row('loser', { budget_amount: 'unknown' })], { limit: 1 }), /INVALID_AMOUNT/);
});

test('independent BigInt/BINARY oracle agrees with SQLite indexed rank order and every keyset page', () => {
  const db = new DatabaseSync(':memory:');
  try {
    db.exec('CREATE TABLE ranked(run_id TEXT NOT NULL, source_key TEXT NOT NULL, rank_key TEXT COLLATE BINARY NOT NULL, row_json TEXT NOT NULL, UNIQUE(run_id,source_key)); CREATE UNIQUE INDEX ranked_order ON ranked(run_id,rank_key COLLATE BINARY)');
    let seed = 12345;
    const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed; };
    const words = ['', 'a', 'aa', '한글', '😀', '\ue000', '𐀀', 'é', 'e\u0301', 'name:/'];
    const rows = Array.from({ length: 500 }, (_, i) => {
      const digits = 1 + random() % 260;
      const magnitude = String(1 + random() % 9) + Array.from({ length: digits - 1 }, () => String(random() % 10)).join('');
      return row(String(i) + words[random() % words.length], { historical_evidence_score: random() % 101,
        budget_amount: i % 13 === 0 ? null : i % 11 === 0 ? '0' : (i % 2 ? '-' : '') + magnitude,
        org_name: words[random() % words.length], project_name: words[random() % words.length] });
    });
    rows.push(row('boundary-positive', { budget_amount: '9'.repeat(FUTURE_RANK_AMOUNT_DIGITS) }),
      row('boundary-negative', { budget_amount: '-' + '9'.repeat(FUTURE_RANK_AMOUNT_DIGITS) }));
    const insert = db.prepare('INSERT INTO ranked VALUES(?,?,?,?)');
    for (const r of rows) {
      const p = prepareFutureRankRow(r);
      assert.match(p.rank_key, /^[\x20-\x7e]+$/);
      insert.run('run', p.source_key, p.rank_key, p.row_json);
    }
    const expected = rows.slice().sort(oracle);
    assert.deepEqual(ids(rows.slice().sort(compareFutureRankRows)), ids(expected));
    assert.deepEqual(db.prepare('SELECT source_key FROM ranked WHERE run_id=? ORDER BY rank_key COLLATE BINARY').all('run').map(r => r.source_key), ids(expected));
    let cursor = '', gathered = [];
    const query = 'SELECT source_key,rank_key FROM ranked WHERE run_id=? AND rank_key COLLATE BINARY>? ORDER BY rank_key COLLATE BINARY ASC LIMIT 25';
    while (true) {
      const page = db.prepare(query).all('run', cursor);
      if (!page.length) break;
      gathered.push(...page.map(r => r.source_key)); cursor = page.at(-1).rank_key;
    }
    assert.deepEqual(gathered, ids(expected));
    for (let i = 0; i < expected.length; i++) {
      const page = db.prepare(query).all('run', futureRankKey(expected[i]));
      assert.deepEqual(page.map(r => r.source_key), ids(expected.slice(i + 1, i + 26)));
    }
    const plan = db.prepare('EXPLAIN QUERY PLAN ' + query).all('run', '');
    assert.ok(plan.some(r => /USING INDEX ranked_order/.test(r.detail)));
    assert.ok(plan.every(r => !/TEMP B-TREE/.test(r.detail)));
    const duplicate = prepareFutureRankRow(rows[0]);
    assert.throws(() => insert.run('run', duplicate.source_key, duplicate.rank_key + '0', duplicate.row_json), /UNIQUE constraint failed/);
  } finally { db.close(); }
});
