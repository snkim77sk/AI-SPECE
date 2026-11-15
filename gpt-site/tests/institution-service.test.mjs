import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { registerHooks } from 'node:module';
import { budgetMatchDocument, matchEvidence } from '../lib/matching-storage.mjs';
import { groupRequests, scorePair } from '../lib/matching.mjs';
import { institutionCommitPlan } from '../lib/institution-storage.mjs';

const env = { DB: null };
globalThis.__g2bInstitutionEnv = env;
registerHooks({ resolve(spec, context, next) {
  if (spec === 'cloudflare:workers') return { url: 'data:text/javascript,export const env=globalThis.__g2bInstitutionEnv', shortCircuit: true };
  if (spec.startsWith('.') && !/\.(ts|mjs|js)$/.test(spec) && context.parentURL?.startsWith('file:')) {
    const url = new URL(spec + '.ts', context.parentURL);
    if (existsSync(fileURLToPath(url))) return next(url.href, context);
  }
  return next(spec, context);
} });
const service = await import('../lib/institution-service.ts');

function setup(t) {
  const db = new DatabaseSync(':memory:'), hooks = { beforeBatch: null, beforeStatement: null, afterCommit: null, afterRead: null };
  db.exec('PRAGMA foreign_keys=ON');
  for (const file of readdirSync(new URL('../drizzle/', import.meta.url)).filter(f => f.endsWith('.sql')).sort()) {
    db.exec(readFileSync(new URL('../drizzle/' + file, import.meta.url), 'utf8'));
  }
  const reads = [], writes = [];
  env.DB = {
    prepare(sql) {
      let args = [];
      const statement = {
        sql,
        bind(...values) { args = values; return statement; },
        async first() { const result = db.prepare(sql).get(...args) || null; await hooks.afterRead?.(sql, result); return result; },
        async all() {
          const results = db.prepare(sql).all(...args); reads.push({ sql, count: results.length });
          await hooks.afterRead?.(sql, results); return { results };
        },
        async run() { return statement._run(); },
        _run() { const result = db.prepare(sql).run(...args); writes.push(sql); return { meta: { changes: Number(result.changes) } }; },
      };
      return statement;
    },
    async batch(statements) {
      await hooks.beforeBatch?.(statements);
      db.exec('BEGIN');
      let results;
      try {
        results = statements.map((s, n) => { hooks.beforeStatement?.(s, n); return s._run(); });
        db.exec('COMMIT');
      } catch (error) { db.exec('ROLLBACK'); throw error; }
      await hooks.afterCommit?.(statements);
      return results;
    },
  };
  const fetch = globalThis.fetch;
  globalThis.fetch = () => { throw Error('PROVIDER_FETCH_FORBIDDEN'); };
  t.after(() => { globalThis.fetch = fetch; env.DB = null; db.close(); });
  return { db, hooks, reads, writes };
}
function insert(db, table, row) {
  const fields = Object.keys(row);
  db.prepare(`INSERT INTO ${table}(${fields.join(',')}) VALUES(${fields.map(() => '?')})`).run(...Object.values(row));
}
function seed(db, { mode = 'synthetic', projects = 1, requests = 1, projectOverrides = () => ({}), requestOverrides = () => ({}), jobOverrides = {} } = {}) {
  const id = crypto.randomUUID(), stamp = '2026-11-07T00:00:00Z';
  const job = { id, mode, state: 'complete', phase: 'DONE', engine_version: 'v18-source-fields', g2b_epoch: 0, budget_epoch: 0,
    from_day: '2026-03-01', to_day: '2026-11-07', cursor: '{}', partial: null,
    budget_count: projects, request_count: requests, candidate_count: requests, match_count: requests, excluded_count: 0,
    lease_until: 0, created_at: stamp, updated_at: stamp, ...jobOverrides };
  insert(db, 'matching_jobs', job);
  const docs = [];
  for (let n = 0; n < projects; n++) {
    const raw = { source_key: 'P' + String(n).padStart(5, '0'), payload_hash: 'synthetic-hash-' + n,
      org: '인천광역시 옹진군', region: '인천광역시', category: 'LIGHTING', project: '북도면 보안등 LED 교체사업',
      account: '일반회계', snapshot_date: '2026-03-01', budget_amount: '1000', appropriation_amount: null,
      executed_amount: '0', remaining_amount: '1000', dept: '합성 부서', fiscal_year: 2026, ...projectOverrides(n) };
    const doc = budgetMatchDocument(raw); docs.push(doc);
    insert(db, 'matching_budgets', { job_id: id, source_key: doc.source_key, row_json: JSON.stringify(doc) });
  }
  for (let n = 0; n < requests; n++) {
    const number = 'R' + String(n).padStart(5, '0'), detail = { source_key: 'D' + number, delivery_req_no: number,
      detail_seq: '1', delivery_change_order: '0', demand_org: '인천광역시 옹진군', demand_region: '인천광역시',
      delivery_req_name: '북도면 보안등 교체 관급자재', item_name: 'LED 보안등기구', primary_category: 'LIGHTING',
      source_date: '2026-06-15', amount: '900', quantity: '1', vendor_name: '합성 업체', ...requestOverrides(n) };
    const request = groupRequests([detail])[0];
    request.missing_amount_count = detail.amount === null ? 1 : 0;
    const scored = scorePair(docs[0], request.missing_amount_count ? { ...request, amount: '0' } : request);
    assert.ok(scored.match, 'fixture must be a real scored match');
    const evidence = matchEvidence(docs[0], request, scored.match);
    insert(db, 'matching_requests', { job_id: id, request_key: request.source_key, row_json: JSON.stringify(request),
      missing_amounts: request.missing_amount_count, integrity: request.request_integrity_status });
    insert(db, 'matching_results', { job_id: id, request_key: request.source_key, budget_key: docs[0].source_key,
      score: evidence.score, level: evidence.level, row_json: JSON.stringify(evidence) });
  }
  return { id, mode, docs };
}
const runRow = (db, id) => db.prepare('SELECT * FROM institution_pattern_runs WHERE id=?').get(id);
const count = (db, table, id) => db.prepare(`SELECT COUNT(*) n FROM ${table} WHERE run_id=?`).get(id).n;
const requestId = () => crypto.randomUUID();
const create = (source, key = requestId()) => service.createInstitutionRun(source.mode, source.id, key);
async function finish(id, mode = 'synthetic', max = 100) {
  let result;
  for (let n = 0; n < max; n++) {
    result = await service.institutionStep(id, mode);
    if (result.run.state === 'complete') return result;
  }
  throw Error('TEST_INSTITUTION_DID_NOT_FINISH');
}
function sourceSnapshot(db) {
  const tables = db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'institution_%' AND name NOT LIKE 'sqlite_%' ORDER BY name").all();
  return JSON.stringify(tables.map(({ name }) => [name, db.prepare(`SELECT * FROM ${name} ORDER BY rowid`).all()]));
}

