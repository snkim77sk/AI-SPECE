import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync,readdirSync,existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {registerHooks} from 'node:module';
import {normalizeDelivery,scopeWindow} from '../lib/g2b-domain.mjs';
import {queryPlan,summaryQuery,summaryPreflightQuery,deliverySummaryMoneyState,DELIVERY_SUMMARY_MAX_DIGITS,DELIVERY_SUMMARY_MAX_ROWS,DELIVERY_SUMMARY_LIMB_BASE} from '../lib/g2b-query.mjs';

const env={DB:null};
globalThis.__deliverySummaryEnv=new Proxy(env,{get(target,key){if(key==='G2B_PUBLIC_AUTH_ENABLED')return undefined;if(key!=='DB')throw Error('SUMMARY_MUST_NOT_READ_SECRETS_OR_CONFIG');return target.DB;}});
registerHooks({resolve(spec,ctx,next){
 if(spec==='cloudflare:workers')return {url:'data:text/javascript,export const env=globalThis.__deliverySummaryEnv',shortCircuit:true};
 if(spec.startsWith('.')&&!/\.(ts|mjs|js)$/.test(spec)&&ctx.parentURL?.startsWith('file:')){const url=new URL(spec+'.ts',ctx.parentURL);if(existsSync(fileURLToPath(url)))return next(url.href,ctx);}
 return next(spec,ctx);
}});
const {analysisRows}=await import('../lib/g2b-service.ts');
const params=(extra={})=>new URLSearchParams({mode:'synthetic',...extra});
function setup(t,hook=()=>{}){
 const db=new DatabaseSync(':memory:');
 for(const f of readdirSync(new URL('../drizzle/',import.meta.url)).filter(f=>f.endsWith('.sql')).sort())db.exec(readFileSync(new URL('../drizzle/'+f,import.meta.url),'utf8'));
 const reads=[];
 env.DB={prepare(sql){let args=[];const statement={bind(...values){args=values;return statement;},async first(){const query=db.prepare(sql);query.setReadBigInts(true);const raw=query.get(...args),r=raw?Object.fromEntries(Object.entries(raw).map(([k,v])=>[k,typeof v==='bigint'?Number(v):v])):null;reads.push({sql,args,rows:r?1:0});hook(sql,db,r);return r;},async all(){const rows=db.prepare(sql).all(...args);assert.ok(rows.length<=200,'only the top 200 aggregate rows may cross D1');reads.push({sql,args,rows:rows.length});hook(sql,db,rows);return {results:rows};},_run(){assert.match(sql,/^(INSERT INTO query_cache|DELETE FROM query_cache)/,'summary writes are optional cache writes only');const r=db.prepare(sql).run(...args);return {meta:{changes:Number(r.changes)}};},async run(){return statement._run();}};return statement;},async batch(statements){db.exec('BEGIN');try{const out=statements.map(s=>s._run());db.exec('COMMIT');return out;}catch(e){db.exec('ROLLBACK');throw e;}}};
 const fetch=globalThis.fetch;globalThis.fetch=()=>{throw Error('SUMMARY_MUST_NOT_CALL_PROVIDER');};
 t.after(()=>{db.close();env.DB=null;globalThis.fetch=fetch;});return {db,reads};
}
function add(db,key,amount='100',patch={},storage={}){
 const w=scopeWindow(),raw={dlvrReqNo:key,prdctSno:'1',dlvrReqChgOrd:'0',dlvrReqRcptDate:w.to,dtilPrdctClsfcNo:'3911160301',prdctAmt:amount,corpNm:'공급업체',corpBizno:'123',dminsttNm:'서울 기관',dminsttCd:'S',dminsttRgnNm:'서울특별시',...patch};
 const {row}=normalizeDelivery(raw,w.to,w,{includeSmart:true}),r={mode:'synthetic',...row,last_session:'fixture',updated_at:'now',...storage};
 db.prepare(`INSERT INTO delivery_rows(${Object.keys(r).join(',')}) VALUES(${Object.keys(r).map(()=>'?').join(',')})`).run(...Object.values(r));return r;
}
const plan=(extra={})=>queryPlan(params(extra),scopeWindow());
function direct(db,group='vendor',q=''){
 const p=plan(),pre=summaryPreflightQuery(p,group,q),stats=db.prepare(pre.sql).get(...pre.args),query=summaryQuery(p,group,q,{preflight:stats});
 return db.prepare(query.sql).all(...query.args);
}

