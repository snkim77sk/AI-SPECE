import test from'node:test';import assert from'node:assert/strict';import{DatabaseSync}from'node:sqlite';import{readFileSync,readdirSync,existsSync}from'node:fs';import{fileURLToPath}from'node:url';import{registerHooks}from'node:module';
const env={DB:null};globalThis.__g2bDashboardEnv=env;registerHooks({resolve(spec,ctx,next){if(spec==='cloudflare:workers')return{url:'data:text/javascript,export const env=globalThis.__g2bDashboardEnv',shortCircuit:true};if(spec.startsWith('.')&&!/\.(ts|mjs|js)$/.test(spec)&&ctx.parentURL?.startsWith('file:')){const u=new URL(spec+'.ts',ctx.parentURL);if(existsSync(fileURLToPath(u)))return next(u.href,ctx);}return next(spec,ctx);}});
const{dashboard}=await import('../lib/dashboard-service.ts'),budget=await import('../lib/budget-service.ts'),{config}=await import('../lib/g2b-service.ts'),{normalizeDelivery}=await import('../lib/g2b-domain.mjs');
function setup(){const db=new DatabaseSync(':memory:');db.exec('PRAGMA foreign_keys=ON');for(const f of readdirSync(new URL('../drizzle/',import.meta.url)).filter(x=>x.endsWith('.sql')).sort())db.exec(readFileSync(new URL('../drizzle/'+f,import.meta.url),'utf8'));env.DB={prepare(sql){let args=[];const q={bind(...v){args=v;return q;},async first(){return db.prepare(sql).get(...args)||null;},async all(){return{results:db.prepare(sql).all(...args)};},async run(){return q._run();},_run(){return{meta:{changes:Number(db.prepare(sql).run(...args).changes)}};}};return q;},async batch(q){db.exec('BEGIN');try{const out=q.map(x=>x._run());db.exec('COMMIT');return out;}catch(e){db.exec('ROLLBACK');throw e;}}};return db;}
function add(db,raw={}){const c=config(),r=normalizeDelivery({dlvrReqNo:'A',prdctSno:'1',dlvrReqChgOrd:'0',dtilPrdctClsfcNo:'3911160301',dlvrReqRcptDate:c.to,prdctAmt:'100',corpNm:'합성 업체',dminsttNm:'합성 기관',dminsttCd:'SYN',dminsttRgnNm:'서울특별시',fnlDlvrReqYn:'Y',...raw},c.to,c,{includeSmart:true}).row;Object.assign(r,{mode:'synthetic',last_session:'fixture',updated_at:'now'});db.prepare(`INSERT INTO delivery_rows(${Object.keys(r).join(',')}) VALUES(${Object.keys(r).map(()=>'?').join(',')})`).run(...Object.values(r));db.exec("UPDATE dataset_epochs SET revision=revision+1 WHERE mode='synthetic'");}
test('empty dashboard has exact zero counts and no source or memory claims',async()=>{const db=setup(),old=globalThis.fetch;globalThis.fetch=()=>{throw Error('PROVIDER_FORBIDDEN');};try{const r=await dashboard('live');assert.equal(r.delivery.stored_source_rows,'0');assert.equal(r.delivery.latest_rows,'0');assert.equal(r.observations.observed_states,'0');assert.equal(r.sourceRevisions.observations,'0');assert.equal(r.readiness.scheduleInstalled,false);assert.equal(r.providerCalls,0);assert.equal(r.memory.peakMeasured,false);assert.equal(r.storage.bytesMeasured,false);assert.equal(r.sourceCompletenessVerified,false);}finally{globalThis.fetch=old;db.close();}});
test('overview separates stored/active/latest/inactive/control and budgets/revisions/observations',async()=>{const db=setup(),c=config();add(db);add(db,{dlvrReqChgOrd:'1'});add(db,{dlvrReqNo:'B'});db.exec("UPDATE delivery_rows SET is_active=0 WHERE request_no='B'");add(db,{dlvrReqNo:'CONTROL',dtilPrdctClsfcNo:'3911980401'});let b=await budget.createBudget('synthetic','QWGJK',2026,c.to,false,4);await budget.budgetStep(b.session.id);b=await budget.createBudget('synthetic','AIDFA',2026,budget.budgetConfig().today,false,4);await budget.budgetStep(b.session.id);const r=await dashboard('synthetic');assert.equal(r.delivery.stored_source_rows,'4');assert.equal(r.delivery.active_source_rows,'3');assert.equal(r.delivery.inactive_source_rows,'1');assert.equal(r.delivery.latest_rows,'1');assert.equal(r.delivery.smart_control_rows,'1');assert.equal(r.delivery.requests,'1');assert.equal(r.budgets.find(x=>x.service==='QWGJK').target_rows,'2');assert.equal(r.budgets.find(x=>x.service==='AIDFA').target_rows,'0');assert.equal(r.revisions.find(x=>x.service==='QWGJK').unique_states,'4');assert.equal(r.observations.observed_states,'4');assert.equal(r.observations.observed_dates,'1');const warm=await dashboard('synthetic');assert.equal(warm.aggregateCache.hit,true);for(const key of ['delivery','budgets','revisions','observations'])assert.deepEqual(warm[key],JSON.parse(JSON.stringify(r[key])));const live=await dashboard('live');assert.equal(live.delivery.stored_source_rows,'0');assert.equal(live.budgets.length,0);db.close();});
test('dashboard rejects mixed source revisions and invalid mode',async()=>{const db=setup(),prepare=env.DB.prepare;let reads=0;env.DB.prepare=sql=>{const q=prepare(sql);if(sql.startsWith('SELECT CAST(d.revision')){const first=q.first;q.first=async()=>{const r=await first();if(++reads>1)r.observations='1';return r;};}return q;};await assert.rejects(dashboard('synthetic'),/SOURCE_CHANGED_RETRY/);await assert.rejects(dashboard('any'),/INVALID_MODE/);db.close();});