test('workspace metadata is explicit and idempotent creation is mode/source bound without provider I/O', async t => {
  const { db } = setup(t), synthetic = seed(db), live = seed(db, { mode: 'live' }), key = requestId();
  const empty = await service.institutionWorkspace('synthetic');
  assert.equal(empty.run, null); assert.equal(empty.version, 20); assert.equal(empty.providerCalls, 0);
  assert.equal(empty.sourceCompletenessVerified, false); assert.equal(empty.automaticSchedule, false);
  assert.deepEqual(empty.limits, { pageRows: 250, pageBytes: 524288, partialBytes: 65536 });
  const before = sourceSnapshot(db), created = await create(synthetic, key), retry = await create(synthetic, key);
  assert.equal(retry.run.id, created.run.id); assert.equal(db.prepare('SELECT COUNT(*) n FROM institution_pattern_runs').get().n, 1);
  assert.equal(created.run.state, 'ready'); assert.equal(created.run.phase, 'INDEX_PROJECTS');
  assert.equal(created.run.source_population_verified, false); assert.equal(created.run.population_complete, false);
  for (const field of ['lease', 'partial_json', 'cursor_json', 'request_id']) assert.equal(field in created.run, false);
  const other = await create(live);
  assert.notEqual(other.run.id, created.run.id);
  assert.equal((await service.institutionWorkspace('synthetic')).runs.every(r => r.mode === 'synthetic'), true);
  assert.equal((await service.institutionWorkspace('live')).sources.every(s => s.mode === 'live'), true);
  await assert.rejects(service.institutionWorkspace('live', created.run.id), /PATTERN_MODE_MISMATCH/);
  await assert.rejects(create(live, key), /PATTERN_REQUEST_CONFLICT/);
  await assert.rejects(create(synthetic), /PATTERN_SOURCE_CHANGED_OR_ACTIVE_RUN/);
  assert.equal(sourceSnapshot(db), before);
});