test('both summary groups sum beyond SQLite int64 and rank wide totals exactly',async t=>{
 const {db}=setup(t);
 for(const [identity,values]of [['A',['9223372036854775807','9223372036854775807']],['B',['18446744073709551613']],['C',['9007199254740993']],['D',['9007199254740992']]])for(const [i,value]of values.entries())add(db,identity+i,value,{corpNm:identity,corpBizno:String(identity.charCodeAt(0)),dminsttNm:identity,dminsttCd:identity});
 for(const group of ['agency','vendor']){
  const result=await analysisRows(params({group}));
  assert.deepEqual(result.rows.map(r=>r.amount),['18446744073709551614','18446744073709551613','9007199254740993','9007199254740992']);
  assert.equal(result.monetaryStatus,'complete');assert.equal(result.moneyComplete,true);assert.equal(result.providerCalls,0);assert.equal(result.sourceRevision,'0');
 }
});

test('signed carries, cross-limb borrow, cancellation, zero and all-missing ranking stay exact',async t=>{
 const {db}=setup(t),cases={positive:['1'],zero:['1000000000000000000000000000','-999999999999999999999999999','-1'],minusOne:['-1000000000000000000000000000','999999999999999999999999999'],minusBillion:['-1000000000'],minusBillionOne:['-1000000001'],missing:[null]};
 for(const [name,values]of Object.entries(cases))for(const [i,value]of values.entries())add(db,name+i,value,{corpNm:name,corpBizno:'',dminsttNm:name,dminsttCd:name});
 for(const group of ['vendor','agency']){
  const r=await analysisRows(params({group}));
  assert.deepEqual(r.rows.map(x=>x.amount),['1','0','-1','-1000000000','-1000000001',null]);assert.equal(r.monetaryStatus,'partial');assert.equal(r.missingAmounts,1);
  assert.equal(r.rows.at(-1).money_complete,0);assert.equal(r.rows[1].money_complete,1);
 }
});

test('partial sums retain missing/request/counterparty counts and never use request totals',async t=>{
 const {db}=setup(t);add(db,'A','100',{dlvrReqAmt:'999999999999999999999'});add(db,'A',null,{prdctSno:'2',dlvrReqAmt:'999999999999999999999'});add(db,'B','-1',{dminsttNm:'다른 기관',dminsttCd:'D'});
 const r=await analysisRows(params());assert.equal(r.rows[0].amount,'99');assert.equal(r.rows[0].rows,3);assert.equal(r.rows[0].requests,2);assert.equal(r.rows[0].counterparties,2);assert.equal(r.rows[0].missing_amounts,1);assert.equal(r.rows[0].money_complete,0);assert.equal(r.moneyComplete,false);assert.equal(r.moneyUnavailable,null);
});

test('all widths through 72 digits agree with BigInt totals and database-side ranking',async t=>{
 const {db}=setup(t),expected=[];
 for(let group=0;group<90;group++){
  let sum=0n;const name=String(group).padStart(3,'0');
  for(let i=0;i<8;i++){
   const digits=(group*7+i*13)%72+1,magnitude='987654321'.repeat(8).slice(0,digits),value=(i%3===0?'-':'')+magnitude;
   sum+=BigInt(value);add(db,name+'-'+i,value,{corpNm:name,corpBizno:name,dminsttNm:name,dminsttCd:name});
  }
  expected.push({name,amount:sum.toString()});
 }
 expected.sort((a,b)=>BigInt(a.amount)>BigInt(b.amount)?-1:BigInt(a.amount)<BigInt(b.amount)?1:a.name.localeCompare(b.name));
 for(const group of ['agency','vendor'])assert.deepEqual((await analysisRows(params({group}))).rows.map(({name,amount})=>({name,amount})),expected);
});

