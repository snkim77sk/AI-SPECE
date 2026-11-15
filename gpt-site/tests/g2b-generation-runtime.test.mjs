import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync,readdirSync} from 'node:fs';
import {generationService,generationFetch} from '../lib/g2b-generation-runtime.mjs';
import {mixedFixture,normalizeDelivery} from '../lib/g2b-domain.mjs';
const day='2026-11-05',window={from:'2026-01-01',to:'2026-11-09'},stamp='2026-11-07T21:41:20.791Z';
const id=()=>crypto.randomUUID();
function setup(total=1000){
 const sql=new DatabaseSync(':memory:');sql.exec('PRAGMA foreign_keys=ON');for(const file of readdirSync(new URL('../drizzle/',import.meta.url)).filter(f=>f.endsWith('.sql')).sort())sql.exec(readFileSync(new URL('../drizzle/'+file,import.meta.url),'utf8'));
 const ids={generationId:id(),rootId:id(),diagnosticId:id()},priorId=id();
 sql.prepare("INSERT INTO collection_sessions(id,mode,from_day,to_day,state,cursor,day,page,page_size,source_total,fixture_total,fetched,matched,excluded,completed_days,calls,max_payload,lease,lease_until,error,created_at,updated_at) VALUES(?,'live',?,?,'error',0,?,1,999,NULL,0,0,0,0,0,1,0,NULL,0,'STORAGE_OR_SOURCE_FAILED',?,?)").run(ids.rootId,day,day,day,stamp,stamp);
 const evidence={code:'SOURCE_PAGE_OVERLAP',phase:'overlap-check',collectionSnapshotUnchanged:true,sourceRowsWritten:0,collectionResumed:false,automaticRetryAllowed:false};
 sql.prepare("INSERT INTO collection_source_probes(id,session_id,day,page,failed_at,attempt_owner,status,quota_day,created_at,completed_at,result_json) VALUES(?,?,?,1,?,?,'complete',?,?,?,?)").run(priorId,ids.rootId,day,stamp,id(),day,stamp,stamp,JSON.stringify({...evidence,code:'SOURCE_NETWORK_OR_TIMEOUT',phase:'source'}));
 sql.prepare("INSERT INTO collection_source_diagnostics_v2(id,prior_probe_id,session_id,day,page,failed_at,attempt_owner,status,quota_day,created_at,completed_at,result_json) VALUES(?,?,?,?,1,?,?,'complete',?,?,?,?)").run(ids.diagnosticId,priorId,ids.rootId,day,stamp,id(),day,stamp,stamp,JSON.stringify(evidence));
 // Existing normalized user data remains nonempty even with an empty failed-day predecessor.
 for(let n=0;n<570;n++){
  const raw=mixedFixture('2026-11-04',1,999,1).response.body.items[0];raw.dlvrReqNo='PREEXISTING-'+n;
  const r={mode:'live',...normalizeDelivery(raw,'2026-11-04',window,{includeSmart:true}).row,last_session:'historical-session',updated_at:stamp};
  sql.prepare(`INSERT INTO delivery_rows(${Object.keys(r).join(',')}) VALUES(${Object.keys(r).map(()=>'?').join(',')})`).run(...Object.values(r));
 }
 const legacyNames=sql.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'g2b_recovery_%' AND name<>'usage' AND name NOT LIKE 'sqlite_%' ORDER BY name").all().map(x=>x.name);
 const saved=()=>legacyNames.map(name=>[name,JSON.stringify(sql.prepare('SELECT * FROM "'+name+'" ORDER BY rowid').all())]);
 const initial=saved();const h={sql,ids,priorId,total,clock:Date.parse('2026-11-10T04:00:00Z'),calls:0,batches:0,fault:null,mutate:null,beforeBatch:null,beforeRead:null,afterBatch:null,saved,initial,maxStatements:0,maxBinding:0};
 const config={enabled:'approved-staging-only',...ids,liveReady:true,budget:950,...window,serviceKey:'OFFLINE_FAKE_DO_NOT_TRANSMIT'};h.config=config;
 const db={prepare(text){let args=[];const effective=()=>text.replaceAll('unixepoch()*1000+999',String(Math.floor(h.clock/1000)*1000+999));const q={text,bind(...values){args=values;for(const v of values)if(typeof v==='string')h.maxBinding=Math.max(h.maxBinding,new TextEncoder().encode(v).length);return q;},async first(){if(h.beforeRead)await h.beforeRead(text);return sql.prepare(effective()).get(...args)||null;},async all(){if(h.beforeRead)await h.beforeRead(text);return {results:sql.prepare(effective()).all(...args)};},async run(){return q._run();},_run(){const r=sql.prepare(effective()).run(...args);return {meta:{changes:Number(r.changes)}};}};return q;},async batch(qs){h.batches++;h.maxStatements=Math.max(h.maxStatements,qs.length);const kind=qs.some(q=>q.text.startsWith('INSERT INTO g2b_recovery_pages'))?'page':qs.some(q=>q.text.startsWith('INSERT INTO g2b_recovery_attempts'))?'reserve':qs.some(q=>q.text.startsWith('INSERT INTO g2b_recovery_runs'))?'start':'other';if(h.beforeBatch)await h.beforeBatch(kind,qs);sql.exec('BEGIN');try{const out=qs.map((q,n)=>{if(h.fault===kind+'-rollback'&&n===1){h.fault=null;throw Error('SECRET rollback');}return q._run();});sql.exec('COMMIT');if(h.afterBatch)await h.afterBatch(kind,qs);if(h.fault===kind+'-ack'){h.fault=null;throw Error('SECRET lost ACK');}return out;}catch(e){if(sql.isTransaction)sql.exec('ROLLBACK');throw e;}}};
 h.service=generationService({db,configuration:()=>config,now:()=>h.clock,fetcher:async(url,options)=>{h.calls++;assert.equal(url.origin,'https://apis.data.go.kr');assert.equal(options.redirect,'manual');assert.equal(url.searchParams.get('inqryBgnDate'),'20261105');assert.equal(url.searchParams.get('numOfRows'),'999');const p=Number(url.searchParams.get('pageNo'));const raw=mixedFixture(day,p,999,h.total);const response=h.mutate?await h.mutate(raw,p,h.calls,options):null;return response||Response.json(raw);}});
 h.used=()=>Number(sql.prepare('SELECT COALESCE(SUM(calls),0) n FROM usage').get().n);
 h.state=()=>sql.prepare('SELECT * FROM g2b_recovery_generations WHERE id=?').get(ids.generationId);
 h.run=runId=>h.service.run(ids,runId||id());h.create=()=>h.service.create(ids);h.close=()=>sql.close();return h;
}
async function withHarness(total,fn){const h=setup(total);try{await fn(h);}finally{h.close();}}
const flags=result=>{for(const field of ['sourceCompletenessVerified','pointInTimeSnapshotVerified','canPromote','legacyResumed'])assert.equal(result[field],false);};