test('creation requires complete DONE current-engine same-mode source and rejects residual partials', async t => {
  const { db } = setup(t);
  for (const change of [{ state: 'ready' }, { phase: 'MATCH' }, { engine_version: 'v17' }, { g2b_epoch: 1 }, { budget_epoch: 1 }, { partial: '{}' }]) {
    const source = seed(db, { requests: 0, jobOverrides: change });
    await assert.rejects(create(source), /PATTERN_COMPLETE_CURRENT_MATCH_REQUIRED/);
  }
  const source = seed(db, { mode: 'live' });
  await assert.rejects(service.createInstitutionRun('synthetic', source.id, requestId()), /PATTERN_COMPLETE_CURRENT_MATCH_REQUIRED/);
  await assert.rejects(service.createInstitutionRun('other', source.id, requestId()), /INVALID_MODE/);
  await assert.rejects(service.createInstitutionRun('live', 'bad-id', requestId()), /INVALID_PATTERN_INPUT/);
  assert.equal(db.prepare('SELECT COUNT(*) n FROM institution_pattern_runs').get().n, 0);
});

test('PROJECT REQUEST AGGREGATE phases resume across 250-row checkpoints and reconcile all source counters', async t => {
  const { db, reads } = setup(t), source = seed(db, { projects: 503, requests: 601,
    projectOverrides: n => n === 502 ? { category: 'SOLAR' } : {} }), before = sourceSnapshot(db);
  const { run } = await create(source);
  let w = await service.institutionStep(run.id, source.mode);
  assert.equal(w.run.phase, 'INDEX_PROJECTS'); assert.equal(w.run.indexed_projects, 250);
  assert.equal(count(db, 'institution_pattern_events', run.id), 250);
  assert.deepEqual(JSON.parse(runRow(db, run.id).cursor_json), { key: 'P00249' });
  w = await service.institutionStep(run.id, source.mode);
  assert.equal(w.run.indexed_projects, 500); assert.equal(w.run.phase, 'INDEX_PROJECTS');
  w = await service.institutionStep(run.id, source.mode);
  assert.equal(w.run.indexed_projects, 503); assert.equal(w.run.excluded_projects, 1); assert.equal(w.run.phase, 'INDEX_REQUESTS');
  assert.deepEqual(JSON.parse(runRow(db, run.id).cursor_json), {});
  w = await service.institutionStep(run.id, source.mode);
  assert.equal(w.run.indexed_requests, 250); assert.equal(w.run.phase, 'INDEX_REQUESTS');
  w = await service.institutionStep(run.id, source.mode);
  assert.equal(w.run.indexed_requests, 500);
  w = await service.institutionStep(run.id, source.mode);
  assert.equal(w.run.indexed_requests, 601); assert.equal(w.run.phase, 'AGGREGATE'); assert.equal(w.run.hasPartial, true);
  w = await finish(run.id);
  assert.equal(w.run.phase, 'DONE'); assert.equal(w.run.processed_projects, 502); assert.equal(w.run.processed_requests, 601);
  assert.equal(w.run.completed_groups, 1); assert.equal(w.run.hasPartial, false); assert.ok(w.run.completed_at);
  assert.equal(count(db, 'institution_pattern_events', run.id), 1104);
  const persisted = db.prepare('SELECT high_projects,row_json FROM institution_pattern_years WHERE run_id=?').get(run.id);
  assert.equal(persisted.high_projects, 1, '601 winning requests must not become 601 high projects');
  assert.ok(reads.filter(r => r.sql.startsWith('WITH picked')).every(r => r.count <= 250));
  const result = await service.institutionResults(new URLSearchParams({ id: run.id, mode: source.mode }));
  assert.equal(result.rows.length, 1); assert.equal(result.next, null); assert.equal(result.stale, false);
  assert.equal(result.rows[0].high_match_rows, 601); assert.equal(result.rows[0].observed_budget_projects, 502);
  assert.equal(result.rows[0].observed_budget_amount, '502000');
  assert.equal(result.rows[0].matched_budget_amount, '1000'); assert.equal(result.rows[0].actual_shopping_amount, '540900');
  assert.equal(result.providerCalls, 0); assert.equal(result.source_io_performed, false);
  assert.equal(result.sourceCompletenessVerified, false); assert.equal(result.populationComplete, false);
  assert.equal(sourceSnapshot(db), before, 'source rows and old current-data tables remain unchanged');
});