test('max-width amounts can create larger exact sums, including negative totals',t=>{
 const {db}=setup(t),wide='9'.repeat(72);
 for(let n=0;n<125;n++){add(db,'P'+n,wide,{corpNm:'positive',corpBizno:'1'});add(db,'N'+n,'-'+wide,{corpNm:'negative',corpBizno:'2'});}
 const r=direct(db);assert.deepEqual(r.map(x=>x.amount),[(BigInt(wide)*125n).toString(),(-BigInt(wide)*125n).toString()]);assert.ok(r[0].amount.length>72);
});

test('vendor alias resolution/search keeps complete groups and excludes unrelated invalid amounts',async t=>{
 const {db}=setup(t);add(db,'old','9007199254740993',{corpNm:'AAA 100%_old'});add(db,'new','2',{corpNm:'ZZZ new'});add(db,'numberless','3',{corpNm:'AAA 100%_old',corpBizno:''});add(db,'bad','9'.repeat(73),{corpNm:'unrelated',corpBizno:'9'});
 for(const q of ['AAA 100%_old','ZZZ new','123']){
  const r=await analysisRows(params({q}));assert.equal(r.moneyUnavailable,null);assert.equal(r.rows.length,1);assert.equal(r.rows[0].amount,'9007199254740998');assert.equal(r.rows[0].rows,3);
 }
 assert.equal((await analysisRows(params({q:'AAA 100X_old'}))).rows.length,0);
 assert.equal((await analysisRows(params({q:"' OR 1=1 --"}))).rows.length,0);
 add(db,'ambiguous','1',{corpNm:'AAA 100%_old',corpBizno:'456'});db.exec("UPDATE dataset_epochs SET revision=revision+1 WHERE mode='synthetic'");
 const r=await analysisRows(params({q:'123'}));assert.equal(r.rows[0].amount,'9007199254740995');assert.equal(r.rows[0].rows,2);
});

test('current/date/region/category/smart filters and agency identity remain intact',async t=>{
 const {db}=setup(t),w=scopeWindow(),previous=new Date(Date.parse(w.to+'T00:00:00Z')-86400000).toISOString().slice(0,10);
 add(db,'version','100',{dlvrReqRcptDate:previous});add(db,'version','200',{dlvrReqChgOrd:'1'});add(db,'inactive','999',{}, {is_active:0});add(db,'pole','300',{dtilPrdctClsfcNo:'3911152601',dminsttNm:'기관 새 이름'});add(db,'other','400',{dminsttRgnNm:'부산광역시',dminsttNm:'부산 기관',dminsttCd:'B'});add(db,'control','500',{dtilPrdctClsfcNo:'3911980401',fnlDlvrReqYn:'Y'});add(db,'no','600',{dtilPrdctClsfcNo:'3911980401',fnlDlvrReqYn:'N'});
 assert.equal((await analysisRows(params({group:'agency',region:'서울특별시'}))).rows[0].amount,'500');
 assert.equal((await analysisRows(params({category:'POLE'}))).rows[0].amount,'300');
 assert.equal((await analysisRows(params({to:previous}))).rows.length,0);
 assert.equal((await analysisRows(params({dataset:'smart',kind:'CONTROL'}))).rows[0].amount,'500');
 await assert.rejects(analysisRows(params({view:'history'})),/CURRENT_ROWS_REQUIRED/);
});

test('uncoded agencies with the same name remain separate by region, while explicit codes retain identity',async t=>{
 const {db}=setup(t);
 add(db,'seoul','100',{dminsttNm:'도로관리과',dminsttCd:'',dminsttRgnNm:'서울특별시'});
 add(db,'busan','200',{dminsttNm:'도로관리과',dminsttCd:'',dminsttRgnNm:'부산광역시'});
 add(db,'unknown','300',{dminsttNm:'도로관리과',dminsttCd:'',dminsttRgnNm:''});
 add(db,'coded-old','400',{dminsttNm:'코드기관 옛이름',dminsttCd:'SAME',dminsttRgnNm:'서울특별시'});
 add(db,'coded-new','500',{dminsttNm:'코드기관 새이름',dminsttCd:'SAME',dminsttRgnNm:'부산광역시'});
 add(db,'code-collision','600',{dminsttNm:'SAME',dminsttCd:'',dminsttRgnNm:'서울특별시'});
 const r=await analysisRows(params({group:'agency'}));assert.equal(r.groupCount,5);
 assert.deepEqual(r.rows.map(x=>x.amount),['900','600','300','200','100']);
 assert.equal(r.rows[0].agency_code,'SAME');assert.equal(r.rows[0].rows,2);
 assert.deepEqual(r.rows.filter(x=>x.name==='도로관리과').map(x=>x.region).sort(),['','부산광역시','서울특별시']);
 assert.equal((await analysisRows(params({group:'agency',agency:'도로관리과',region:'서울특별시'}))).rows[0].amount,'100');
});