test('two-pass happy path preserves every legacy table and 570 prior rows',()=>withHarness(1000,async h=>{
 await h.create();const runId=id(),r=await h.run(runId);assert.equal(r.generation.state,'observed_consistent_needs_review');assert.equal(r.run.state,'finished');assert.equal(r.receipts.length,4);assert.equal(h.calls,4);assert.equal(h.used(),4);assert.equal(r.generation.sourceRows,1000);assert.equal(r.generation.targetRows+r.generation.excludedRows,1000);assert.equal(h.sql.prepare('SELECT COUNT(*) n FROM g2b_recovery_source_rows').get().n,1000);assert.equal(h.sql.prepare('SELECT COUNT(*) n FROM delivery_rows').get().n,570);flags(r);flags(r.generation);assert.deepEqual(h.saved(),h.initial);await h.run(runId);assert.equal(h.calls,4);assert.ok(h.maxStatements<=64);assert.ok(h.maxBinding<=1400000);
}));
test('zero-total date requires two source observations without completion claim',()=>withHarness(0,async h=>{await h.create();const r=await h.run();assert.equal(h.calls,2);assert.equal(r.receipts.length,2);assert.equal(r.generation.sourceRows,0);assert.equal(r.generation.state,'observed_consistent_needs_review');flags(r);assert.deepEqual(h.saved(),h.initial);}));
test('run limit eight is durable and at most42 calls cover max20000-row day',()=>withHarness(20000,async h=>{
 await h.create();let r;for(let run=0;run<6;run++){const before=h.calls;r=await h.run();assert.ok(h.calls-before<=8);assert.equal(r.run.attempts,h.calls-before);if(run===0){assert.equal(h.calls,8);assert.equal(r.generation.state,'ready');}}
 assert.equal(h.calls,42);assert.equal(r.generation.state,'observed_consistent_needs_review');assert.equal(r.receipts.length,42);assert.equal(r.generation.sourceRows,20000);assert.equal(h.sql.prepare('SELECT MAX(attempts) n FROM g2b_recovery_runs').get().n,8);assert.deepEqual(h.saved(),h.initial);
}));
test('within/cross page duplicate stops with bounded hashed conflict and no failed page commit',async()=>{
 for(const kind of ['within','cross','changed'])await withHarness(1000,async h=>{await h.create();h.mutate=(raw,p)=>{if(kind==='within'&&p===1)raw.response.body.items[1]={...raw.response.body.items[0]};if(kind!=='within'&&p===2){raw.response.body.items[0]=mixedFixture(day,1,999,1000).response.body.items[0];if(kind==='changed')raw.response.body.items[0].prdctAmt='111';}};const r=await h.run();assert.equal(h.calls,kind==='within'?1:2);assert.equal(r.receipts.length,kind==='within'?0:1);assert.equal(r.generation.state,'uncertain');const conflict=JSON.parse(r.failures[0].conflict_json);assert.equal(conflict.count,1);assert.ok(conflict.samples.length<=8);assert.equal(conflict.samples[0].samePayload,kind!=='changed');assert.ok(!JSON.stringify(conflict).includes('SYNTHETIC'));assert.deepEqual(h.saved(),h.initial);const before=h.calls;await h.run();assert.equal(h.calls,before);});
});
test('second-pass order/content and source total drift block observed success',async()=>{
 for(const kind of ['order','content','total','short','page'])await withHarness(1000,async h=>{await h.create();h.mutate=(raw,p,n)=>{if(kind==='total'&&n===2)raw.response.body.totalCount=1001;if(kind==='short'&&n===1)raw.response.body.items.pop();if(kind==='page'&&n===1)raw.response.body.pageNo=2;if(n===3&&kind==='order')raw.response.body.items.reverse();if(n===3&&kind==='content')raw.response.body.items[0].unknown='changed';};const r=await h.run();assert.equal(r.generation.state,'uncertain');flags(r);assert.notEqual(r.generation.state,'observed_consistent_needs_review');assert.deepEqual(h.saved(),h.initial);});
});
test('identity/canonical/response/count/payload limits fail closed',async()=>{
 for(const kind of ['identity','numeric','capacity','response','payload'])await withHarness(kind==='capacity'?20001:2,async h=>{await h.create();if(kind==='payload')h.sql.prepare('UPDATE g2b_recovery_generations SET payload_bytes=67108864').run();h.mutate=raw=>{if(kind==='identity')raw.response.body.items[0].dlvrReqNo='x'.repeat(1025);if(kind==='numeric')raw.response.body.items[0].unknown=9007199254740992;if(kind==='response')return new Response('x'.repeat(8388609));};const r=await h.run();assert.equal(r.generation.state,'uncertain');assert.equal(r.receipts.length,0);assert.equal(h.calls,1);assert.equal(h.used(),1);assert.deepEqual(h.saved(),h.initial);});
});
test('exact config binding/default disabled fail before calls and creation',async()=>{
 for(const key of ['enabled','generationId','rootId','diagnosticId','liveReady','budget'])await withHarness(2,async h=>{h.config[key]=key==='liveReady'?false:key==='budget'?951:'';await assert.rejects(h.create(),/NOT_ENABLED/);assert.equal(h.calls,0);assert.equal(h.used(),0);assert.equal(h.sql.prepare('SELECT COUNT(*) n FROM g2b_recovery_generations').get().n,0);assert.deepEqual(h.saved(),h.initial);});
});
test('one generation per root and exact immutable diagnostic/root snapshot',async()=>{
 await withHarness(2,async h=>{await h.create();const ids={...h.ids,generationId:id()};h.config.generationId=ids.generationId;await assert.rejects(h.service.create(ids),/ALREADY_EXISTS/);assert.equal(h.calls,0);});
 for(const table of ['collection_sessions','collection_source_probes','collection_source_diagnostics_v2'])await withHarness(2,async h=>{await h.create();h.sql.prepare('UPDATE '+table+' SET '+(table==='collection_sessions'?'updated_at':'result_json')+'=?').run('changed');const r=await h.run();assert.equal(h.calls,0);assert.equal(h.state().state,'uncertain');assert.equal(r.error,'GENERATION_RUN_START_UNCERTAIN');});
});
test('pause/cancel during fetch wins, consumes quota but never commits source page',async()=>{
 for(const state of ['paused','cancelled'])await withHarness(2,async h=>{await h.create();h.mutate=async()=>{await h.service.control(h.ids,state);};const r=await h.run();assert.equal(r.generation.state,state);assert.equal(h.calls,1);assert.equal(h.used(),1);assert.equal(r.receipts.length,0);await h.run();assert.equal(h.calls,1);assert.deepEqual(h.saved(),h.initial);});
});
test('known reservation rollback and lost ACK stop durable run without source retry',async()=>{
 for(const fault of ['start-rollback','start-ack','reserve-rollback','reserve-ack','page-rollback','page-ack'])await withHarness(2,async h=>{await h.create();h.fault=fault;const runId=id();await h.run(runId);const count=h.calls;assert.equal(count,fault.startsWith('page')?1:0);assert.equal(h.used(),fault.startsWith('page')||fault==='reserve-ack'?1:0);const r=await h.service.read(h.ids.generationId,runId);if(fault==='page-ack')assert.equal(r.receipts.length,1);else assert.equal(r.receipts.length,0);await h.run(runId);await h.run();assert.equal(h.calls,count);assert.notEqual(h.state().state,'ready');assert.deepEqual(h.saved(),h.initial);});
});
test('concurrent same/distinct run requests cannot duplicate quota or pages',async()=>{
 for(const same of [true,false])await withHarness(1000,async h=>{await h.create();const a=id(),b=same?a:id();await Promise.allSettled([h.run(a),h.run(b)]);assert.equal(h.calls,4);assert.equal(h.used(),4);assert.equal(h.sql.prepare('SELECT COUNT(*) n FROM g2b_recovery_attempts').get().n,4);assert.equal(h.sql.prepare('SELECT COUNT(*) n FROM g2b_recovery_runs').get().n,1);assert.deepEqual(h.saved(),h.initial);});
});
test('shared950 exhausted or reached during run stops and never refunds permits',async()=>{
 for(const initial of [950,949])await withHarness(1000,async h=>{await h.create();h.sql.prepare('INSERT INTO usage(day,calls) VALUES(?,?)').run('2026-11-10',initial);const r=await h.run();assert.equal(h.calls,initial===950?0:1);assert.equal(h.used(),950);assert.equal(r.generation.state,'uncertain');await h.run();assert.equal(h.used(),950);});
});
test('KST midnight between reservation and dispatch consumes only old day permit and blocks call',()=>withHarness(2,async h=>{
 await h.create();h.clock=Date.parse('2026-11-10T14:59:59.000Z');h.afterBatch=kind=>{if(kind==='reserve')h.clock=Date.parse('2026-11-10T15:00:01.000Z');};const r=await h.run();assert.equal(h.calls,0);assert.equal(h.used(),1);assert.equal(h.sql.prepare('SELECT day FROM usage').get().day,'2026-11-10');assert.equal(r.generation.error,'GENERATION_QUOTA_DAY_CHANGED');
}));
test('90 second deadline never resets within run and expired active run cannot be replaced',()=>withHarness(1000,async h=>{
 await h.create();h.mutate=()=>{h.clock+=90001;};const runId=id(),r=await h.run(runId);assert.equal(h.calls,1);assert.equal(h.sql.prepare('SELECT COUNT(*) n FROM g2b_recovery_pages').get().n,0);assert.notEqual(h.state().state,'ready');await h.run();assert.equal(h.calls,1);flags(r);const run=h.sql.prepare('SELECT * FROM g2b_recovery_runs').get();assert.equal(run.deadline-run.started_at,90000);assert.notEqual(run.state,'finished');
}));
test('less than30s remaining prevents new page reservation at clean boundary',()=>withHarness(1000,async h=>{
 await h.create();h.mutate=()=>{h.clock+=61000;};const r=await h.run();assert.equal(h.calls,1);assert.equal(r.run.attempts,1);assert.equal(r.run.state,'finished');assert.equal(r.generation.state,'ready');assert.equal(r.receipts.length,1);
}));
test('secret-bearing source/storage errors and redirects never leak raw error text or retry',async()=>{
 for(const kind of ['throw','redirect','json'])await withHarness(2,async h=>{await h.create();h.mutate=()=>{if(kind==='throw')throw Error('SECRET serviceKey=OFFLINE_FAKE');if(kind==='redirect')return new Response(null,{status:302,headers:{Location:'https://SECRET.invalid'}});return new Response('SECRET invalid json');};const r=await h.run();assert.equal(h.calls,1);assert.equal(r.receipts.length,0);assert.ok(!JSON.stringify(r).includes('SECRET'));assert.ok(!JSON.stringify(r).includes('OFFLINE_FAKE'));assert.deepEqual(h.saved(),h.initial);});
});

