import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, readdirSync } from 'node:fs';
import {
  institutionFrame, institutionUnpack, institutionEventQuery, eventCursor,
  institutionResultQuery, encodePatternCursor,
} from '../lib/institution-query.mjs';
import { INSTITUTION_PAGE, INSTITUTION_BYTES } from '../lib/institution-storage.mjs';

function setup(t) {
  const db = new DatabaseSync(':memory:');
  t.after(() => db.close());
  db.exec('PRAGMA foreign_keys=ON');
  for (const name of readdirSync(new URL('../drizzle/', import.meta.url)).filter(n => n.endsWith('.sql')).sort()) {
    db.exec(readFileSync(new URL('../drizzle/' + name, import.meta.url), 'utf8'));
  }
  return db;
}
function insert(db, table, row) {
  const keys = Object.keys(row);
  db.prepare(`INSERT INTO ${table}(${keys.join(',')}) VALUES(${keys.map(() => '?')})`).run(...Object.values(row));
}
function runFixture(db, id = 'run-one') {
  const matching = 'matching-' + id;
  insert(db, 'matching_jobs', { id: matching, mode: 'synthetic', state: 'complete', phase: 'DONE',
    engine_version: 'v18-source-fields', g2b_epoch: 0, budget_epoch: 0, from_day: '2026-01-01', to_day: '2026-11-07',
    cursor: '{}', partial: null, budget_count: 0, request_count: 0, candidate_count: 0, match_count: 0,
    excluded_count: 0, lease_until: 0, created_at: '2026-11-07T00:00:00Z', updated_at: '2026-11-07T00:00:00Z' });
  const row = { id, request_id: 'request-' + id, mode: 'synthetic', matching_job_id: matching,
    matching_engine_version: 'v18-source-fields', pattern_version: 'institution-stream-v1', projection_version: 'institution-events-v1',
    g2b_epoch: 0, budget_epoch: 0, from_day: '2026-01-01', to_day: '2026-11-07', budget_scope_from: '2026-01-01',
    budget_scope_to: '2026-11-07', scope_json: '{}', scope_hash: 'scope-' + id, state: 'ready', phase: 'INDEX_PROJECTS',
    cursor_json: '{}', partial_json: null, generation: 0, min_score: 80, indexed_projects: 0, indexed_requests: 0,
    excluded_projects: 0, excluded_requests: 0, processed_projects: 0, processed_requests: 0, completed_groups: 0,
    lease_until: 0, created_at: '2026-11-07T00:00:00Z', updated_at: '2026-11-07T00:00:00Z' };
  insert(db, 'institution_pattern_runs', row);
  return row;
}
function event(n, overrides = {}) {
  return { kind: 'PROJECT', source_event_key: String(n).padStart(5, '0'), region_key: '인천광역시',
    org_key: '인천광역시옹진군', category: 'LIGHTING', fiscal_year: 2026, budget_key: 'P1', kind_order: 0,
    row_json: JSON.stringify({ source: n }), eligible: 1, ...overrides };
}
function yearRow(n, overrides = {}) {
  return { region_key: '인천광역시', org_key: 'org-' + String(n).padStart(4, '0'), category: 'LIGHTING',
    fiscal_year: 2026, row_json: JSON.stringify({ n }), high_projects: 0, ...overrides };
}
const plan = (db, sql, args) => db.prepare('EXPLAIN QUERY PLAN ' + sql).all(...args).map(r => r.detail);

test('institution source framing resumes at 250 rows without gaps and uses source indexes', t => {
  const db = setup(t), run = runFixture(db);
  for (let n = 0; n < 503; n++) insert(db, 'matching_budgets', { job_id: run.matching_job_id,
    source_key: String(n).padStart(5, '0'), row_json: JSON.stringify({ source_key: String(n).padStart(5, '0') }) });
  const sql = institutionFrame('SELECT source_key,row_json FROM matching_budgets WHERE job_id=? AND source_key>?');
  const explain = plan(db, sql, [run.matching_job_id, '']);
  assert.ok(explain.some(s => /SEARCH matching_budgets USING INDEX.*job_id=\? AND source_key>\?/.test(s)), explain.join('\n'));
  let key = '', all = [], sizes = [];
  for (;;) {
    const framed = db.prepare(sql).all(run.matching_job_id, key), page = institutionUnpack(framed);
    assert.ok(framed.every(r => r.prefix_bytes <= INSTITUTION_BYTES));
    sizes.push(page.rows.length); all.push(...page.rows.map(r => r.source_key));
    if (page.done) break;
    key = page.rows.at(-1).source_key;
  }
  assert.deepEqual(sizes, [250, 250, 3]);
  assert.equal(all.length, 503); assert.equal(new Set(all).size, 503);
  assert.equal(INSTITUTION_PAGE, 250); assert.equal(INSTITUTION_BYTES, 512 * 1024);
});