test('all 503 identities rank before the 200-row boundary, with explicit truncation',async t=>{
 const {db,reads}=setup(t);
 for(let n=0;n<503;n++){const name=String(n).padStart(4,'0');add(db,name,(100000000000000000000n+BigInt(n)).toString(),{corpNm:name,corpBizno:name});}
 const r=await analysisRows(params());assert.equal(r.groupCount,503);assert.equal(r.rowCount,503);assert.equal(r.rows.length,200);assert.equal(r.rows[0].name,'0502');assert.equal(r.rows.at(-1).name,'0303');assert.equal(r.truncated,true);
 const aggregate=reads.find(x=>x.sql.includes('carried AS'));assert.equal(aggregate.rows,200);assert.match(aggregate.sql,/ORDER BY .*LIMIT 200/s);assert.ok(r.rows.every(row=>!('limb_sums'in row)&&!('raw'in row)&&!('source_key'in row)));
});

test('over-width and malformed stored values explicitly disable money ranking, not approximate',async t=>{
 const {db}=setup(t);add(db,'too-wide','9'.repeat(73));let r=await analysisRows(params());assert.equal(r.monetaryStatus,'unavailable');assert.equal(r.moneyUnavailable,'AMOUNT_EXCEEDS_SUMMARY_DIGIT_LIMIT');assert.equal(r.rows.length,0);assert.equal(r.rowCount,1);assert.equal(r.limits.maxAmountDigits,72);
 for(const invalid of ['1.5','1e3','--1','+1','01','-0','12x','3\0hidden']){
  db.prepare('UPDATE delivery_rows SET amount=?').run(invalid);db.exec("UPDATE dataset_epochs SET revision=revision+1 WHERE mode='synthetic'");r=await analysisRows(params());assert.equal(r.moneyUnavailable,'INVALID_STORED_AMOUNT',invalid);assert.equal(r.invalidAmounts,1);assert.deepEqual(r.rows,[]);
 }
 const query=summaryQuery(plan(),'vendor');assert.deepEqual(db.prepare(query.sql).all(...query.args),[],'direct SQL also fails closed');
});

test('empty and unknown-only selections preserve counts and completeness',async t=>{
 const {db}=setup(t);let r=await analysisRows(params());assert.deepEqual(r.rows,[]);assert.equal(r.moneyComplete,true);assert.equal(r.rowCount,0);assert.equal(r.truncated,false);
 add(db,'unknown',null);db.exec("UPDATE dataset_epochs SET revision=revision+1 WHERE mode='synthetic'");r=await analysisRows(params());assert.equal(r.rows[0].amount,null);assert.equal(r.monetaryStatus,'partial');assert.equal(r.moneyUnavailable,null);assert.equal(r.missingAmounts,1);
});

test('source changes between preflight and sums suppress money and abort the response',async t=>{
 const {db,reads}=setup(t,(sql,d)=>{if(sql.includes('row_count')&&!sql.includes('carried AS')){d.prepare('UPDATE delivery_rows SET amount=?').run('9'.repeat(100));d.exec("UPDATE dataset_epochs SET revision=revision+1 WHERE mode='synthetic'");}});
 add(db,'one','1');await assert.rejects(analysisRows(params()),/DELIVERY_SUMMARY_SOURCE_CHANGED_RETRY/);assert.equal(reads.find(r=>r.sql.includes('carried AS')).rows,0);
});

test('source changes after sums also abort before returning a ranked result',async t=>{
 const {db}=setup(t,(sql,d)=>{if(sql.includes('carried AS'))d.exec("UPDATE dataset_epochs SET revision=revision+1 WHERE mode='synthetic'");});
 add(db,'one','1');await assert.rejects(analysisRows(params()),/DELIVERY_SUMMARY_SOURCE_CHANGED_RETRY/);
});

