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
async function completed(db,options={}){const source=seed(db,options);let w=await service.createInstitutionRun(source.mode,source.id,crypto.randomUUID());for(let n=0;n<100&&w.run.state!=='complete';n++)w=await service.institutionStep(w.run.id,source.mode);assert.equal(w.run.state,'complete');return w.run;}
function target(db,n=0,overrides={}){const row={source_key:'T'+String(n).padStart(4,'0'),payload_hash:'h-'+n,layer:'APPROPRIATION',fiscal_year:2027,snapshot_date:'2026-11-07',region:'인천광역시',org:'인천광역시 옹진군',dept:'',project:'LED 보안등 조명',category:'LIGHTING',account:'일반회계',budget_amount:'9007199254740993123456789',appropriation_amount:'9007199254740993123456789',remaining_amount:null,invalid_amount:0,raw:'{}',...overrides};insert(db,'budget_rows',{mode:'synthetic',service:'AIDFA',source_key:row.source_key,payload_hash:row.payload_hash,fiscal_year:row.fiscal_year,snapshot_date:row.snapshot_date,category:row.category,region:row.region,org:row.org,dept:row.dept,project:row.project,row_json:JSON.stringify(row),is_active:1,last_session:'synthetic-fixture',updated_at:'2026-11-07T00:00:00Z'});return row;}
const params=(id,more={})=>new URLSearchParams({action:'results',mode:'synthetic',id,year:'2027',service:'AIDFA',...more});
function summary(db,run){return JSON.parse(db.prepare('SELECT row_json FROM institution_pattern_years WHERE run_id=?').get(run.id).row_json);}