test('an empty matching snapshot completes explicitly with zero events and zero output groups', async t => {
  const { db } = setup(t), source = seed(db, { projects: 0, requests: 0 }), { run } = await create(source);
  const w = await finish(run.id);
  assert.equal(w.run.indexed_projects, 0); assert.equal(w.run.indexed_requests, 0); assert.equal(w.run.completed_groups, 0);
  assert.equal(count(db, 'institution_pattern_events', run.id), 0);
  assert.deepEqual((await service.institutionResults(new URLSearchParams({ mode: source.mode, id: run.id }))).rows, []);
});

test('incomplete money stays unknown while observed amounts and counts remain exact', async t => {
  const { db } = setup(t), source = seed(db, { projects: 2, requests: 2,
    projectOverrides: n => n ? { budget_amount: null, appropriation_amount: null } : {},
    requestOverrides: n => n ? { amount: null } : {} }), { run } = await create(source);
  const w = await finish(run.id);
  assert.equal(w.run.processed_projects, 2); assert.equal(w.run.processed_requests, 2);
  const result = await service.institutionResults(new URLSearchParams({ mode: source.mode, id: run.id }));
  const row = result.rows[0];
  assert.equal(row.high_matched_budget_projects, 1);
  assert.equal(row.high_match_rows, 2);
  assert.equal(row.actual_shopping_amount, null);
  assert.equal(row.observed_shopping_amount, '900');
  assert.equal(row.historical_budget_amount, null);
});

test('source counts must reconcile before advancing the projection checkpoint', async t => {
  const { db } = setup(t), source = seed(db, { jobOverrides: { budget_count: 2 } }), { run } = await create(source);
  await assert.rejects(service.institutionStep(run.id, source.mode), /PATTERN_SOURCE_COUNT_MISMATCH/);
  const state = runRow(db, run.id);
  assert.equal(state.state, 'error'); assert.equal(state.indexed_projects, 0);
  assert.equal(state.phase, 'INDEX_PROJECTS'); assert.equal(count(db, 'institution_pattern_events', run.id), 0);
});

test('projection checkpoint and emitted events roll back together on a mid-batch failure', async t => {
  const { db, hooks } = setup(t), source = seed(db), { run } = await create(source);
  hooks.beforeStatement = statement => { if (statement.sql.startsWith('UPDATE institution_pattern_runs')) throw Error('TEST_TRANSACTION_FAILURE'); };
  await assert.rejects(service.institutionStep(run.id, source.mode), /TEST_TRANSACTION_FAILURE/);
  assert.equal(count(db, 'institution_pattern_events', run.id), 0);
  assert.equal(runRow(db, run.id).indexed_projects, 0); assert.equal(runRow(db, run.id).cursor_json, '{}');
  hooks.beforeStatement = null;
  await service.institutionState(run.id, source.mode, 'ready');
  const done = await finish(run.id);
  assert.equal(done.run.indexed_projects, 1); assert.equal(done.run.indexed_requests, 1);
});

test('aggregate outputs and partial cursor roll back together if final checkpoint cannot commit', async t => {
  const { db, hooks } = setup(t), source = seed(db), { run } = await create(source);
  await service.institutionStep(run.id, source.mode); await service.institutionStep(run.id, source.mode);
  const before = runRow(db, run.id);
  hooks.beforeStatement = statement => { if (statement.sql.startsWith('UPDATE institution_pattern_runs')) throw Error('TEST_OUTPUT_COMMIT_FAILURE'); };
  await assert.rejects(service.institutionStep(run.id, source.mode), /TEST_OUTPUT_COMMIT_FAILURE/);
  const after = runRow(db, run.id);
  assert.equal(count(db, 'institution_pattern_years', run.id), 0);
  assert.equal(after.cursor_json, before.cursor_json); assert.equal(after.partial_json, before.partial_json);
  assert.equal(after.processed_projects, 0); assert.equal(after.processed_requests, 0);
  hooks.beforeStatement = null; await service.institutionState(run.id, source.mode, 'ready');
  assert.equal((await finish(run.id)).run.completed_groups, 1);
});

