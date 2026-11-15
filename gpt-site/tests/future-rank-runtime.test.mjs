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
globalThis.__g2bFutureEnv = env;
registerHooks({ resolve(spec, context, next) {
  if (spec === 'cloudflare:workers') return { url: 'data:text/javascript,export const env=globalThis.__g2bFutureEnv', shortCircuit: true };
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

const future=await import('../lib/future-service.ts');
const {mergeFutureHistory}=await import('../lib/future-history.mjs');
const {futureHistoryQuery,futureYearsQuery,FUTURE_QUERY_VERSION}=await import('../lib/future-query.mjs');
const rank=await import('../lib/future-rank-service.ts');
const {FUTURE_RANK_ENGINE}=await import('../lib/future-rank-store.mjs');
async function completed(db,options={}){const source=seed(db,options);let w=await service.createInstitutionRun(source.mode,source.id,crypto.randomUUID());for(let n=0;n<100&&w.run.state!=='complete';n++)w=await service.institutionStep(w.run.id,source.mode);assert.equal(w.run.state,'complete');return w.run;}
function target(db,n=0,overrides={}){const row={source_key:'T'+String(n).padStart(4,'0'),payload_hash:'h-'+n,layer:'APPROPRIATION',fiscal_year:2027,snapshot_date:'2026-11-07',region:'인천광역시',org:'인천광역시 옹진군',dept:'',project:'LED 보안등 조명',category:'LIGHTING',account:'일반회계',budget_amount:'9007199254740993123456789',appropriation_amount:'9007199254740993123456789',remaining_amount:null,invalid_amount:0,raw:'{}',...overrides};insert(db,'budget_rows',{mode:'synthetic',service:'AIDFA',source_key:row.source_key,payload_hash:row.payload_hash,fiscal_year:row.fiscal_year,snapshot_date:row.snapshot_date,category:row.category,region:row.region,org:row.org,dept:row.dept,project:row.project,row_json:JSON.stringify(row),is_active:1,last_session:'synthetic-fixture',updated_at:'2026-11-07T00:00:00Z'});return row;}
const params=(id,more={})=>new URLSearchParams({action:'results',mode:'synthetic',id,year:'2027',service:'AIDFA',...more});
function summary(db,run){return JSON.parse(db.prepare('SELECT row_json FROM institution_pattern_years WHERE run_id=?').get(run.id).row_json);}


const createRank=(run,over={})=>rank.createFutureRank('synthetic',run.id,crypto.randomUUID(),{year:'2027',service:'AIDFA',...over.options},over.limit??200);
async function finishRank(id,max=25){for(let n=0;n<max;n++){const r=await rank.futureRankStep(id,'synthetic');if(r.run.state==='complete')return r;}throw Error('TEST_RANK_INCOMPLETE');}
const resultParams=id=>new URLSearchParams({mode:'synthetic',id,action:'results'});
function sourceState(db){return JSON.stringify(['matching_jobs','matching_budgets','matching_requests','matching_results','institution_pattern_runs','institution_pattern_years','institution_pattern_aliases','budget_rows','source_usage'].map(table=>[table,db.prepare('SELECT * FROM '+table+' ORDER BY rowid').all()]));}
test('ranking has a declared latest-source cap, no global claim, stable pause/reload/resume and exact source preservation',async t=>{const{db}=setup(t),institution=await completed(db);for(let n=0;n<61;n++)target(db,n,{budget_amount:String(n),appropriation_amount:String(n)});const before=sourceState(db),created=await createRank(institution,{limit:50}),id=created.run.id;assert.equal(created.run.state,'ready');let r=await rank.futureRankStep(id,'synthetic');assert.equal(r.run.examined,25);r=await rank.futureRankControl(id,'synthetic','paused');assert.equal(r.run.state,'paused');assert.equal((await rank.futureRankWorkspace('synthetic',id)).run.examined,25);await assert.rejects(rank.futureRankStep(id,'synthetic'),/BUSY_PAUSED_OR_COMPLETE/);await rank.futureRankControl(id,'synthetic','ready');r=await finishRank(id);assert.equal(r.run.examined,50);assert.equal(r.run.selectionTruncated,true);assert.equal(r.run.sourceExhausted,false);assert.equal(r.run.globalRanking,false);const first=await rank.futureRankResults(resultParams(id));assert.equal(first.rows.length,25);assert.equal(first.rows[0].source_key,'T0049');const p=resultParams(id);p.set('after',first.next);const last=await rank.futureRankResults(p);assert.equal(last.rows.length,25);assert.equal(last.next,null);assert.equal(new Set([...first.rows,...last.rows].map(r=>r.source_key)).size,50);assert.equal(sourceState(db),before);});
test('empty and exactly exhausted selections are distinct from cap truncation',async t=>{const{db}=setup(t),institution=await completed(db),empty=await createRank(institution);const a=await finishRank(empty.run.id);assert.equal(a.run.examined,0);assert.equal(a.run.sourceExhausted,true);assert.equal(a.run.selectionTruncated,false);assert.deepEqual((await rank.futureRankResults(resultParams(a.run.id))).rows,[]);for(let n=0;n<25;n++)target(db,n);const full=await createRank(institution,{limit:25}),b=await finishRank(full.run.id);assert.equal(b.run.examined,25);assert.equal(b.run.sourceExhausted,true);assert.equal(b.run.selectionTruncated,false);});
test('request identifiers bind exact mode, institution source, filters and cap',async t=>{const{db}=setup(t),institution=await completed(db),key=crypto.randomUUID(),opts={year:'2027',service:'AIDFA'},a=await rank.createFutureRank('synthetic',institution.id,key,opts,200),b=await rank.createFutureRank('synthetic',institution.id,key,opts,200);assert.equal(a.run.id,b.run.id);for(const changed of [{limit:500},{options:{...opts,year:'2028'}}])await assert.rejects(rank.createFutureRank('synthetic',institution.id,key,changed.options||opts,changed.limit||200),/REQUEST_CONFLICT/);await assert.rejects(createRank(institution),/SOURCE_CHANGED_OR_ACTIVE/);for(const limit of [0,501,1.5])await assert.rejects(createRank(institution,{limit}),/INVALID/);await assert.rejects(rank.futureRankWorkspace('live',a.run.id),/MODE_MISMATCH/);});
test('ranking cannot show partial results or resume invalid source evidence',async t=>{const{db}=setup(t),institution=await completed(db);target(db);const a=await createRank(institution);await assert.rejects(rank.futureRankResults(resultParams(a.run.id)),/NOT_COMPLETE/);db.exec("UPDATE budget_epochs SET revision=revision+1 WHERE mode='synthetic'");await assert.rejects(rank.futureRankStep(a.run.id,'synthetic'),/SOURCE_STALE/);assert.equal(db.prepare('SELECT COUNT(*) n FROM future_rank_rows').get().n,0);assert.equal((await rank.futureRankWorkspace('synthetic',a.run.id)).run.stale,true);});
test('rows and cursor roll back together on a forced last-statement failure',async t=>{const{db,hooks}=setup(t),institution=await completed(db);for(let n=0;n<28;n++)target(db,n);const a=await createRank(institution);hooks.beforeStatement=s=>{if(s.sql.startsWith('UPDATE future_rank_runs SET state='))throw Error('SIMULATED_COMMIT_FAILURE');};await assert.rejects(rank.futureRankStep(a.run.id,'synthetic'));assert.equal(db.prepare('SELECT COUNT(*) n FROM future_rank_rows').get().n,0);assert.equal(db.prepare('SELECT examined FROM future_rank_runs').get().examined,0);hooks.beforeStatement=null;await rank.futureRankControl(a.run.id,'synthetic','ready');const done=await finishRank(a.run.id);assert.equal(done.run.ranked,28);});
test('source mutation immediately before commit fences all rows and checkpoint',async t=>{const{db,hooks}=setup(t),institution=await completed(db);target(db);const a=await createRank(institution);hooks.beforeBatch=()=>db.exec("UPDATE dataset_epochs SET revision=revision+1 WHERE mode='synthetic'");await assert.rejects(rank.futureRankStep(a.run.id,'synthetic'));assert.equal(db.prepare('SELECT COUNT(*) n FROM future_rank_rows').get().n,0);assert.equal(db.prepare('SELECT examined FROM future_rank_runs').get().examined,0);});
test('expired lease and takeover cannot commit a stale rank page',async t=>{const{db,hooks}=setup(t),institution=await completed(db);target(db);const a=await createRank(institution);hooks.beforeBatch=()=>db.prepare('UPDATE future_rank_runs SET generation=generation+1,lease=?,lease_until=? WHERE id=?').run('replacement',Date.now()+90000,a.run.id);await assert.rejects(rank.futureRankStep(a.run.id,'synthetic'));assert.equal(db.prepare('SELECT COUNT(*) n FROM future_rank_rows').get().n,0);assert.equal(db.prepare('SELECT lease FROM future_rank_runs').get().lease,'replacement');});
test('lost committed response is read back without repeating a completed page',async t=>{const{db,hooks}=setup(t),institution=await completed(db);for(let n=0;n<28;n++)target(db,n);const a=await createRank(institution);let once=true;hooks.afterCommit=()=>{if(once){once=false;throw Error('SIMULATED_RESPONSE_LOSS');}};await assert.rejects(rank.futureRankStep(a.run.id,'synthetic'));const recovered=await rank.futureRankWorkspace('synthetic',a.run.id);assert.equal(recovered.run.examined,25);assert.equal(recovered.run.state,'ready');const done=await finishRank(a.run.id);assert.equal(done.run.examined,28);assert.equal(db.prepare('SELECT COUNT(*) n FROM future_rank_rows').get().n,28);});
test('completed rank survives source changes only as a stale historical result and cursor is run-bound',async t=>{const{db}=setup(t),institution=await completed(db);for(let n=0;n<30;n++)target(db,n);const a=await createRank(institution);await finishRank(a.run.id);const first=await rank.futureRankResults(resultParams(a.run.id));db.exec("UPDATE budget_epochs SET revision=revision+1 WHERE mode='synthetic'");const historic=await rank.futureRankResults(resultParams(a.run.id));assert.equal(historic.stale,true);assert.deepEqual(historic.rows,first.rows);const cursor=JSON.parse(first.next);cursor.run=crypto.randomUUID();const p=resultParams(a.run.id);p.set('after',JSON.stringify(cursor));await assert.rejects(rank.futureRankResults(p),/INVALID_FUTURE_RANK_CURSOR/);});
test('rank rows have source foreign keys, unique identity and ordered index',async t=>{const{db}=setup(t),institution=await completed(db);target(db);const a=await createRank(institution);await finishRank(a.run.id);const plan=db.prepare('EXPLAIN QUERY PLAN SELECT * FROM future_rank_rows WHERE run_id=? AND rank_key>? ORDER BY rank_key LIMIT 25').all(a.run.id,'').map(r=>r.detail).join(' ');assert.match(plan,/idx_future_rank_order/);const r=db.prepare('SELECT * FROM future_rank_rows').get();assert.throws(()=>insert(db,'future_rank_rows',r),/UNIQUE/);assert.throws(()=>insert(db,'future_rank_rows',{...r,run_id:'unknown'}),/FOREIGN KEY/);});

test('a second simultaneous step cannot obtain the held lease or duplicate rows',async t=>{const{db,hooks}=setup(t),institution=await completed(db);for(let n=0;n<28;n++)target(db,n);const a=await createRank(institution);let checked=false;hooks.beforeBatch=async()=>{if(!checked){checked=true;await assert.rejects(rank.futureRankStep(a.run.id,'synthetic'),/BUSY_PAUSED_OR_COMPLETE/);}};const first=await rank.futureRankStep(a.run.id,'synthetic');assert.equal(first.run.examined,25);assert.equal(db.prepare('SELECT COUNT(*) n FROM future_rank_rows').get().n,25);});
test('expiry without takeover still prevents a delayed rank transaction',async t=>{const{db,hooks}=setup(t),institution=await completed(db);target(db);const a=await createRank(institution);hooks.beforeBatch=()=>db.prepare('UPDATE future_rank_runs SET lease_until=0 WHERE id=?').run(a.run.id);await assert.rejects(rank.futureRankStep(a.run.id,'synthetic'),/LEASE_EXPIRED/);assert.equal(db.prepare('SELECT COUNT(*) n FROM future_rank_rows').get().n,0);});
test('byte-short ranking result pages never omit a stored row and partial-cap source remains explicit',async t=>{const{db}=setup(t),institution=await completed(db);for(let n=0;n<26;n++)target(db,n,{project:'가'.repeat(6000),budget_amount:String(n)});const a=await createRank(institution);await finishRank(a.run.id);let after='',keys=[];for(let n=0;n<30;n++){const p=resultParams(a.run.id);if(after)p.set('after',after);const r=await rank.futureRankResults(p);keys.push(...r.rows.map(x=>x.source_key));after=r.next;if(!after)break;}assert.equal(keys.length,26);assert.equal(new Set(keys).size,26);const b=await createRank(institution,{limit:7}),done=await finishRank(b.run.id);assert.equal(done.run.examined,7);assert.equal(done.run.sourceExhausted,false);assert.equal(done.run.selectionTruncated,true);});
test('rank result read rejects a current-to-stale transition during the page',async t=>{const{db,hooks}=setup(t),institution=await completed(db);target(db);const a=await createRank(institution);await finishRank(a.run.id);let once=true;hooks.afterRead=sql=>{if(once&&sql.includes('FROM future_rank_rows')){once=false;db.exec("UPDATE budget_epochs SET revision=revision+1 WHERE mode='synthetic'");}};await assert.rejects(rank.futureRankResults(resultParams(a.run.id)),/CHANGED_RETRY/);});
for(const [label,statement]of[
 ['institution generation',"UPDATE institution_pattern_runs SET generation=generation+1 WHERE id=?"],
 ['institution alias version',"UPDATE institution_pattern_runs SET alias_version='old' WHERE id=?"],
 ['source match count',"UPDATE matching_jobs SET match_count=match_count+1 WHERE id=(SELECT matching_job_id FROM institution_pattern_runs WHERE id=?)"],
 ['source updated timestamp',"UPDATE matching_jobs SET updated_at='changed' WHERE id=(SELECT matching_job_id FROM institution_pattern_runs WHERE id=?)"],
 ['source residual partial',"UPDATE matching_jobs SET partial='{}' WHERE id=(SELECT matching_job_id FROM institution_pattern_runs WHERE id=?)"]])test(label+' changing before rank commit cannot write output',async t=>{const{db,hooks}=setup(t),institution=await completed(db);target(db);const a=await createRank(institution);hooks.beforeBatch=()=>db.prepare(statement).run(institution.id);await assert.rejects(rank.futureRankStep(a.run.id,'synthetic'));assert.equal(db.prepare('SELECT COUNT(*) n FROM future_rank_rows').get().n,0);assert.equal(db.prepare('SELECT examined FROM future_rank_runs').get().examined,0);});

test('large valid rank keys use short durable cursor IDs and missing or foreign IDs fail',async t=>{
 const{db}=setup(t),institution=await completed(db);for(let n=0;n<27;n++)target(db,n);const a=await createRank(institution);await finishRank(a.run.id);
 const{prepareFutureRankRow}=await import('../lib/future-rank-order.mjs');
 for(const record of db.prepare('SELECT * FROM future_rank_rows WHERE run_id=?').all(a.run.id)){
  const row=JSON.parse(record.row_json);row.org_name='o'.repeat(35000);row.project_name='p'.repeat(35000);const prepared=prepareFutureRankRow(row);
  assert.ok(prepared.rank_key.length>131072);db.prepare('UPDATE future_rank_rows SET rank_key=?,row_json=? WHERE run_id=? AND source_key=?').run(prepared.rank_key,prepared.row_json,a.run.id,record.source_key);
 }
 const seen=new Set(),p=resultParams(a.run.id);let firstCursor=null;
 for(let n=0;n<28;n++){const result=await rank.futureRankResults(p);for(const row of result.rows){assert.ok(!seen.has(row.source_key));seen.add(row.source_key);}if(!result.next)break;assert.ok(result.next.length<500);firstCursor??=result.next;p.set('after',result.next);}
 assert.equal(seen.size,27);const cursor=JSON.parse(firstCursor);cursor.id=crypto.randomUUID();p.set('after',JSON.stringify(cursor));await assert.rejects(rank.futureRankResults(p),/INVALID_FUTURE_RANK_CURSOR/);
 const b=await createRank(institution,{limit:1});await finishRank(b.run.id);cursor.id=db.prepare('SELECT cursor_id FROM future_rank_rows WHERE run_id=?').get(b.run.id).cursor_id;p.set('after',JSON.stringify(cursor));await assert.rejects(rank.futureRankResults(p),/INVALID_FUTURE_RANK_CURSOR/);
 const plan=db.prepare('EXPLAIN QUERY PLAN SELECT rank_key FROM future_rank_rows WHERE run_id=? AND cursor_id=?').all(a.run.id,cursor.id).map(r=>r.detail).join(' ');assert.match(plan,/idx_future_rank_cursor/);
});

test('rank evidence and key diagnostics expose separate exact text bytes without source writes',async t=>{
 const{db}=setup(t),institution=await completed(db);target(db);const a=await createRank(institution);await finishRank(a.run.id);const before=sourceState(db);
 const{storageInspectionQuery}=await import('../lib/storage-inspection.mjs');const row=db.prepare('SELECT * FROM future_rank_rows').get();
 for(const[id,field]of[['future_rank_rows','row_json'],['future_rank_keys','rank_key']]){const q=storageInspectionQuery('synthetic',id,'2026-01-01'),r=db.prepare(q.sql).get(...q.args);assert.equal(r.rows,'1');assert.equal(r.selected_payload_bytes,String(Buffer.byteLength(row[field])));assert.equal(r.no_comparable_date_rows,'1');const empty=storageInspectionQuery('live',id,'2026-01-01');assert.equal(db.prepare(empty.sql).get(...empty.args).rows,'0');}
 assert.equal(sourceState(db),before);
});