test('future runtime uses indexed same-region aliases, exact money and observed-only score without writes or network',async t=>{const {db,writes}=setup(t),run=await completed(db);target(db);const before=writes.length,r=await future.futureResults(params(run.id));assert.equal(r.rows.length,1);const a=r.rows[0];assert.equal(a.historical_evidence_score,71);assert.equal(a.historical_population_complete,false);assert.equal(a.historical_high_match_project_rate,null);assert.equal(a.historical_actual_shopping_amount,'900');assert.equal(a.appropriation_amount,'9007199254740993123456789');assert.equal(a.historical_pattern_match_basis,'DIRECT_ORG_ALIAS');assert.equal(a.confirmed_procurement,false);assert.equal(r.providerCalls,0);assert.equal(r.globalRanking,false);assert.equal(writes.length,before);assert.ok(db.prepare('SELECT COUNT(*) n FROM institution_pattern_aliases').get().n>0);});
test('AIDFA score never exceeds75 and score is not a global rank',async t=>{const {db}=setup(t),run=await completed(db,{requests:5,projects:5});target(db);const r=await future.futureResults(params(run.id));assert.ok(r.rows[0].historical_evidence_score<=75);assert.equal(r.order,'SOURCE_SNAPSHOT_DESC_KEY_ASC_NOT_EVIDENCE_RANK');});
test('target2026 excludes all2026 evidence, missing category and region aliases stay absent',async t=>{const {db}=setup(t),run=await completed(db);target(db,0,{fiscal_year:2026});target(db,1,{region:'부산광역시'});target(db,2,{category:'POLE'});const a=await future.futureResults(params(run.id,{year:'2026'}));assert.deepEqual(a.selectedYears,[]);assert.equal(a.rows[0].historical_evidence_score,0);const b=await future.futureResults(params(run.id));assert.equal(b.rows.length,2);assert.ok(b.rows.every(x=>x.historical_evidence_score===0));});
test('two same-region source institutions with overlapping aliases are withheld rather than max-score selected',async t=>{const {db}=setup(t),run=await completed(db,{projects:2,projectOverrides:n=>({org:n?'옹진군':'인천광역시 옹진군'})});target(db);const r=await future.futureResults(params(run.id));assert.equal(r.rows[0].historical_evidence_score,0);assert.deepEqual(r.rows[0].historical_evidence_reasons,['AMBIGUOUS_ORGANIZATION_PATTERN']);});
test('legacy completed run is preserved but future read requires new alias-enabled analysis',async t=>{const {db}=setup(t),run=await completed(db);db.prepare("UPDATE institution_pattern_runs SET alias_version='' WHERE id=?").run(run.id);await assert.rejects(future.futureResults(params(run.id)),/FUTURE_ALIAS_REBUILD_REQUIRED/);assert.equal(db.prepare('SELECT state FROM institution_pattern_runs WHERE id=?').get(run.id).state,'complete');});
test('read rejects a source epoch changed after target lookup',async t=>{const {db,hooks}=setup(t),run=await completed(db);target(db);let once=true;hooks.afterRead=sql=>{if(once&&sql.includes('FROM budget_rows')){once=false;db.exec("UPDATE budget_epochs SET revision=revision+1 WHERE mode='synthetic'");}};await assert.rejects(future.futureResults(params(run.id)),/PATTERN_SOURCE_STALE/);});
test('read rejects changed completed-run generation between queries',async t=>{const {db,hooks}=setup(t),run=await completed(db);target(db);let once=true;hooks.afterRead=sql=>{if(once&&sql.includes('FROM budget_rows')){once=false;db.prepare('UPDATE institution_pattern_runs SET generation=generation+1 WHERE id=?').run(run.id);}};await assert.rejects(future.futureResults(params(run.id)),/FUTURE_SOURCE_CHANGED_RETRY/);});
test('source-order keyset pagination is stable, has no duplicates and binds filters/mode/run',async t=>{const {db}=setup(t),run=await completed(db);for(let n=0;n<29;n++)target(db,n);const a=await future.futureResults(params(run.id)),b=await future.futureResults(params(run.id,{after:a.next}));assert.equal(a.rows.length,25);assert.equal(b.rows.length,4);assert.equal(b.next,null);assert.equal(new Set([...a.rows,...b.rows].map(x=>x.source_key)).size,29);await assert.rejects(future.futureResults(params(run.id,{after:a.next,year:'2028'})),/INVALID_FUTURE_CURSOR/);await assert.rejects(future.futureResults(params(run.id,{mode:'live'})),/PATTERN_RUN_NOT_FOUND/);});
test('byte-short source pages resume after the last emitted row, not the fetched tail',async t=>{const {db}=setup(t),run=await completed(db),big='가'.repeat(8000);for(let n=0;n<26;n++)target(db,n,{project:big,dept:big,account:big,raw:JSON.stringify({fld_nm:big,sect_nm:big})});let after=null,keys=[];for(let n=0;n<30;n++){const r=await future.futureResults(params(run.id,after?{after}:{}));keys.push(...r.rows.map(x=>x.source_key));after=r.next;if(!after)break;}assert.equal(keys.length,26);assert.equal(new Set(keys).size,26);});
test('oversized one-row document fails explicitly, never silently skips a target',async t=>{const {db}=setup(t),run=await completed(db);target(db,0,{project:'가'.repeat(100000)});await assert.rejects(future.futureResults(params(run.id)),/FUTURE_TARGET_TOO_LARGE/);});
test('unknown service, invalid window and category cannot silently broaden query',async t=>{const {db}=setup(t),run=await completed(db);for(const p of [{service:'X'},{year:'2025'},{history:'6'},{category:'OTHER'},{mode:'x'}])await assert.rejects(future.futureResults(params(run.id,p)),/INVALID_FUTURE/);});
test('same-analysis year merge recomputes exact money and weighted lag without claiming verified rates',async t=>{const {db}=setup(t),run=await completed(db),a=summary(db,run),b=structuredClone(a);b.scope.fiscal_years=[2027];b.evidence_years=[2027];b.sufficient_statistics.fiscal_year=2027;b.sufficient_statistics.lag_sum='2';const out=mergeFutureHistory([a,b],{analysisId:run.matching_job_id,targetYear:2028,selectedYears:[2026,2027]});assert.equal(out.actual_shopping_amount,'1800');assert.equal(out.high_matched_budget_projects,2);assert.equal(out.high_match_project_rate,null);assert.equal(out.population_complete,false);assert.equal(out.average_nonnegative_lag_days,(Number(a.sufficient_statistics.lag_sum)+2)/2);});
test('year merge rejects duplicated years, mixed matching jobs, inconsistent identities, fake counts and future years',async t=>{const {db}=setup(t),run=await completed(db),a=summary(db,run),options={analysisId:run.matching_job_id,targetYear:2028,selectedYears:[2026,2027]};assert.throws(()=>mergeFutureHistory([a,a],options),/SCOPE/);for(const mutate of [b=>b.sufficient_statistics.analysis_id='other',b=>b.sufficient_statistics.org_key='other',b=>b.sufficient_statistics.high_matched_budget_projects='99',b=>b.sufficient_statistics.fiscal_year=2028]){const b=structuredClone(a);b.scope.fiscal_years=[2027];b.evidence_years=[2027];b.sufficient_statistics.fiscal_year=2027;mutate(b);assert.throws(()=>mergeFutureHistory([a,b],options));}});
test('missing shopping values propagate unknown totals across years, with known sums separate',async t=>{const {db}=setup(t),run=await completed(db,{requestOverrides:()=>({amount:null})}),a=summary(db,run);const out=mergeFutureHistory([a],{analysisId:run.matching_job_id,targetYear:2027,selectedYears:[2026]});assert.equal(out.actual_shopping_amount,null);assert.equal(out.observed_shopping_amount,'0');});
test('alias inserts and output/cursor are one atomic batch',async t=>{const {db,hooks}=setup(t),source=seed(db);let w=await service.createInstitutionRun(source.mode,source.id,crypto.randomUUID());while(w.run.phase!=='AGGREGATE')w=await service.institutionStep(w.run.id,source.mode);hooks.beforeStatement=s=>{if(s.sql.startsWith('INSERT INTO institution_pattern_aliases'))throw Error('SIMULATED_ALIAS_FAILURE');};await assert.rejects(service.institutionStep(w.run.id,source.mode));assert.equal(db.prepare('SELECT COUNT(*) n FROM institution_pattern_years WHERE run_id=?').get(w.run.id).n,0);assert.equal(db.prepare('SELECT COUNT(*) n FROM institution_pattern_aliases WHERE run_id=?').get(w.run.id).n,0);assert.equal(db.prepare('SELECT completed_groups FROM institution_pattern_runs WHERE id=?').get(w.run.id).completed_groups,0);});
test('aliases have a foreign key to their exact run/year group and use the keyed index',async t=>{const {db}=setup(t),run=await completed(db),query=futureHistoryQuery(run,[target(db)],[2026]);const plan=db.prepare('EXPLAIN QUERY PLAN '+query.sql).all(...query.args).map(x=>x.detail).join('\n');assert.match(plan,/SEARCH a USING COVERING INDEX sqlite_autoindex_institution_pattern_aliases_1/);assert.throws(()=>db.prepare('INSERT INTO institution_pattern_aliases VALUES(?,?,?,?,?,?)').run(run.id,'인천광역시','LIGHTING','bad','missing',2026),/FOREIGN KEY/);});