test('source epoch mutations immediately before commit fence events and checkpoint writes', async t => {
  const { db, hooks } = setup(t), source = seed(db), { run } = await create(source);
  hooks.beforeBatch = () => { hooks.beforeBatch = null; db.exec("UPDATE budget_epochs SET revision=revision+1 WHERE mode='synthetic'"); };
  await assert.rejects(service.institutionStep(run.id, source.mode), /PATTERN_SOURCE_STALE|PATTERN_SOURCE_CHANGED_OR_LEASE_LOST|PATTERN_PROCESSING_FAILED/);
  assert.equal(count(db, 'institution_pattern_events', run.id), 0);
  assert.equal(runRow(db, run.id).indexed_projects, 0); assert.equal(runRow(db, run.id).state, 'error');
  assert.equal((await service.institutionWorkspace(source.mode, run.id)).run.stale, true);
  await assert.rejects(service.institutionState(run.id, source.mode, 'ready'), /PATTERN_SOURCE_STALE/);
});

test('lease ownership survives old generation writes and an expired lease permits a new bounded step', async t => {
  const { db, hooks } = setup(t), source = seed(db, { projects: 251 }), { run } = await create(source);
  db.prepare("UPDATE institution_pattern_runs SET state='running',lease='old-owner',generation=4,lease_until=? WHERE id=?").run(Date.now() + 90000, run.id);
  await assert.rejects(service.institutionStep(run.id, source.mode), /PATTERN_BUSY_PAUSED_OR_COMPLETE/);
  assert.equal(runRow(db, run.id).generation, 4);
  db.prepare('UPDATE institution_pattern_runs SET lease_until=0 WHERE id=?').run(run.id);
  const old = runRow(db, run.id);
  const w = await service.institutionStep(run.id, source.mode);
  assert.equal(w.run.generation, 5); assert.equal(w.run.indexed_projects, 250);
  const before = runRow(db, run.id);
  const stale = institutionCommitPlan(old, 'old-owner', { phase: 'DONE', cursor: {}, partial: null, complete: true,
    indexedProjects: 999, processedProjects: 999 }, [], [], '2026-11-07T00:00:00Z');
  await assert.rejects(env.DB.batch(stale.map(p => env.DB.prepare(p.sql).bind(...p.params))), /malformed JSON/);
  assert.deepEqual(runRow(db, run.id), before);
  hooks.beforeBatch = () => {
    hooks.beforeBatch = null;
    db.prepare("UPDATE institution_pattern_runs SET generation=generation+1,lease='new-owner',state='running',lease_until=? WHERE id=?").run(Date.now() + 90000, run.id);
  };
  await assert.rejects(service.institutionStep(run.id, source.mode), /PATTERN_LEASE_LOST|PATTERN_SOURCE_CHANGED_OR_LEASE_LOST|PATTERN_PROCESSING_FAILED/);
  const replacement = runRow(db, run.id);
  assert.equal(replacement.lease, 'new-owner'); assert.equal(replacement.state, 'running');
  assert.equal(replacement.indexed_projects, 250); assert.equal(count(db, 'institution_pattern_events', run.id), 250);
});