test('missing or whitespace-only agency identities are not counted as real agencies',async()=>{const db=setup();add(db,{dlvrReqNo:'MISSING',dminsttNm:'',dminsttCd:''});add(db,{dlvrReqNo:'SPACE',dminsttNm:'  ',dminsttCd:'  '});const r=await dashboard('synthetic');assert.equal(r.delivery.latest_rows,'2');assert.equal(r.delivery.agency_identities,'0');add(db,{dlvrReqNo:'KNOWN'});assert.equal((await dashboard('synthetic')).delivery.agency_identities,'1');db.close();});

// Count real SQLite statements and inject source changes at awaited boundaries.
function instrument(after=()=>{}){
 const prepare=env.DB.prepare,reads=[];
 env.DB.prepare=sql=>{const q=prepare(sql);for(const method of ['first','all']){const run=q[method];q[method]=async()=>{reads.push(sql);const result=await run();await after(sql,result);return result;};}return q;};
 return{reads,aggregates:()=>reads.filter(sql=>/FROM (delivery_rows|budget_rows|budget_revisions|budget_observations)\b/.test(sql)).length};
}
const epochTables={delivery:'dataset_epochs',budget:'budget_epochs',history:'budget_history_epochs',observations:'budget_observation_epochs'};
function bump(db,source,mode='synthetic'){db.prepare(`INSERT INTO ${epochTables[source]}(mode,revision) VALUES(?,1) ON CONFLICT(mode) DO UPDATE SET revision=revision+1`).run(mode);}