test('source byte framing counts UTF-8 bytes before parsing and never skips a boundary row', t => {
  const db = setup(t);
  db.exec('CREATE TABLE input(source_key TEXT PRIMARY KEY,row_json TEXT NOT NULL)');
  for (let n = 0; n < 11; n++) insert(db, 'input', { source_key: String(n).padStart(3, '0'),
    row_json: JSON.stringify({ source_key: String(n).padStart(3, '0'), text: '한'.repeat(20000) }) });
  const sql = institutionFrame('SELECT source_key,row_json FROM input WHERE source_key>?');
  const first = db.prepare(sql).all(''), a = institutionUnpack(first);
  assert.equal(a.rows.length, 8); assert.equal(a.done, false);
  assert.ok(first.at(-1).prefix_bytes <= INSTITUTION_BYTES);
  const b = institutionUnpack(db.prepare(sql).all(a.rows.at(-1).source_key));
  assert.equal(b.rows.length, 3); assert.equal(b.done, true);
  assert.equal(new Set([...a.rows, ...b.rows].map(r => r.source_key)).size, 11);
  db.exec('DELETE FROM input');
  insert(db, 'input', { source_key: 'oversized', row_json: JSON.stringify({ text: '한'.repeat(180000) }) });
  const oversized = db.prepare(sql).all('');
  assert.equal(oversized.length, 1); assert.equal(oversized[0].row_json, null);
  assert.throws(() => institutionUnpack(oversized), /PATTERN_INPUT_RECORD_TOO_LARGE/);
});

test('winner request keysets use indexed source and provenance joins rather than source scans', t => {
  const db = setup(t), run = runFixture(db);
  const sql = institutionFrame(`SELECT r.request_key AS source_key,r.row_json FROM matching_results r
    LEFT JOIN matching_budgets b ON b.job_id=r.job_id AND b.source_key=r.budget_key
    LEFT JOIN matching_requests q ON q.job_id=r.job_id AND q.request_key=r.request_key
    WHERE r.job_id=? AND r.request_key>? AND (b.source_key IS NOT NULL OR q.request_key IS NOT NULL)`);
  const explain = plan(db, sql, [run.matching_job_id, 'REQUEST:R00500']);
  assert.ok(explain.some(s => /SEARCH r USING INDEX.*job_id=\? AND request_key>\?/.test(s)), explain.join('\n'));
  assert.ok(explain.some(s => /SEARCH b USING COVERING INDEX.*job_id=\? AND source_key=\?/.test(s)), explain.join('\n'));
  assert.ok(explain.some(s => /SEARCH q USING COVERING INDEX.*job_id=\? AND request_key=\?/.test(s)), explain.join('\n'));
  assert.equal(explain.some(s => /^SCAN [rbq]($| )/.test(s)), false, explain.join('\n'));
});

test('event keyset walks one project with many requests using the compound event index', t => {
  const db = setup(t), run = runFixture(db);
  insert(db, 'institution_pattern_events', { run_id: run.id, ...event(0) });
  for (let n = 0; n < 601; n++) insert(db, 'institution_pattern_events', { run_id: run.id,
    ...event(n, { kind: 'REQUEST', kind_order: 1 }) });
  insert(db, 'institution_pattern_events', { run_id: run.id, ...event(9999, { eligible: 0 }) });
  const second = runFixture(db, 'run-two');
  insert(db, 'institution_pattern_events', { run_id: second.id, ...event(9998) });
  const query = institutionEventQuery(run.id, ['인천광역시', '인천광역시옹진군', 'LIGHTING', 2026, 'P1', 0, '00000']);
  const explain = plan(db, query.sql, query.args);
  assert.ok(explain.some(s => /SEARCH institution_pattern_events USING INDEX idx_institution_event_page/.test(s)), explain.join('\n'));
  assert.ok(explain.some(s => s.includes('(region_key,org_key,category,fiscal_year,budget_key,kind_order,source_event_key)>')), explain.join('\n'));
  let cursor = null, all = [];
  for (;;) {
    const q = institutionEventQuery(run.id, cursor), page = institutionUnpack(db.prepare(q.sql).all(...q.args));
    assert.ok(page.rows.length <= 250); all.push(...page.rows);
    if (page.done) break;
    cursor = eventCursor(page.rows.at(-1));
  }
  assert.equal(all.length, 602); assert.equal(all[0].kind, 'PROJECT');
  assert.equal(new Set(all.map(r => r.kind + ':' + r.source_event_key)).size, 602);
  assert.throws(() => institutionEventQuery(run.id, ['bad']), /INVALID_PATTERN_EVENT_CURSOR/);
});

