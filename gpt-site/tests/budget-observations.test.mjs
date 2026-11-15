import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync, readdirSync} from 'node:fs';
import {budgetCommitPlan, BUDGET_ACQUIRE} from '../lib/budget-sql.mjs';
import {budgetObservationPlan} from '../lib/budget-observations.mjs';
import {normalizeBudget} from '../lib/budget-domain.mjs';

const migrations = readdirSync(new URL('../drizzle/', import.meta.url)).filter(file => file.endsWith('.sql')).sort();
function migrate(db, files = migrations) {
  for (const file of files) db.exec(readFileSync(new URL('../drizzle/' + file, import.meta.url), 'utf8'));
}
function setup(t, files) {
  const db = new DatabaseSync(':memory:');
  db.exec('PRAGMA foreign_keys=ON');
  migrate(db, files);
  t.after(() => db.close());
  return db;
}
function session(db, id, date = '2026-11-05', {mode = 'synthetic', historyOnly = 0, service = 'QWGJK'} = {}) {
  db.prepare(`INSERT INTO budget_sessions(id,mode,service,fiscal_year,snapshot_date,history_only,state,page,page_size,source_total,fetched,saw_full,fixture_total,max_payload,lease,lease_until,error,created_at,updated_at)
    VALUES(?,?,?,2026,?,?,'ready',1,1000,NULL,0,0,2,0,NULL,0,NULL,'2026-11-07T00:00:00.000Z','2026-11-07T00:00:00.000Z')`).run(id, mode, service, date, historyOnly);
  return acquire(db, id);
}
function acquire(db, id) {
  assert.equal(db.prepare(BUDGET_ACQUIRE).run('token-' + id, 99999, id, 0).changes, 1);
  return db.prepare('SELECT * FROM budget_sessions WHERE id=?').get(id);
}
const raw = (key = 'one', executed = '20') => ({fyr: '2026', dbiz_cd: key, dbiz_nm: '조명 ' + key, bdg_cash_amt: '100', ep_amt: executed});
async function plan(s, raws, {stamp = '2026-11-07T01:00:00.000Z', done = true, total = s.fetched + raws.length} = {}) {
  const rows = await Promise.all(raws.map(row => normalizeBudget(row, s)));
  return budgetCommitPlan(s, s.lease, rows, {done, total, sawFull: done ? 0 : 1}, 100, 'page-hash', stamp);
}
function execute(db, statements, after = () => {}) {
  db.exec('BEGIN');
  try {
    for (const statement of statements) {
      db.prepare(statement.sql).run(...statement.params);
      after(statement);
    }
    db.exec('COMMIT');
  } catch (error) {
    db.exec('ROLLBACK');
    throw error;
  }
}
async function commit(db, s, raws, options) { execute(db, await plan(s, raws, options)); }
const epoch = (db, mode = 'synthetic') => db.prepare('SELECT revision FROM budget_observation_epochs WHERE mode=?').get(mode)?.revision || 0;
const count = (db, table) => db.prepare('SELECT COUNT(*) n FROM ' + table).get().n;
const observations = db => db.prepare('SELECT * FROM budget_observations ORDER BY snapshot_date,source_key,payload_hash').all();

test('migration starts an empty ledger and never invents observations from legacy unique states', async t => {
  const db = setup(t, migrations.filter(file => !/^0013_/.test(file)));
  const row = await normalizeBudget(raw(), {service: 'QWGJK', fiscal_year: 2026, snapshot_date: '2026-01-01'});
  db.prepare('INSERT INTO budget_revisions VALUES(?,?,?,?,?,?,?)').run('synthetic', 'QWGJK', row.source_key, row.payload_hash, JSON.stringify(row), '2026-11-07T00:00:00.000Z', '2026-01-01');
  const newMigrations = migrations.filter(file => /^0013_/.test(file));
  assert.equal(newMigrations.length, 1);
  migrate(db, newMigrations);
  assert.equal(count(db, 'budget_observations'), 0);
  assert.equal(count(db, 'budget_revisions'), 1);
  assert.equal(epoch(db), 0);
  await commit(db, session(db, 'observed-later', '2026-11-05', {historyOnly: 1}), [raw()]);
  assert.deepEqual(observations(db).map(row => row.snapshot_date), ['2026-11-05']);
  assert.equal(count(db, 'budget_revisions'), 1);
  assert.equal(db.prepare('SELECT first_snapshot FROM budget_revisions').get().first_snapshot, '2026-01-01');
});