test('service reports the row safety limit without running the money query',async t=>{
 const {db,reads}=setup(t,(sql,d,row)=>{if(sql.includes('row_count')&&!sql.includes('carried AS'))row.row_count=String(DELIVERY_SUMMARY_MAX_ROWS+1);});
 add(db,'one','1');const r=await analysisRows(params());assert.equal(r.rowCount,DELIVERY_SUMMARY_MAX_ROWS+1);assert.equal(r.moneyUnavailable,'ROW_COUNT_EXCEEDS_EXACT_SUMMARY_LIMIT');assert.equal(r.monetaryStatus,'unavailable');assert.deepEqual(r.rows,[]);assert.ok(reads.every(r=>!r.sql.includes('carried AS')));
});

test('a missing source epoch fails closed and exact epoch text is preserved',async t=>{
 const {db}=setup(t);add(db,'one');db.exec("UPDATE dataset_epochs SET revision=9007199254740993 WHERE mode='synthetic'");
 // The adapter models D1's numeric transport for cache revision reads. The
 // summary's own text fence must retain the exact source revision regardless.
 assert.equal((await analysisRows(params())).sourceRevision,'9007199254740993');db.exec("DELETE FROM dataset_epochs WHERE mode='synthetic'");await assert.rejects(analysisRows(params()),/DELIVERY_SUMMARY_SOURCE_NOT_FOUND/);
});

test('row and digit bounds fail closed before unsafe sums, with exact public counts',()=>{
 const empty={row_count:'0',group_count:'0',missing_amounts:'0',invalid_amounts:'0',max_digits:0};
 const limit=BigInt(DELIVERY_SUMMARY_MAX_ROWS)*BigInt(DELIVERY_SUMMARY_LIMB_BASE)-1n;
 assert.ok(limit<=BigInt(Number.MAX_SAFE_INTEGER));assert.ok(limit+BigInt(DELIVERY_SUMMARY_LIMB_BASE)>BigInt(Number.MAX_SAFE_INTEGER));
 assert.equal(deliverySummaryMoneyState({...empty,row_count:String(DELIVERY_SUMMARY_MAX_ROWS),max_digits:DELIVERY_SUMMARY_MAX_DIGITS}).moneyUnavailable,null);
 for(const count of [BigInt(DELIVERY_SUMMARY_MAX_ROWS)+1n,9007199254740993n]){
  const stats={...empty,row_count:count.toString()};const r=deliverySummaryMoneyState(stats);assert.equal(r.moneyUnavailable,'ROW_COUNT_EXCEEDS_EXACT_SUMMARY_LIMIT');assert.equal(r.moneyComplete,false);assert.equal(String(r.rowCount),count.toString());assert.throws(()=>summaryQuery(plan(),'vendor','',{preflight:stats}),/UNSAFE_AGGREGATE/);
 }
 for(const patch of [{row_count:1},{row_count:'-1'},{group_count:'1'},{missing_amounts:'1'},{max_digits:NaN}])assert.throws(()=>deliverySummaryMoneyState({...empty,...patch}),/INVALID_AGGREGATE/);
});