test('warm dashboard performs zero aggregate queries and keeps readiness outside cache',async()=>{
 const db=setup();add(db);const trace=instrument();
 try{
  const first=await dashboard('synthetic');assert.deepEqual(first.aggregateCache,{hit:false,ttlSeconds:60,sourceBound:true});assert.equal(trace.aggregates(),6);const before=trace.reads.length;
  env.G2B_DAILY_BUDGET='17';env.LOFIN_DAILY_BUDGET='19';
  const warm=await dashboard('synthetic');assert.equal(trace.aggregates(),6);assert.equal(trace.reads.length-before,3);
  assert.deepEqual(warm.aggregateCache,{hit:true,ttlSeconds:60,sourceBound:true});assert.deepEqual(warm.delivery,first.delivery);assert.equal(warm.readiness.g2b.localDailyCap,17);
  const stored=JSON.parse(db.prepare('SELECT payload FROM query_cache').get().payload);
  assert.deepEqual(Object.keys(stored).sort(),['identity','delivery','budgets','revisions','observations'].sort());
  assert.equal(JSON.stringify(stored).includes('hasKey'),false);
 }finally{delete env.G2B_DAILY_BUDGET;delete env.LOFIN_DAILY_BUDGET;db.close();}
});
for(const source of Object.keys(epochTables)){
 test(`${source}-only epoch change invalidates cached aggregates`,async()=>{
  const db=setup(),trace=instrument();try{await dashboard('synthetic');bump(db,source);const fresh=await dashboard('synthetic');assert.equal(fresh.sourceRevisions[source],'1');assert.equal(trace.aggregates(),12);await dashboard('synthetic');assert.equal(trace.aggregates(),12);}finally{db.close();}
 });
 test(`${source} changing after a cache hit rejects the response`,async()=>{
  const db=setup();try{await dashboard('synthetic');const trace=instrument(sql=>{if(sql.startsWith('SELECT payload'))bump(db,source);});await assert.rejects(dashboard('synthetic'),/DASHBOARD_SOURCE_CHANGED_RETRY/);assert.equal(trace.aggregates(),0);}finally{db.close();}
 });
 test(`${source} changing between aggregate queries rejects without caching`,async()=>{
  const db=setup();try{let changed=false;instrument(sql=>{if(!changed&&sql.includes('stored_source_rows')){changed=true;bump(db,source);}});await assert.rejects(dashboard('synthetic'),/DASHBOARD_SOURCE_CHANGED_RETRY/);assert.equal(db.prepare('SELECT COUNT(*) n FROM query_cache').get().n,0);}finally{db.close();}
 });
 test(`${source} changing during cache storage rejects and cannot poison next request`,async()=>{
  const db=setup(),batch=env.DB.batch;try{env.DB.batch=async q=>{const result=await batch(q);bump(db,source);return result;};await assert.rejects(dashboard('synthetic'),/DASHBOARD_SOURCE_CHANGED_RETRY/);env.DB.batch=batch;const trace=instrument(),fresh=await dashboard('synthetic');assert.equal(fresh.sourceRevisions[source],'1');assert.equal(trace.aggregates(),6);}finally{db.close();}
 });
}

test('mode and resolved date window have separate cache identities',async()=>{
 const db=setup(),RealDate=globalThis.Date;try{
  add(db);const trace=instrument();const first=await dashboard('synthetic');const live=await dashboard('live');assert.equal(live.delivery.stored_source_rows,'0');assert.equal(trace.aggregates(),12);
  // Advance the query day without expiring the cache clock, proving key isolation.
  const tomorrow=RealDate.now()+86400000;globalThis.Date=class extends RealDate{constructor(...args){super(...(args.length?args:[tomorrow]));}static now(){return RealDate.now();}};
  const next=await dashboard('synthetic');assert.notEqual(next.window.to,first.window.to);assert.equal(trace.aggregates(),18);
 }finally{globalThis.Date=RealDate;db.close();}
});

for(const failure of ['read','write','malformed','wrong-identity','expired','oversized'])test(`optional cache ${failure} falls back safely`,async()=>{
 const db=setup();try{
  await dashboard('synthetic');const prepare=env.DB.prepare;
  if(failure==='read')env.DB.prepare=sql=>{if(sql.startsWith('SELECT payload'))throw Error('CACHE_READ_UNAVAILABLE');return prepare(sql);};
  if(failure==='write'){db.exec('DELETE FROM query_cache');env.DB.batch=async()=>{throw Error('CACHE_WRITE_UNAVAILABLE');};}
  if(failure==='malformed')db.exec("UPDATE query_cache SET payload='{'");
  if(failure==='wrong-identity'){const payload=JSON.parse(db.prepare('SELECT payload FROM query_cache').get().payload);payload.identity='other';db.prepare('UPDATE query_cache SET payload=?').run(JSON.stringify(payload));}
  if(failure==='expired')db.exec('UPDATE query_cache SET expires_at=0');
  if(failure==='oversized')db.prepare('UPDATE query_cache SET payload=?').run(' '.repeat(524289));
  const trace=instrument(),r=await dashboard('synthetic');assert.equal(r.delivery.stored_source_rows,'0');assert.equal(trace.aggregates(),6);
 }finally{db.close();}
});