test('lexical loss is rejected before JSON.parse including decimal and exponent underflow',async()=>{
 for(const token of ['9007199254740991.1','9007199254740993','1e-400','-1e-400','1.234567890123456789']){
  await assert.rejects(generationFetch(async()=>new Response('{"number":'+token+'}'),new URL('https://offline.invalid'),1000),/NUMBER_OUT_OF_RANGE/,token);
 }
 const good=await generationFetch(async()=>new Response('{"number":1.25,"zero":0e-400,"text":"9007199254740993"}'),new URL('https://offline.invalid'),1000);assert.equal(good.raw.number,1.25);assert.equal(good.raw.text,'9007199254740993');
});
test('maximum response numeric token is rejected with bounded conversion work',async()=>{
 const huge='9'.repeat(8388608);const started=Date.now();await assert.rejects(generationFetch(async()=>new Response(huge),new URL('https://offline.invalid'),2000),/NUMBER_OUT_OF_RANGE/);assert.ok(Date.now()-started<5000,'Numeric conversion work exceeded local test safety bound');
});
test('arbitrary secret conflict fields are dropped and only typed bounded fields survive',async()=>{
 for(const malformed of [false,true])await withHarness(2,async h=>{await h.create();h.mutate=()=>{const error=Error('SOURCE_PAGE_OVERLAP');error.conflict={count:1,secret:'SECRET_TOP',samples:[{firstPage:1,currentPage:1,identityHash:malformed?'SECRET_HASH':'a'.repeat(64),firstPayloadHash:'b'.repeat(64),currentPayloadHash:'c'.repeat(64),samePayload:false,secret:'SECRET_SAMPLE'}]};throw error;};const r=await h.run();assert.equal(h.calls,1);assert.ok(!JSON.stringify(r).includes('SECRET'));const evidence=r.failures[0].conflict_json;if(malformed)assert.equal(evidence,null);else assert.deepEqual(Object.keys(JSON.parse(evidence).samples[0]).sort(),['firstPage','currentPage','identityHash','firstPayloadHash','currentPayloadHash','samePayload'].sort());});
});
test('KST midnight during final awaited fence read is checked immediately before dispatch',()=>withHarness(2,async h=>{
 await h.create();h.clock=Date.parse('2026-11-10T14:59:59.000Z');h.beforeRead=async text=>{if(text.startsWith('SELECT g.id FROM g2b_recovery_generations')){await Promise.resolve();h.clock=Date.parse('2026-11-10T15:00:01.000Z');}};const r=await h.run();assert.equal(h.calls,0);assert.equal(h.used(),1);assert.equal(r.generation.error,'GENERATION_QUOTA_DAY_CHANGED');
}));
test('scope/config changes during fetch block the page commit',async()=>{
 for(const field of ['from','to','enabled','rootId'])await withHarness(2,async h=>{await h.create();h.mutate=()=>{h.config[field]=field==='from'?'2026-01-02':field==='to'?'2026-11-08':'';};const r=await h.run();assert.equal(h.calls,1);assert.equal(r.receipts.length,0);assert.equal(r.generation.state,'uncertain');assert.deepEqual(h.saved(),h.initial);});
});
test('database-delayed reservation cannot execute after server-side deadline',()=>withHarness(2,async h=>{
 await h.create();h.beforeBatch=kind=>{if(kind==='reserve')h.clock+=90001;};await h.run();assert.equal(h.calls,0);assert.equal(h.used(),0);assert.equal(h.sql.prepare('SELECT COUNT(*) n FROM g2b_recovery_attempts').get().n,0);h.beforeBatch=null;await h.run();assert.equal(h.calls,0);assert.equal(h.sql.prepare('SELECT COUNT(*) n FROM g2b_recovery_runs').get().n,1);
}));
test('unfinished run stays latched after deadline without expired-owner takeover',()=>withHarness(2,async h=>{
 await h.create();h.fault='start-ack';const runId=id();await h.run(runId);h.clock+=900001;const before=h.sql.prepare('SELECT * FROM g2b_recovery_runs WHERE id=?').get(runId);await h.run(runId);await h.run();assert.equal(h.calls,0);assert.equal(h.used(),0);assert.equal(h.sql.prepare('SELECT COUNT(*) n FROM g2b_recovery_runs').get().n,1);assert.equal(h.sql.prepare('SELECT owner FROM g2b_recovery_runs WHERE id=?').get(runId).owner,before.owner);
}));
test('generationFetch cancels stalled body at deadline and never follows redirect',async()=>{
 let cancelled=false,signal;await assert.rejects(generationFetch(async(url,options)=>{signal=options.signal;return new Response(new ReadableStream({cancel(){cancelled=true;}}));},new URL('https://offline.invalid'),5),/SOURCE_FAILED/);assert.equal(signal.aborted,true);assert.equal(cancelled,true);
 let calls=0;await assert.rejects(generationFetch(async(url,options)=>{calls++;assert.equal(options.redirect,'manual');return new Response(null,{status:302,headers:{Location:'https://SECRET.invalid'}});},new URL('https://offline.invalid'),100),/REDIRECT/);assert.equal(calls,1);
});