test('A to B to A on separate days retains three observations pointing to two payloads', async t => {
  const db = setup(t);
  await commit(db, session(db, 'a', '2026-11-01'), [raw('one', '20')]);
  await commit(db, session(db, 'b', '2026-11-02'), [raw('one', '40')]);
  await commit(db, session(db, 'a-again', '2026-11-03'), [raw('one', '20')]);
  const seen = observations(db);
  assert.equal(seen.length, 3);
  assert.equal(count(db, 'budget_revisions'), 2);
  assert.equal(seen[0].payload_hash, seen[2].payload_hash);
  assert.notEqual(seen[0].payload_hash, seen[1].payload_hash);
  assert.deepEqual(seen.map(row => row.snapshot_date), ['2026-11-01', '2026-11-02', '2026-11-03']);
  assert.equal(db.prepare('SELECT first_snapshot FROM budget_revisions WHERE payload_hash=?').get(seen[2].payload_hash).first_snapshot, '2026-11-01');
  assert.equal(db.prepare('SELECT snapshot_date FROM budget_rows').get().snapshot_date, '2026-11-03');
  const fields = db.prepare('PRAGMA table_info(budget_observations)').all().map(row => row.name);
  assert.ok(!fields.includes('raw') && !fields.includes('row_json'));
  assert.equal(db.prepare(`SELECT COUNT(*) n FROM budget_observations o JOIN budget_revisions r
    ON r.mode=o.mode AND r.service=o.service AND r.source_key=o.source_key AND r.payload_hash=o.payload_hash`).get().n, 3);
});

test('same-day same-hash recollection is idempotent while first/last retrieval and last session stay accurate', async t => {
  const db = setup(t), date = '2026-11-05';
  await commit(db, session(db, 'first', date), [raw()], {stamp: '2026-11-07T01:00:00.000Z'});
  const before = epoch(db);
  await commit(db, session(db, 'repeat', date, {historyOnly: 1}), [raw()], {stamp: '2026-11-07T02:00:00.000Z'});
  assert.equal(count(db, 'budget_observations'), 1);
  assert.equal(count(db, 'budget_revisions'), 1);
  assert.ok(epoch(db) > before);
  assert.equal(observations(db)[0].first_seen, '2026-11-07T01:00:00.000Z');
  assert.equal(observations(db)[0].last_seen, '2026-11-07T02:00:00.000Z');
  assert.equal(observations(db)[0].last_session, 'repeat');
  await commit(db, session(db, 'older-clock', date, {historyOnly: 1}), [raw()], {stamp: '2026-11-07T00:00:00.000Z'});
  assert.equal(observations(db)[0].first_seen, '2026-11-07T00:00:00.000Z');
  assert.equal(observations(db)[0].last_seen, '2026-11-07T02:00:00.000Z');
  assert.equal(observations(db)[0].last_session, 'repeat');
});

test('different same-day payloads are retained without inventing intraday transition order', async t => {
  const db = setup(t);
  for (const [id, value, stamp] of [['a', '20', '01'], ['b', '40', '02'], ['a-again', '20', '03']]) {
    await commit(db, session(db, id), [raw('one', value)], {stamp: `2026-11-07T${stamp}:00:00.000Z`});
  }
  assert.equal(count(db, 'budget_observations'), 2);
  assert.equal(count(db, 'budget_revisions'), 2);
  const repeated = observations(db).find(row => row.last_session === 'a-again');
  assert.equal(repeated.first_seen, '2026-11-07T01:00:00.000Z');
  assert.equal(repeated.last_seen, '2026-11-07T03:00:00.000Z');
});