test('selected-region history years are limited after region filtering rather than displaced by other regions',async t=>{const {db,writes}=setup(t),run=await completed(db,{projects:3,projectOverrides:n=>({fiscal_year:2026+n,...(n?{region:'부산광역시',org:'부산광역시 해운대구'}:{})})});target(db,0,{fiscal_year:2029});const before=writes.length,scoped=await future.futureResults(params(run.id,{year:'2029',region:'인천',history:'2'})),global=await future.futureResults(params(run.id,{year:'2029',history:'2'}));assert.deepEqual(scoped.selectedYears,[2026]);assert.equal(scoped.rows.length,1);assert.equal(scoped.rows[0].historical_actual_shopping_amount,'900');assert.ok(scoped.rows[0].historical_evidence_score>0);assert.equal(scoped.rows[0].historical_year_selection_scope,'SELECTED_REGION_READ_GROUP');assert.deepEqual(global.selectedYears,[2027,2028]);assert.equal(global.rows[0].historical_evidence_score,0);assert.equal(writes.length,before);});
test('history year read group includes predecessor regions without claiming lineage and keeps target cutoff/window',async t=>{const {db}=setup(t),run=await completed(db,{projects:5,requests:0,projectOverrides:n=>({fiscal_year:2026+n,region:n===4?'부산광역시':n%2?'광주광역시':'전라남도',org:n===4?'부산광역시 해운대구':n%2?'광주광역시 북구':'전라남도 목포시'})});const read=(region,year,history)=>{const q=futureYearsQuery(run,{region,year,history});return db.prepare(q.sql).all(...q.args).map(r=>r.fiscal_year);};assert.deepEqual(read('전남광주통합특별시',2031,2),[2029,2028]);assert.deepEqual(read('전남광주통합특별시',2029,5),[2028,2027,2026]);assert.deepEqual(read('알 수 없는 지역',2031,5),[]);assert.deepEqual(read('전남광주통합특별시',2026,5),[]);assert.deepEqual(read('부산',2031,1),[2030]);assert.equal(FUTURE_QUERY_VERSION,'future-read-v3-lineage');});
test('pre-correction future cursors are rejected even with identical source and filters',async t=>{const {db}=setup(t),run=await completed(db);for(let n=0;n<26;n++)target(db,n);const a=await future.futureResults(params(run.id)),cursor=JSON.parse(a.next),scope=JSON.parse(cursor.scope);scope.version='future-read-v1';cursor.scope=JSON.stringify(scope);await assert.rejects(future.futureResults(params(run.id,{after:JSON.stringify(cursor)})),/INVALID_FUTURE_CURSOR/);});