test('cache remains at most 64 entries per mode and expires after 60 seconds',async()=>{
 const db=setup();try{const insert=db.prepare('INSERT INTO query_cache VALUES(?,?,?,?,?,?)');for(let i=0;i<70;i++)insert.run('old-'+i,'synthetic',0,'{}',i,0);await dashboard('synthetic');assert.equal(db.prepare("SELECT COUNT(*) n FROM query_cache WHERE mode='synthetic'").get().n,64);const entry=db.prepare("SELECT * FROM query_cache WHERE cache_key LIKE '%:dashboard:%'").get();assert.equal(entry.expires_at-entry.created_at,60000);assert.ok(Buffer.byteLength(entry.payload)<=524288);}finally{db.close();}
});

test('oversized computed aggregates are returned safely but never cached',async()=>{
 const db=setup();try{instrument((sql,result)=>{if(sql.includes('stored_source_rows'))result.updated_at='x'.repeat(524289);});const r=await dashboard('synthetic');assert.equal(r.delivery.updated_at.length,524289);assert.equal(db.prepare('SELECT COUNT(*) n FROM query_cache').get().n,0);}finally{db.close();}
});

test('required source epoch read failure is never swallowed as an optional cache error',async()=>{
 const db=setup();try{await dashboard('synthetic');let epochs=0;instrument(sql=>{if(sql.startsWith('SELECT CAST(d.revision')&&++epochs===2)throw Error('REQUIRED_EPOCH_READ_FAILED');});await assert.rejects(dashboard('synthetic'),/REQUIRED_EPOCH_READ_FAILED/);}finally{db.close();}
});

for(const [name,damage] of [
 ['missing delivery counts',v=>{v.delivery={};}],
 ['string delivery',v=>{v.delivery='wrong';}],
 ['missing observation counts',v=>{v.observations={};}],
 ['numeric count',v=>{v.delivery.latest_rows=0;}],
 ['invalid count string',v=>{v.observations.observed_dates='NaN';}],
 ['unbounded services',v=>{v.revisions=Array(3).fill({service:'QWGJK',unique_states:'1',first_stored_snapshot:null,last_stored_snapshot:null});}],
 ['missing budget fields',v=>{v.budgets=[{service:'QWGJK',fiscal_year:2026}];}],
 ['extra delivery fields',v=>{v.delivery.hasKey=true;}],
])test(`valid JSON with ${name} is not accepted as an aggregate hit`,async()=>{
 const db=setup();try{await dashboard('synthetic');const value=JSON.parse(db.prepare('SELECT payload FROM query_cache').get().payload);damage(value);db.prepare('UPDATE query_cache SET payload=?').run(JSON.stringify(value));const trace=instrument(),r=await dashboard('synthetic');assert.equal(trace.aggregates(),6);assert.equal(r.delivery.latest_rows,'0');assert.equal(r.observations.observed_dates,'0');}finally{db.close();}
});

for(const source of Object.keys(epochTables))test(`${source} change immediately before cache insert is fenced in SQL`,async()=>{
 const db=setup(),batch=env.DB.batch;try{env.DB.batch=async q=>{bump(db,source);return batch(q);};await assert.rejects(dashboard('synthetic'),/DASHBOARD_SOURCE_CHANGED_RETRY/);assert.equal(db.prepare('SELECT COUNT(*) n FROM query_cache').get().n,0);}finally{db.close();}
});

test('progressive cold dashboard performs one aggregate per request then matches complete response',async()=>{
 const db=setup();try{add(db);const trace=instrument();for(let i=0;i<6;i++){const before=trace.aggregates();await assert.rejects(dashboard('synthetic',true),/DASHBOARD_AGGREGATE_PENDING/);assert.equal(trace.aggregates()-before,1);}const before=trace.aggregates(),r=await dashboard('synthetic',true);assert.equal(trace.aggregates(),before);assert.equal(r.delivery.latest_rows,'1');assert.deepEqual(r.delivery,(await dashboard('synthetic')).delivery);assert.equal((await dashboard('synthetic',true)).aggregateCache.hit,true);}finally{db.close();}
});
test('progressive stages cannot combine source epochs and reject corrupted counts',async()=>{
 const db=setup();try{add(db);await assert.rejects(dashboard('synthetic',true),/PENDING/);bump(db,'budget');await assert.rejects(dashboard('synthetic',true),/PENDING/);db.exec("UPDATE query_cache SET payload='{}'");const trace=instrument();await assert.rejects(dashboard('synthetic',true),/PENDING/);assert.equal(trace.aggregates(),1);}finally{db.close();}
});