test('out-of-order older observations retain source dates without changing first-stored revision or current data', async t => {
  const db = setup(t);
  await commit(db, session(db, 'newer', '2026-11-05'), [raw()], {stamp: '2026-11-06T00:00:00.000Z'});
  const current = db.prepare('SELECT * FROM budget_rows').all();
  await commit(db, session(db, 'older', '2026-11-02', {historyOnly: 1}), [raw()], {stamp: '2026-11-07T00:00:00.000Z'});
  assert.deepEqual(observations(db).map(row => [row.snapshot_date, row.first_seen]), [
    ['2026-11-02', '2026-11-07T00:00:00.000Z'], ['2026-11-05', '2026-11-06T00:00:00.000Z'],
  ]);
  assert.equal(db.prepare('SELECT first_snapshot FROM budget_revisions').get().first_snapshot, '2026-11-05');
  assert.deepEqual(db.prepare('SELECT * FROM budget_rows').all(), current);
});

test('history-only collection never changes current rows or matching epoch and observations do not imply a complete day', async t => {
  const db = setup(t);
  await commit(db, session(db, 'current'), [raw()]);
  const current = db.prepare('SELECT * FROM budget_rows').all();
  const currentEpoch = db.prepare('SELECT * FROM budget_epochs').all();
  await commit(db, session(db, 'partial', '2026-11-01', {historyOnly: 1}), [raw('historical')], {done: false, total: 2});
  assert.equal(count(db, 'budget_observations'), 2);
  assert.equal(db.prepare("SELECT state FROM budget_sessions WHERE id='partial'").get().state, 'ready');
  assert.equal(db.prepare("SELECT COUNT(*) n FROM budget_sessions WHERE snapshot_date='2026-11-01' AND state='complete'").get().n, 0);
  await commit(db, session(db, 'empty', '2026-11-02', {historyOnly: 1}), []);
  assert.equal(db.prepare("SELECT state FROM budget_sessions WHERE id='empty'").get().state, 'complete');
  assert.equal(db.prepare("SELECT COUNT(*) n FROM budget_observations WHERE snapshot_date='2026-11-02'").get().n, 0);
  assert.deepEqual(db.prepare('SELECT * FROM budget_rows').all(), current);
  assert.deepEqual(db.prepare('SELECT * FROM budget_epochs').all(), currentEpoch);
});

test('observation identity and mutation epochs isolate live/synthetic modes and exclude AIDFA', async t => {
  const db = setup(t);
  await commit(db, session(db, 'synthetic'), [raw()]);
  const before = epoch(db);
  await commit(db, session(db, 'live', '2026-11-05', {mode: 'live', historyOnly: 1}), [raw()]);
  assert.equal(epoch(db), before);
  assert.ok(epoch(db, 'live') > 0);
  assert.deepEqual(observations(db).map(row => row.mode).sort(), ['live', 'synthetic']);
  await commit(db, session(db, 'aidfa', '2026-11-05', {service: 'AIDFA'}), [{fyr: '2026', laf_cd: 'O', fld_cd: 'F', sect_cd: 'S', acnt_dv_cd: 'A', biz_bdg_tott_amt: '100'}]);
  assert.equal(epoch(db), before);
  assert.equal(count(db, 'budget_observations'), 2);
  assert.equal(count(db, 'budget_revisions'), 3);
  assert.deepEqual(budgetObservationPlan({service: 'AIDFA'}, [], '', '', []), []);
});