test('schema enforces compound output/event uniqueness and real foreign keys', t => {
  const db = setup(t), a = runFixture(db), b = runFixture(db, 'second');
  assert.equal(db.prepare('PRAGMA foreign_keys').get().foreign_keys, 1);
  const e = event(1);
  insert(db, 'institution_pattern_events', { run_id: a.id, ...e });
  assert.throws(() => insert(db, 'institution_pattern_events', { run_id: a.id, ...e, budget_key: 'other' }), /UNIQUE constraint/);
  insert(db, 'institution_pattern_events', { run_id: b.id, ...e });
  insert(db, 'institution_pattern_events', { run_id: a.id, ...e, kind: 'REQUEST', kind_order: 1 });
  assert.throws(() => insert(db, 'institution_pattern_events', { run_id: 'missing', ...e }), /FOREIGN KEY/);
  const y = yearRow(1);
  insert(db, 'institution_pattern_years', { run_id: a.id, ...y });
  assert.throws(() => insert(db, 'institution_pattern_years', { run_id: a.id, ...y, high_projects: 9 }), /UNIQUE constraint/);
  insert(db, 'institution_pattern_years', { run_id: b.id, ...y });
  insert(db, 'institution_pattern_years', { run_id: a.id, ...y, fiscal_year: 2027 });
  assert.throws(() => insert(db, 'institution_pattern_years', { run_id: 'missing', ...y }), /FOREIGN KEY/);
  assert.throws(() => db.prepare('DELETE FROM matching_jobs WHERE id=?').run(a.matching_job_id), /FOREIGN KEY/);
  assert.throws(() => db.prepare('DELETE FROM institution_pattern_runs WHERE id=?').run(a.id), /FOREIGN KEY/);
  assert.throws(() => insert(db, 'institution_pattern_runs', { ...a, id: 'another', request_id: 'different', matching_job_id: 'missing' }), /FOREIGN KEY/);
  assert.deepEqual(db.prepare('PRAGMA foreign_key_check').all(), []);
});

test('result pagination is 50 rows, exact keyset, and bound to run/scope/generation/filters', t => {
  const db = setup(t), run = runFixture(db), params = new URLSearchParams();
  for (let n = 0; n < 103; n++) insert(db, 'institution_pattern_years', { run_id: run.id, ...yearRow(n) });
  let q = institutionResultQuery(run, params), rows = db.prepare(q.sql).all(...q.args);
  assert.equal(rows.length, 50); assert.equal(rows[0].candidate_count, 51);
  const cursor = encodePatternCursor(run, rows.at(-1), params);
  params.set('after', cursor); q = institutionResultQuery(run, params);
  const explain = plan(db, q.sql, q.args);
  assert.ok(explain.some(s => /SEARCH institution_pattern_years USING INDEX.*run_id=\?/.test(s)), explain.join('\n'));
  assert.ok(explain.some(s => s.includes('(region_key,org_key,category,fiscal_year)>')), explain.join('\n'));
  const second = db.prepare(q.sql).all(...q.args);
  params.set('after', encodePatternCursor(run, second.at(-1), params)); q = institutionResultQuery(run, params);
  const last = db.prepare(q.sql).all(...q.args);
  assert.equal(second.length, 50); assert.equal(last.length, 3); assert.equal(last[0].candidate_count, 3);
  assert.equal(new Set([...rows, ...second, ...last].map(r => r.org_key)).size, 103);
  for (const changed of [{ ...run, id: 'other' }, { ...run, scope_hash: 'new' }, { ...run, generation: 1 }]) {
    assert.throws(() => institutionResultQuery(changed, params), /INVALID_PATTERN_CURSOR/);
  }
  params.set('category', 'POLE');
  assert.throws(() => institutionResultQuery(run, params), /INVALID_PATTERN_CURSOR/);
  for (const value of ['not-json', '{}', JSON.stringify({ ...JSON.parse(cursor), key: ['a', 'b', 'LIGHTING', '2026'] })]) {
    assert.throws(() => institutionResultQuery(run, new URLSearchParams({ after: value })), /INVALID_PATTERN_CURSOR/);
  }
  for (const [key, value, error] of [['year', '2025', 'YEAR'], ['category', 'SOLAR', 'CATEGORY'], ['org', 'x'.repeat(1025), 'FILTER']]) {
    assert.throws(() => institutionResultQuery(run, new URLSearchParams({ [key]: value })), new RegExp('INVALID_PATTERN_' + error));
  }
});

test('result query byte framing is resumable and produces an explicit oversized sentinel', t => {
  const db = setup(t), run = runFixture(db), params = new URLSearchParams();
  for (let n = 0; n < 12; n++) insert(db, 'institution_pattern_years', { run_id: run.id,
    ...yearRow(n, { row_json: JSON.stringify({ n, label: '한'.repeat(20000) }) }) });
  let q = institutionResultQuery(run, params), first = db.prepare(q.sql).all(...q.args);
  assert.equal(first.length, 8); assert.equal(first[0].candidate_count, 12);
  assert.ok(first.at(-1).prefix_bytes <= INSTITUTION_BYTES);
  params.set('after', encodePatternCursor(run, first.at(-1), params)); q = institutionResultQuery(run, params);
  assert.equal(db.prepare(q.sql).all(...q.args).length, 4);
  db.exec('DELETE FROM institution_pattern_years');
  insert(db, 'institution_pattern_years', { run_id: run.id, ...yearRow(0, { row_json: JSON.stringify({ s: '한'.repeat(180000) }) }) });
  q = institutionResultQuery(run, new URLSearchParams());
  assert.equal(db.prepare(q.sql).all(...q.args)[0].row_json, null);
});