test('lineage runtime combines exact top governments, preserves source names/regions and scores current successor',async t=>{const{db,writes}=setup(t),run=await completed(db,{projects:3,projectOverrides:n=>({org:n===0?'광주광역시':n===1?'전라남도':'광주광역시 북구',region:n===1?'전라남도':'광주광역시'}),requestOverrides:()=>({demand_org:'광주광역시',demand_region:'광주광역시'})});const rawBefore=db.prepare('SELECT row_json FROM matching_budgets ORDER BY source_key').all(),groups=db.prepare('SELECT row_json FROM institution_pattern_years WHERE run_id=?').all(run.id).map(r=>JSON.parse(r.row_json));assert.equal(groups.length,2);const merged=groups.find(g=>g.org_name==='전남광주통합특별시');assert.equal(merged.observed_budget_projects,2);assert.equal(merged.actual_shopping_amount,'900');assert.equal(merged.organization_lineage_applied,true);assert.deepEqual(merged.historical_org_names,['광주광역시','전라남도']);assert.deepEqual(merged.historical_region_names,['광주광역시','전라남도']);target(db,0,{org:'전남광주통합특별시',region:'전남광주통합특별시'});const count=writes.length,result=await future.futureResults(params(run.id,{region:'전남광주통합특별시'}));assert.equal(result.rows.length,1);assert.equal(result.rows[0].historical_actual_shopping_amount,'900');assert.ok(result.rows[0].historical_evidence_score>0);assert.equal(result.rows[0].historical_organization_lineage_applied,true);assert.equal(result.rows[0].historical_pattern_match_basis,'SUPPORTED_CONFIGURED_LINEAGE');assert.equal(result.rows[0].org_name,'전남광주통합특별시');assert.equal(result.rows[0].historical_population_complete,false);const filtered=await service.institutionResults(new URLSearchParams({id:run.id,mode:'synthetic',region:'전라남도'}));assert.equal(filtered.rows.length,1);assert.equal(filtered.rows[0].org_name,'전남광주통합특별시');const years=futureYearsQuery(run,{region:'전라남도',year:2027,history:2});assert.deepEqual(db.prepare(years.sql).all(...years.args).map(r=>r.fiscal_year),[2026]);assert.deepEqual(db.prepare('SELECT row_json FROM matching_budgets ORDER BY source_key').all(),rawBefore);assert.equal(writes.length,count);});
test('split project clues group separately, unknown predecessors do not gain successor evidence and source targets remain unchanged',async t=>{const{db}=setup(t),run=await completed(db,{projects:3,projectOverrides:n=>({org:'인천광역시 중구',project:n===0?'영종동 북도면 보안등 LED 교체사업':n===1?'신포동 LED 보안등':'영종동 신포동 LED 보안등'}),requestOverrides:()=>({demand_org:'인천광역시 중구',delivery_req_name:'영종동 북도면 보안등 교체 관급자재'})});const groups=db.prepare('SELECT row_json FROM institution_pattern_years WHERE run_id=?').all(run.id).map(r=>JSON.parse(r.row_json));assert.equal(groups.length,3);assert.equal(groups.find(g=>g.org_name==='인천광역시 중구').organization_lineage_unresolved,true);target(db,0,{org:'인천광역시 영종구',project:'LED 보안등'});target(db,1,{org:'인천광역시 중구',project:'영종동 LED 보안등',snapshot_date:'2026-03-01'});target(db,2,{org:'인천광역시 중구',project:'영종동 신포동 LED 보안등',snapshot_date:'2026-03-01'});target(db,3,{org:'인천광역시 중구',project:'영종동 LED 보안등',snapshot_date:'2026-11-07'});const r=await future.futureResults(params(run.id));const find=key=>r.rows.find(x=>x.source_key===key);assert.equal(find('T0000').historical_actual_shopping_amount,'900');assert.equal(find('T0001').org_name,'인천광역시 중구');assert.equal(find('T0001').historical_target_lineage_org,'인천광역시 영종구');assert.ok(find('T0001').historical_evidence_score>0);for(const key of ['T0002','T0003']){assert.equal(find(key).historical_evidence_score,0);assert.deepEqual(find(key).historical_evidence_reasons,['TARGET_ORGANIZATION_LINEAGE_UNRESOLVED']);}});
test('lineage summaries cannot be replayed with fabricated provenance or missing source counts',async t=>{const{db}=setup(t),run=await completed(db),a=summary(db,run),opts={analysisId:run.matching_job_id,targetYear:2027,selectedYears:[2026]};for(const mutate of [p=>p.organization_lineage_version='made-up',p=>p.sufficient_statistics.lineage_version='old',p=>p.organization_lineage_basis={FAKE:2},p=>p.organization_lineage_basis={SOURCE_ORG:999}]){const b=structuredClone(a);mutate(b);assert.throws(()=>mergeFutureHistory([b],opts),/PROVENANCE|LINEAGE/);}});