test('lost commit response and repeated stale resume leave no duplicate observations, receipts or epoch increments', async t => {
  const db = setup(t), s = session(db, 'resume');
  const firstPage = await plan(s, [raw('one')], {done: false, total: 2});
  execute(db, firstPage); // The commit succeeded even if the caller lost its response.
  const before = observations(db), revision = epoch(db);
  for (let retry = 0; retry < 3; retry++) execute(db, firstPage);
  assert.deepEqual(observations(db), before);
  assert.equal(epoch(db), revision);
  assert.equal(count(db, 'budget_receipts'), 1);
  assert.equal(db.prepare('SELECT page FROM budget_sessions').get().page, 2);
  const resumed = acquire(db, s.id), lastPage = await plan(resumed, [raw('two')], {total: 2});
  execute(db, lastPage);
  const final = observations(db), finalEpoch = epoch(db);
  for (let retry = 0; retry < 3; retry++) execute(db, lastPage);
  assert.deepEqual(observations(db), final);
  assert.equal(epoch(db), finalEpoch);
  assert.equal(count(db, 'budget_observations'), 2);
  assert.equal(count(db, 'budget_receipts'), 2);
  assert.equal(db.prepare('SELECT state FROM budget_sessions').get().state, 'complete');
});

test('failure after observation insert or update rolls back all rows, receipts, epochs and checkpoint', async t => {
  const db = setup(t);
  await commit(db, session(db, 'existing'), [raw()]);
  const s = session(db, 'failing', '2026-11-05', {historyOnly: 1});
  const tables = ['budget_observations', 'budget_revisions', 'budget_rows', 'budget_receipts', 'budget_seen', 'budget_sessions', 'budget_epochs', 'budget_history_epochs', 'budget_observation_epochs'];
  const before = tables.map(table => db.prepare('SELECT * FROM ' + table).all());
  const statements = await plan(s, [raw(), raw('new')], {stamp: '2026-11-07T05:00:00.000Z'});
  assert.throws(() => execute(db, statements, query => {
    if (query.sql.startsWith('INSERT INTO budget_observations')) throw Error('INJECTED_AFTER_OBSERVATION_WRITE');
  }), /INJECTED_AFTER_OBSERVATION_WRITE/);
  assert.deepEqual(tables.map(table => db.prepare('SELECT * FROM ' + table).all()), before);
  execute(db, statements);
  assert.equal(count(db, 'budget_observations'), 2);
  assert.equal(observations(db).find(row => row.last_session === s.id).last_seen, '2026-11-07T05:00:00.000Z');
});

test('receipt failure, duplicate source key and late count failure cannot commit observation evidence', async t => {
  for (const failure of ['receipt', 'duplicate', 'count']) {
    const db = setup(t), s = session(db, failure);
    let statements = await plan(s, failure === 'duplicate' ? [raw(), raw()] : [raw()]);
    if (failure === 'receipt') db.exec("CREATE TRIGGER fail_receipt BEFORE INSERT ON budget_receipts BEGIN SELECT RAISE(ABORT,'INJECTED_RECEIPT_FAILURE'); END");
    if (failure === 'count') statements = await plan({...s, fetched: 1}, [raw()]);
    assert.throws(() => execute(db, statements));
    for (const table of ['budget_observations', 'budget_revisions', 'budget_rows', 'budget_receipts', 'budget_seen']) assert.equal(count(db, table), 0, failure + ' ' + table);
    assert.equal(epoch(db), 0);
    assert.equal(db.prepare('SELECT page FROM budget_sessions').get().page, 1);
  }
});

test('stale lease, cancelled source session and wrong page all fence observations with the existing page transaction', async t => {
  for (const change of ["lease='different'", "lease=NULL,state='cancelled'", 'page=2']) {
    const db = setup(t), s = session(db, 'fenced'), statements = await plan(s, [raw()]);
    db.exec('UPDATE budget_sessions SET ' + change);
    execute(db, statements);
    for (const table of ['budget_observations', 'budget_revisions', 'budget_rows', 'budget_receipts', 'budget_seen']) assert.equal(count(db, table), 0, change + ' ' + table);
    assert.equal(epoch(db), 0);
  }
});