test('query plans keep indexed latest checks and bound only the final SQL-ranked result',t=>{
 const {db}=setup(t);add(db,'one');
 for(const group of ['agency','vendor']){
  const p=plan(),pre=summaryPreflightQuery(p,group),stats=db.prepare(pre.sql).get(...pre.args),sum=summaryQuery(p,group,'',{preflight:stats,revision:'0'});
  for(const query of [pre,sum]){
   const explain=db.prepare('EXPLAIN QUERY PLAN '+query.sql).all(...query.args).map(x=>x.detail).join('\n');
   assert.match(explain,/SEARCH n USING INDEX idx_delivery_current \(mode=\? AND is_active=\? AND request_no=\? AND sequence=\?/);
   assert.match(explain,/SEARCH observed USING INDEX/);
   if(query===sum){assert.match(explain,/RECURSIVE STEP/);assert.match(explain,/TEMP B-TREE FOR ORDER BY/);}
   t.diagnostic(group+' '+(query===pre?'preflight':'sum')+' EXPLAIN:\n'+explain);
  }
  assert.ok(sum.sql.length<15000);assert.equal(sum.limbs,1);
 }
});

test('vendor categories come from selected alias rows only, including numberless fallback',async t=>{
 const {db}=setup(t);
 add(db,'lamp','10',{corpNm:'old name'});
 add(db,'pole','20',{corpNm:'new name',dtilPrdctClsfcNo:'3911152601'});
 add(db,'fallback','5',{corpNm:'new name',corpBizno:'',dtilPrdctClsfcNo:'3911152601'});
 add(db,'outside','999',{corpNm:'other',corpBizno:'999'});
 let r=await analysisRows(params({q:'old name'}));
 assert.deepEqual(r.rows[0].categories,['LIGHTING','POLE']);assert.equal(r.rows[0].amount,'35');assert.equal(r.rows[0].rows,3);
 r=await analysisRows(params({category:'POLE',q:'123'}));assert.deepEqual(r.rows[0].categories,['POLE']);assert.equal(r.rows[0].amount,'25');
 assert.ok(!('has_lighting' in r.rows[0]));assert.ok(!('has_pole' in r.rows[0]));
});

test('1000 vendor review pages transfer at most 200, retain exact signed order and stable identity ties',async t=>{
 const {db,reads}=setup(t),expected=[];
 for(let n=0;n<1003;n++){
  const bizno=String(n).padStart(4,'0'),amount=(BigInt(Math.floor(n/3))-170n)*100000000000000000000n;
  add(db,'page-'+n,amount.toString(),{corpNm:'same name',corpBizno:bizno});expected.push({bizno,amount:amount.toString()});
 }
 expected.sort((a,b)=>BigInt(a.amount)>BigInt(b.amount)?-1:BigInt(a.amount)<BigInt(b.amount)?1:a.bizno.localeCompare(b.bizno));
 let cursor=null;const actual=[];
 for(let page=0;page<5;page++){
  const r=await analysisRows(params({reviewLimit:'1000',...(cursor?{cursor}:{})}));
  assert.equal(r.offset,page*200);assert.equal(r.pageSize,200);assert.equal(r.reviewLimit,1000);assert.equal(r.totalGroups,1003);assert.equal(r.selectedGroups,1000);assert.equal(r.truncated,true);assert.equal(r.providerCalls,0);
  actual.push(...r.rows.map(({bizno,amount})=>({bizno,amount})));cursor=r.next;
  assert.equal(Boolean(cursor),page<4);
 }
 assert.deepEqual(actual,expected.slice(0,1000));assert.equal(new Set(actual.map(x=>x.bizno)).size,1000);
 assert.ok(reads.filter(x=>x.sql.includes('carried AS')).every(x=>x.rows<=200));
 const small=await analysisRows(params({limit:'10'}));assert.equal(small.rows.length,10);assert.equal(small.next,null);assert.equal(small.reviewLimit,10);
 const uneven=await analysisRows(params({reviewLimit:'201'}));const last=await analysisRows(params({reviewLimit:'201',cursor:uneven.next}));assert.equal(last.rows.length,1);assert.equal(last.pageSize,1);assert.equal(last.offset,200);assert.equal(last.next,null);
 const agency=await analysisRows(params({group:'agency',reviewLimit:'1000'}));assert.equal(agency.reviewLimit,200);
});

test('cursor rejects changed scope, malformed offsets, source changes and cached-page races',async t=>{
 let race=false;
 const {db}=setup(t,(sql,d)=>{if(race&&sql.startsWith('SELECT e.revision,c.payload')){race=false;d.exec("UPDATE dataset_epochs SET revision=revision+1 WHERE mode='synthetic'");}});
 for(let n=0;n<403;n++)add(db,'cursor-'+n,'1',{corpNm:'vendor '+n,corpBizno:String(n)});
 const first=await analysisRows(params({reviewLimit:'500'}));
 const base={reviewLimit:'500',cursor:first.next};
 for(const patch of [{reviewLimit:'1000'},{mode:'live'},{category:'POLE'},{region:'부산광역시'},{q:'other'},{group:'agency'},{from:scopeWindow().to}])await assert.rejects(analysisRows(params({...base,...patch})),/INVALID_CURSOR/);
 for(const cursor of ['broken','null','x'.repeat(1025),JSON.stringify({...JSON.parse(first.next),offset:201}),JSON.stringify({...JSON.parse(first.next),offset:1000})])await assert.rejects(analysisRows(params({...base,cursor})),/INVALID_CURSOR/);
 await analysisRows(params(base)); // Populate second-page cache.
 race=true;await assert.rejects(analysisRows(params(base)),/SOURCE_CHANGED_RETRY/);
 await assert.rejects(analysisRows(params(base)),/SOURCE_CHANGED_RETRY/);
 const fresh=await analysisRows(params({reviewLimit:'500'}));assert.notEqual(fresh.sourceRevision,first.sourceRevision);
 for(const reviewLimit of ['9','1001','NaN','200.5',''])await assert.rejects(analysisRows(params({reviewLimit})),/INVALID_REVIEW_LIMIT/);
});

test('review ends at real population, and direct query page bounds fail closed',async t=>{
 const {db}=setup(t);for(let n=0;n<203;n++)add(db,'short-'+n,'1',{corpNm:String(n),corpBizno:String(n)});
 const first=await analysisRows(params({reviewLimit:'1000'})),last=await analysisRows(params({reviewLimit:'1000',cursor:first.next}));
 assert.equal(last.offset,200);assert.equal(last.rows.length,3);assert.equal(last.totalGroups,203);assert.equal(last.selectedGroups,203);assert.equal(last.next,null);assert.equal(last.truncated,false);
 for(const options of [{pageSize:201},{pageSize:0},{offset:801},{offset:-1},{offset:0.5}])assert.throws(()=>summaryQuery(plan(),'vendor','',options),/INVALID_PAGE/);
});

test('analysis route preserves invalid review limit errors',async t=>{
 setup(t);const {GET}=await import('../app/api/analysis/route.ts');
 const response=await GET(new Request('https://example.test/api/analysis?'+params({reviewLimit:'1001'})));
 assert.equal(response.status,409);assert.deepEqual(await response.json(),{error:'DELIVERY_SUMMARY_INVALID_REVIEW_LIMIT'});
});

test('analysis route preserves cursor errors for malformed tokens and changed mode or filters',async t=>{
 const {db}=setup(t);const {GET}=await import('../app/api/analysis/route.ts');
 for(let n=0;n<201;n++)add(db,'route-'+n,'1',{corpNm:'vendor '+n,corpBizno:String(n)});
 const first=await analysisRows(params({reviewLimit:'1000'}));
 for(const patch of [{cursor:'broken'},{mode:'live'},{category:'POLE'},{q:'other'}]){
  const response=await GET(new Request('https://example.test/api/analysis?'+params({reviewLimit:'1000',cursor:first.next,...patch})));
  assert.equal(response.status,409);assert.deepEqual(await response.json(),{error:'DELIVERY_SUMMARY_INVALID_CURSOR'});
 }
});

test('analysis route serves unchanged cached pages and preserves source-change rejection including cache races',async t=>{
 let race=false;const {db}=setup(t,(sql,d)=>{if(race&&sql.startsWith('SELECT e.revision,c.payload')){race=false;d.exec("UPDATE dataset_epochs SET revision=revision+1 WHERE mode='synthetic'");}});
 const {GET}=await import('../app/api/analysis/route.ts');
 for(let n=0;n<201;n++)add(db,'route-epoch-'+n,'1',{corpNm:'vendor '+n,corpBizno:String(n)});
 const first=await analysisRows(params({reviewLimit:'1000'}));
 const url='https://example.test/api/analysis?'+params({reviewLimit:'1000',cursor:first.next});
 const initial=await GET(new Request(url));assert.equal(initial.status,200);const expected=await initial.json();
 const cached=await GET(new Request(url));assert.equal(cached.status,200);assert.deepEqual(await cached.json(),expected);
 race=true;const changed=await GET(new Request(url));assert.equal(changed.status,409);assert.deepEqual(await changed.json(),{error:'DELIVERY_SUMMARY_SOURCE_CHANGED_RETRY'});
 const stale=await GET(new Request(url));assert.equal(stale.status,409);assert.deepEqual(await stale.json(),{error:'DELIVERY_SUMMARY_SOURCE_CHANGED_RETRY'});
});