test('pause/cancel is persistent, resume keeps checkpoints, and opposite mode cannot control a run', async t => {
  const { db } = setup(t), source = seed(db, { projects: 251 }), { run } = await create(source);
  await service.institutionStep(run.id, source.mode);
  const before = runRow(db, run.id), paused = await service.institutionState(run.id, source.mode, 'paused');
  assert.equal(paused.run.state, 'paused'); assert.equal(paused.run.generation, before.generation + 1);
  await assert.rejects(service.institutionStep(run.id, source.mode), /PATTERN_BUSY_PAUSED_OR_COMPLETE/);
  await assert.rejects(service.institutionState(run.id, 'live', 'ready'), /PATTERN_RUN_NOT_FOUND/);
  await assert.rejects(service.institutionStep(run.id, 'live'), /PATTERN_RUN_NOT_FOUND/);
  await service.institutionState(run.id, source.mode, 'ready');
  assert.equal(runRow(db, run.id).cursor_json, before.cursor_json);
  await service.institutionStep(run.id, source.mode);
  assert.equal(runRow(db, run.id).indexed_projects, 251);
  await service.institutionState(run.id, source.mode, 'cancelled');
  await assert.rejects(service.institutionStep(run.id, source.mode), /PATTERN_BUSY_PAUSED_OR_COMPLETE/);
  await assert.rejects(service.institutionState(run.id, source.mode, 'ready'), /PATTERN_BUSY_OR_COMPLETE/);
});

test('lost committed responses are recovered by readback without duplicating source events or outputs', async t => {
  const { db, hooks } = setup(t), source = seed(db, { projects: 251 }), key = requestId(), { run } = await create(source, key);
  hooks.afterCommit = () => { hooks.afterCommit = null; throw Error('TEST_RESPONSE_LOST'); };
  await assert.rejects(service.institutionStep(run.id, source.mode), /TEST_RESPONSE_LOST/);
  const readback = await service.institutionWorkspace(source.mode, run.id);
  assert.equal(readback.run.state, 'ready'); assert.equal(readback.run.indexed_projects, 250);
  assert.equal((await create(source, key)).run.id, run.id);
  assert.equal(count(db, 'institution_pattern_events', run.id), 250);
  const w = await finish(run.id);
  assert.equal(w.run.indexed_projects, 251); assert.equal(w.run.indexed_requests, 1);
  assert.equal(count(db, 'institution_pattern_events', run.id), 252);
  assert.equal(count(db, 'institution_pattern_years', run.id), 1);
});

test('results reject incomplete runs, detect mid-read epoch changes, and label completed snapshots stale', async t => {
  const { db, hooks } = setup(t), source = seed(db), { run } = await create(source), params = new URLSearchParams({ mode: source.mode, id: run.id });
  await assert.rejects(service.institutionResults(params), /PATTERN_RUN_NOT_COMPLETE/);
  await finish(run.id);
  hooks.afterRead = sql => {
    if (sql.includes('FROM institution_pattern_years')) {
      hooks.afterRead = null; db.exec("UPDATE dataset_epochs SET revision=revision+1 WHERE mode='synthetic'");
    }
  };
  await assert.rejects(service.institutionResults(params), /PATTERN_SOURCE_CHANGED_RETRY/);
  const stale = await service.institutionResults(params);
  assert.equal(stale.stale, true); assert.equal(stale.rows.length, 1);
  await assert.rejects(service.institutionResults(new URLSearchParams({ mode: 'live', id: run.id })), /PATTERN_RUN_NOT_FOUND/);
});

test('byte-limited source projection checkpoints resume before 250 rows without dropping long source keys', async t => {
  const { db } = setup(t), source = seed(db, { projects: 160, requests: 0,
    projectOverrides: n => ({ source_key: 'P' + String(n).padStart(5, '0') + 'x'.repeat(4000) }) }), { run } = await create(source);
  const first = await service.institutionStep(run.id, source.mode);
  assert.equal(first.run.phase, 'INDEX_PROJECTS');
  assert.ok(first.run.indexed_projects > 0 && first.run.indexed_projects < 160);
  assert.equal(count(db, 'institution_pattern_events', run.id), first.run.indexed_projects);
  const finished = await finish(run.id);
  assert.equal(finished.run.indexed_projects, 160); assert.equal(finished.run.processed_projects, 160);
  assert.equal(count(db, 'institution_pattern_events', run.id), 160);
});

