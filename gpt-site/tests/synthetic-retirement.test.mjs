import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync,readdirSync} from 'node:fs';
import {RETIREMENT_TABLES,retirementBatch,retireBackedUpBatch,retirementStatus,assertRetirementIdle} from '../lib/synthetic-retirement.mjs';
import {RETIREMENT_COLUMNS} from '../lib/retirement-columns.mjs';
import {liveOnlyRequest} from '../lib/live-only.mjs';

function setup(){
 const raw=new DatabaseSync(':memory:');
 for(const f of readdirSync(new URL('../drizzle/',import.meta.url)).filter(f=>f.endsWith('.sql')).sort())raw.exec(readFileSync(new URL('../drizzle/'+f,import.meta.url),'utf8'));
 const db={async batch(statements){return Promise.all(statements.map(s=>s.all()));},prepare(sql){assert.doesNotMatch(sql,/UNION\s+ALL/i,'hosted compound SELECT limit');return {bind(...args){return statement(sql,args);},...statement(sql,[])}}};
 function statement(sql,args){return {async all(){return {results:raw.prepare(sql).all(...args)};},async first(){return raw.prepare(sql).get(...args)||null;},async run(){return {meta:{changes:raw.prepare(sql).run(...args).changes}};}};}
 return {raw,db};
}
function fixture(raw,table,mode){
 const row={};for(const c of raw.prepare(`PRAGMA table_info(${table})`).all()){
  if(c.dflt_value!==null)continue;
  row[c.name]=c.type==='INTEGER'?0:mode;
  if(!c.notnull&&!c.pk)row[c.name]=null;
 }
 for(const k of ['id','session_id','job_id','run_id','generation_id','root_id','diagnostic_id','prior_probe_id','institution_run_id','matching_job_id','source_key','region_key','org_key','category','budget_key','alias','payload_hash','snapshot_date'])if(k in row)row[k]=mode;
 if('mode' in row)row.mode=mode;
 if('state' in row)row.state=table==='g2b_recovery_runs'?'finished':table==='g2b_recovery_attempts'?'committed':'ready';
 if('status' in row)row.status='complete';
 if('service' in row)row.service='QWGJK';
 if('source_limit' in row)row.source_limit=1;
 if('pass' in row)row.pass=1;
 if('page' in row)row.page=1;
 if('deadline' in row)row.deadline=90000;
 if('row_json' in row)row.row_json=JSON.stringify({label:mode});
 raw.prepare(`INSERT OR REPLACE INTO ${table}(${Object.keys(row).join(',')}) VALUES(${Object.keys(row).map(()=>'?').join(',')})`).run(...Object.values(row));
}
function seed(raw){for(const {table} of [...RETIREMENT_TABLES].reverse())for(const mode of ['live','synthetic'])fixture(raw,table,mode);}
const request=(path,body)=>new Request('https://example.test'+path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});

test('all schema tables are accounted for; complete cleanup preserves live rows and provider ledgers',async()=>{
 const {raw,db}=setup();seed(raw);
 const untouched=['usage','source_usage','server_sync_keys','server_sync_pages'];
 const schema=raw.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").all().map(r=>r.name).sort();
 assert.deepEqual([...RETIREMENT_TABLES.map(s=>s.table),...untouched].sort(),schema);
 for(const table of schema)assert.deepEqual(RETIREMENT_COLUMNS[table],raw.prepare(`PRAGMA table_info(${table})`).all().map(c=>c.name));
 raw.exec("INSERT INTO usage VALUES('2026-11-15',12); INSERT INTO source_usage VALUES('lofin','2026-11-15',3)");
 const before=new Map(RETIREMENT_TABLES.map(s=>[s.table,JSON.stringify(raw.prepare(`SELECT * FROM ${s.table} WHERE NOT (${s.where}) ORDER BY rowid`).all())]));
 const status=await retirementStatus(db);assert.ok(status.total>=40);
 let total=0;
 for(const {table} of RETIREMENT_TABLES){
  while(true){const batch=await retirementBatch(db,table);if(!batch.rows.length)break;
   const payload={version:batch.version,table,ids:batch.rows.map(r=>r._rowid),hash:batch.hash};
   total+=(await retireBackedUpBatch(db,payload)).deleted;
   assert.equal((await retireBackedUpBatch(db,payload)).alreadyAbsent,true);
  }
 }
 assert.equal((await retirementStatus(db)).total,0);assert.equal(total,status.total);
 for(const {table,where}of RETIREMENT_TABLES)assert.equal(JSON.stringify(raw.prepare(`SELECT * FROM ${table} WHERE NOT (${where}) ORDER BY rowid`).all()),before.get(table),table);
 assert.equal(raw.prepare('SELECT calls FROM usage').get().calls,12);assert.equal(raw.prepare('SELECT calls FROM source_usage').get().calls,3);
 assert.deepEqual(raw.prepare('PRAGMA foreign_key_check').all(),[]);
});