test('page11 legacy predecessor with570 targets,9990 identities and10 receipts stays byte-for-byte unchanged',()=>withHarness(1000,async h=>{
 const root=h.ids.rootId;
 h.sql.exec('BEGIN');
 h.sql.prepare("UPDATE collection_sessions SET page=11,source_total=12243,fetched=9990,matched=570,excluded=9420,calls=11,max_payload=1000000 WHERE id=?").run(root);
 h.sql.prepare('UPDATE delivery_rows SET last_session=?,source_date=?').run(root,day);
 const insertSeen=h.sql.prepare("INSERT INTO seen_source_keys(session_id,day,source_key,mode,request_no,sequence,change_rank,stored,is_active) VALUES(?,?,?,'live',?,?,'0',?,1)");
 for(const r of h.sql.prepare('SELECT source_key,request_no,sequence FROM delivery_rows').all())insertSeen.run(root,day,r.source_key,r.request_no,r.sequence,1);
 for(let n=0;n<9420;n++)insertSeen.run(root,day,JSON.stringify(['EXCLUDED-LEGACY-'+n,'0','1']),'EXCLUDED-LEGACY-'+n,'1',0);
 const insertReceipt=h.sql.prepare('INSERT INTO collection_receipts(session_id,day,page,fetched,matched,excluded,bytes,hash,committed_at) VALUES(?,?,?,999,?,?,1000000,?,?)');
 for(let pageNo=1;pageNo<=10;pageNo++){const matched=pageNo===1?570:0;insertReceipt.run(root,day,pageNo,matched,999-matched,'legacy-hash-'+pageNo,stamp);}
 h.sql.prepare("INSERT INTO day_checkpoints(session_id,day,state,next_page,total,fetched,matched,updated_at) VALUES(?,?,'incomplete',11,12243,9990,570,?)").run(root,day,stamp);
 for(const table of ['collection_source_probes','collection_source_diagnostics_v2'])h.sql.prepare('UPDATE '+table+' SET page=11 WHERE session_id=?').run(root);
 h.sql.exec('COMMIT');
 const protectedSnapshot=h.saved();
 await h.create();const r=await h.run();assert.equal(r.generation.state,'observed_consistent_needs_review');assert.equal(h.calls,4);assert.deepEqual(h.saved(),protectedSnapshot);
 const original=h.sql.prepare('SELECT * FROM collection_sessions WHERE id=?').get(root);assert.equal(original.page,11);assert.equal(original.error,'STORAGE_OR_SOURCE_FAILED');assert.equal(original.updated_at,stamp);assert.equal(original.fetched,9990);assert.equal(original.matched,570);assert.equal(original.source_total,12243);
 assert.equal(h.sql.prepare('SELECT COUNT(*) n FROM collection_receipts WHERE session_id=?').get(root).n,10);assert.equal(h.sql.prepare('SELECT COUNT(*) n FROM seen_source_keys WHERE session_id=?').get(root).n,9990);assert.equal(h.sql.prepare('SELECT COUNT(*) n FROM delivery_rows WHERE last_session=?').get(root).n,570);assert.equal(h.sql.prepare('SELECT next_page FROM day_checkpoints WHERE session_id=?').get(root).next_page,11);
}));