test('bounded aggregate group output commits only consumed rows and result pagination returns every group', async t => {
  const { db } = setup(t), source = seed(db, { projects: 103, requests: 0,
    projectOverrides: n => ({ org: '인천광역시 합성기관' + String(n).padStart(4, '0') }) }), { run } = await create(source);
  await service.institutionStep(run.id, source.mode); await service.institutionStep(run.id, source.mode);
  const first = await service.institutionStep(run.id, source.mode);
  assert.equal(first.run.phase, 'AGGREGATE'); assert.equal(first.run.completed_groups, 8);
  assert.ok(first.run.processed_projects < 103);
  const state = runRow(db, run.id), partial = JSON.parse(state.partial_json);
  assert.deepEqual(JSON.parse(state.cursor_json).key, partial.lastKey);
  const finished = await finish(run.id);
  assert.equal(finished.run.processed_projects, 103); assert.equal(finished.run.completed_groups, 103);
  assert.equal(count(db, 'institution_pattern_years', run.id), 103);
  const params = new URLSearchParams({ mode: source.mode, id: run.id }), all = [], sizes = [];
  for (;;) {
    const result = await service.institutionResults(params); all.push(...result.rows); sizes.push(result.rows.length);
    if (!result.next) break;
    params.set('after', result.next);
  }
  assert.deepEqual(sizes, [50, 50, 3]); assert.equal(new Set(all.map(r => r.org_name)).size, 103);
  assert.ok(all.every(r => r.observed_budget_projects === 1));
});

test('strict lease expiry without takeover rejects a delayed commit and writes no events', async t => {
  const { db, hooks } = setup(t), source = seed(db), { run } = await create(source);
  hooks.beforeBatch = () => {
    hooks.beforeBatch = null;
    db.prepare('UPDATE institution_pattern_runs SET lease_until=0 WHERE id=?').run(run.id);
  };
  await assert.rejects(service.institutionStep(run.id, source.mode), /PATTERN_LEASE_EXPIRED/);
  const state = runRow(db, run.id);
  assert.equal(state.state, 'error'); assert.equal(state.error, 'PATTERN_LEASE_EXPIRED');
  assert.equal(state.indexed_projects, 0); assert.equal(count(db, 'institution_pattern_events', run.id), 0);
  await service.institutionState(run.id, source.mode, 'ready');
  assert.equal((await finish(run.id)).run.completed_groups, 1);
});

for (const [field, value] of [['state', 'running'], ['phase', 'MATCH'], ['engine_version', 'v17'], ['partial', '{}']]) {
  test(`source ${field} changes before commit invalidate the frozen source`, async t => {
    const { db, hooks } = setup(t), source = seed(db), { run } = await create(source);
    hooks.beforeBatch = () => {
      hooks.beforeBatch = null;
      db.prepare(`UPDATE matching_jobs SET ${field}=? WHERE id=?`).run(value, source.id);
    };
    await assert.rejects(service.institutionStep(run.id, source.mode), /PATTERN_SOURCE_STALE|PATTERN_SOURCE_CHANGED_OR_LEASE_LOST/);
    assert.equal(count(db, 'institution_pattern_events', run.id), 0);
    assert.equal(runRow(db, run.id).indexed_projects, 0);
    const workspace = await service.institutionWorkspace(source.mode, run.id);
    assert.equal(workspace.run.stale, true);
    const listed = workspace.sources.find(s => s.id === source.id);
    if (listed) assert.equal(listed.usable, false);
  });
}

test('a final response loss leaves completion readable and never re-emits aggregate output', async t => {
  const { db, hooks } = setup(t), source = seed(db), { run } = await create(source);
  await service.institutionStep(run.id, source.mode); await service.institutionStep(run.id, source.mode);
  hooks.afterCommit = () => { hooks.afterCommit = null; throw Error('TEST_FINAL_RESPONSE_LOST'); };
  await assert.rejects(service.institutionStep(run.id, source.mode), /TEST_FINAL_RESPONSE_LOST/);
  const readback = await service.institutionWorkspace(source.mode, run.id);
  assert.equal(readback.run.state, 'complete'); assert.equal(readback.run.completed_groups, 1);
  assert.equal((await service.institutionResults(new URLSearchParams({ mode: source.mode, id: run.id }))).rows.length, 1);
  await assert.rejects(service.institutionStep(run.id, source.mode), /PATTERN_BUSY_PAUSED_OR_COMPLETE/);
  assert.equal(count(db, 'institution_pattern_years', run.id), 1);
});