test('backup mismatch, live row IDs, SQL injection and overlarge batches cannot delete',async()=>{
 const {raw,db}=setup();fixture(raw,'delivery_rows','live');fixture(raw,'delivery_rows','synthetic');
 const b=await retirementBatch(db,'delivery_rows'),p={version:b.version,table:b.table,ids:b.rows.map(r=>r._rowid),hash:b.hash};
 raw.exec("UPDATE delivery_rows SET agency='changed' WHERE mode='synthetic'");
 await assert.rejects(()=>retireBackedUpBatch(db,p),/BACKUP_MISMATCH/);
 const liveId=raw.prepare("SELECT rowid id FROM delivery_rows WHERE mode='live'").get().id;
 assert.equal((await retireBackedUpBatch(db,{...p,ids:[liveId]})).deleted,0);
 await assert.rejects(()=>retirementBatch(db,'delivery_rows; DROP TABLE usage'),/INVALID_TABLE/);
 await assert.rejects(()=>retireBackedUpBatch(db,{...p,ids:Array.from({length:501},(_,i)=>i+1)}),/INVALID_BATCH/);
 assert.equal(raw.prepare('SELECT COUNT(*) n FROM delivery_rows').get().n,2);
});

test('actual active lease blocks cleanup; stale RUNNING alone does not',async()=>{
 const {raw,db}=setup();fixture(raw,'collection_sessions','synthetic');
 raw.prepare("UPDATE collection_sessions SET state='running',lease_until=?").run(Date.now()+60000);
 await assert.rejects(()=>retirementBatch(db,'collection_sessions'),/ACTIVE_LEASE/);
 raw.exec('UPDATE collection_sessions SET lease_until=0');await assertRetirementIdle(db);
 assert.equal((await retirementBatch(db,'collection_sessions')).rows.length,1);
});

test('batch rows and escaped bytes stay bounded; oversized row is preserved',async()=>{
 const {raw,db}=setup();for(let i=0;i<610;i++){fixture(raw,'query_cache','synthetic');raw.prepare("UPDATE query_cache SET cache_key=? WHERE cache_key='synthetic'").run('key'+i);}
 const b=await retirementBatch(db,'query_cache');assert.ok(b.rows.length<=500);assert.ok(Buffer.byteLength(JSON.stringify(b))<1048576);
 raw.exec("UPDATE query_cache SET payload=replace(hex(zeroblob(300000)),'0',char(10))");
 await assert.rejects(()=>retirementBatch(db,'query_cache'),/ROW_TOO_LARGE/);
 assert.ok(raw.prepare('SELECT COUNT(*) n FROM query_cache').get().n>0);
});

test('production denies synthetic APIs and ID-only replay; real sync and queries remain available',async()=>{
 const {raw,db}=setup();fixture(raw,'collection_sessions','synthetic');const id='12345678-1234-1234-1234-123456789012';raw.prepare('UPDATE collection_sessions SET id=?').run(id);
 for(const [path,body]of [['/api/trial'],['/api/deliveries?mode=synthetic'],['/api/budget',{action:'create',mode:'synthetic'}],['/api/collection',{id,action:'step'}],['/api/collection?id='+id]])assert.equal((await liveOnlyRequest(request(path,body),db)).status,410);
 assert.equal(await liveOnlyRequest(request('/api/deliveries?mode=live'),db),null);
 assert.equal(await liveOnlyRequest(request('/api/server-sync',{rows:[]}),db),null);
 const redirect=await liveOnlyRequest(request('/shopping?mode=synthetic&category=POLE&id='+id),db);assert.equal(redirect.status,307);const u=new URL(redirect.headers.get('location'));assert.equal(u.searchParams.get('mode'),'live');assert.equal(u.searchParams.get('category'),'POLE');assert.equal(u.searchParams.has('id'),false);
});