test('referenced payload mutation invalidates observation epoch while foreign keys reject orphaning and no unrelated mutation leaks across modes', async t => {
  const db = setup(t);
  await commit(db, session(db, 'observed'), [raw()]);
  const seen = observations(db)[0], before = epoch(db);
  db.prepare("UPDATE budget_revisions SET row_json=json_set(row_json,'$.project','corrected') WHERE mode='synthetic' AND source_key=? AND payload_hash=?").run(seen.source_key, seen.payload_hash);
  assert.ok(epoch(db) > before);
  assert.equal(epoch(db, 'live'), 0);
  const updated = epoch(db);
  assert.throws(() => db.prepare('DELETE FROM budget_revisions WHERE mode=? AND source_key=? AND payload_hash=?').run(seen.mode, seen.source_key, seen.payload_hash), /FOREIGN KEY/);
  assert.equal(epoch(db), updated);
  assert.equal(count(db, 'budget_observations'), 1);
  assert.throws(() => db.prepare('INSERT INTO budget_observations VALUES(?,?,?,?,?,?,?,?,?)').run('synthetic', 'QWGJK', 'missing', '2026-11-05', 'missing', 2026, 'now', 'now', 'observed'), /FOREIGN KEY/);
  const unobserved = await normalizeBudget(raw('unobserved'), {service: 'QWGJK', fiscal_year: 2026, snapshot_date: '2026-11-04'});
  db.prepare('INSERT INTO budget_revisions VALUES(?,?,?,?,?,?,?)').run('synthetic', 'QWGJK', unobserved.source_key, unobserved.payload_hash, JSON.stringify(unobserved), 'now', '2026-11-04');
  db.prepare("UPDATE budget_revisions SET first_seen='later' WHERE source_key=?").run(unobserved.source_key);
  db.prepare('DELETE FROM budget_revisions WHERE source_key=?').run(unobserved.source_key);
  assert.equal(epoch(db), updated);
  db.prepare('DELETE FROM budget_observations WHERE source_key=?').run(seen.source_key);
  assert.ok(epoch(db) > updated);
  const deleted = epoch(db);
  db.prepare('DELETE FROM budget_revisions WHERE source_key=?').run(seen.source_key);
  assert.equal(epoch(db), deleted);
});

test('ledger mode changes invalidate both affected modes and remain bound to an existing payload', async t => {
  const db = setup(t);
  await commit(db, session(db, 'one'), [raw()]);
  db.exec("INSERT INTO budget_revisions SELECT 'live',service,source_key,payload_hash,row_json,first_seen,first_snapshot FROM budget_revisions WHERE mode='synthetic'");
  const before = epoch(db);
  assert.equal(epoch(db, 'live'), 0);
  db.exec("UPDATE budget_observations SET mode='live'");
  assert.ok(epoch(db) > before);
  assert.ok(epoch(db, 'live') > 0);
  assert.equal(observations(db)[0].mode, 'live');
});

test('observation keys use bounded JSON parameters without repeating raw payloads or exceeding bind limits', () => {
  const key = '가'.repeat(2400), rows = Array.from({length: 600}, (_, n) => ({source_key: key + n, payload_hash: 'a'.repeat(64), raw: 'RAW_PAYLOAD_MUST_NOT_BE_REPEATED'}));
  const statements = budgetObservationPlan({mode: 'synthetic', service: 'QWGJK', id: 's', fiscal_year: 2026, snapshot_date: '2026-11-05'}, rows, '2026-11-07T00:00:00.000Z', 'EXISTS(SELECT 1 FROM budget_sessions WHERE id=? AND lease=? AND page=?)', ['s', 'token', 1]);
  assert.ok(statements.length > 1);
  let total = 0;
  for (const query of statements) {
    assert.ok(query.params.length < 100);
    const chunk = query.params.find(value => typeof value === 'string' && value.startsWith('[{'));
    assert.ok(Buffer.byteLength(chunk) <= 1400000);
    assert.ok(!chunk.includes('RAW_PAYLOAD_MUST_NOT_BE_REPEATED'));
    for (const row of JSON.parse(chunk)) assert.deepEqual(Object.keys(row).sort(), ['payload_hash', 'source_key']);
    total += JSON.parse(chunk).length;
  }
  assert.equal(total, rows.length);
});