test('missing projected events cannot silently pass the aggregate source reconciliation', async t => {
  const { db } = setup(t), source = seed(db), { run } = await create(source);
  await service.institutionStep(run.id, source.mode); await service.institutionStep(run.id, source.mode);
  db.prepare("DELETE FROM institution_pattern_events WHERE run_id=? AND kind='REQUEST'").run(run.id);
  await assert.rejects(service.institutionStep(run.id, source.mode), /PATTERN_AGGREGATE_COUNT_MISMATCH/);
  assert.equal(runRow(db, run.id).processed_projects, 0); assert.equal(count(db, 'institution_pattern_years', run.id), 0);
});

test('oversized partial state is rejected before any transaction is prepared', async t => {
  const { db } = setup(t), source = seed(db), { run } = await create(source);
  assert.throws(() => institutionCommitPlan(runRow(db, run.id), 'unused', { phase: 'AGGREGATE', cursor: {},
    partial: { text: '한'.repeat(22000) } }, [], [], '2026-11-07T00:00:00Z'), /PATTERN_PARTIAL_TOO_LARGE/);
  assert.equal(count(db, 'institution_pattern_events', run.id), 0);
});

test('source match-count mismatch preserves the completed project phase but no request outputs', async t => {
  const { db } = setup(t), source = seed(db, { jobOverrides: { match_count: 2 } }), { run } = await create(source);
  await service.institutionStep(run.id, source.mode);
  await assert.rejects(service.institutionStep(run.id, source.mode), /PATTERN_SOURCE_COUNT_MISMATCH/);
  const state = runRow(db, run.id);
  assert.equal(state.phase, 'INDEX_REQUESTS'); assert.equal(state.indexed_projects, 1); assert.equal(state.indexed_requests, 0);
  assert.equal(count(db, 'institution_pattern_events', run.id), 1); assert.equal(state.cursor_json, '{}');
});

test('out-of-scope requests remain counted as excluded without changing source winner assignment', async t => {
  const { db } = setup(t), source = seed(db, { requests: 2,
    requestOverrides: n => n ? { source_date: '2025-12-31' } : {} }), before = sourceSnapshot(db), { run } = await create(source);
  const w = await finish(run.id);
  assert.equal(w.run.indexed_requests, 2); assert.equal(w.run.excluded_requests, 1); assert.equal(w.run.processed_requests, 1);
  assert.equal(count(db, 'institution_pattern_events', run.id), 3);
  const row = (await service.institutionResults(new URLSearchParams({ mode: source.mode, id: run.id }))).rows[0];
  assert.equal(row.high_match_rows, 1); assert.equal(row.high_matched_budget_projects, 1);
  assert.equal(sourceSnapshot(db), before);
});

test('source metadata changing during result reads is rejected and then marked stale', async t => {
  const { db, hooks } = setup(t), source = seed(db), { run } = await create(source);
  await finish(run.id);
  hooks.afterRead = sql => {
    if (sql.includes('FROM institution_pattern_years')) {
      hooks.afterRead = null;
      db.prepare("UPDATE matching_jobs SET engine_version='v17' WHERE id=?").run(source.id);
    }
  };
  const params = new URLSearchParams({ mode: source.mode, id: run.id });
  await assert.rejects(service.institutionResults(params), /PATTERN_SOURCE_CHANGED_RETRY/);
  const result = await service.institutionResults(params);
  assert.equal(result.stale, true); assert.equal(result.run.stale, true); assert.equal(result.rows.length, 1);
});

test('creation races are fenced at INSERT and retry request IDs cannot select a different source job', async t => {
  const { db, hooks } = setup(t), source = seed(db), other = seed(db), key = requestId();
  const first = await create(source, key);
  await assert.rejects(create(other, key), /PATTERN_REQUEST_CONFLICT/);
  await service.institutionState(first.run.id, source.mode, 'cancelled');
  hooks.afterRead = sql => {
    if (sql.startsWith('SELECT CAST(d.revision AS TEXT)')) {
      hooks.afterRead = null; db.exec("UPDATE dataset_epochs SET revision=revision+1 WHERE mode='synthetic'");
    }
  };
  await assert.rejects(create(other), /PATTERN_SOURCE_CHANGED_OR_ACTIVE_RUN/);
  assert.equal(db.prepare('SELECT COUNT(*) n FROM institution_pattern_runs').get().n, 1);
});
