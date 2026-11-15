import test from 'node:test';
import assert from 'node:assert/strict';
import {LIMITS,VERSION,canonical,sha256,strictIdentity,validateGenerationPage} from '../lib/g2b-generation-domain.mjs';
import {mixedFixture} from '../lib/g2b-domain.mjs';
const day='2026-11-05',window={from:'2026-01-01',to:'2026-11-09'};
const page=(n=1,total=2)=>mixedFixture(day,n,999,total);
const validate=(raw,n=1,total=null)=>validateGenerationPage(raw,{page:n,total,day,window});
const row=()=>page().response.body.items[0];

test('frozen contract limits and version declare each algorithm',()=>{
 assert.ok(Object.isFrozen(LIMITS));assert.equal(LIMITS.pageSize,999);assert.equal(LIMITS.maxRows,20000);assert.equal(LIMITS.maxPages,21);assert.equal(LIMITS.maxAttempts,42);assert.equal(LIMITS.maxResponseBytes,8*1024*1024);assert.equal(LIMITS.maxPayloadBytes,64*1024*1024);assert.equal(LIMITS.maxBindingBytes,1400000);assert.equal(LIMITS.maxStatements,64);assert.equal(LIMITS.maxRunPages,8);assert.equal(LIMITS.maxRunMs,90000);for(const x of ['identity','canonical','classification'])assert.ok(VERSION.includes(x));
});
test('canonical sorting preserves arrays, scalar types, unknown fields and minus zero',async()=>{
 assert.equal(canonical({b:[true,null,'1',1,-0],a:'한글'}),'{'+'"a":"한글","b":[true,null,"1",1,-0]}');
 assert.equal(canonical({z:2,a:1}),canonical({a:1,z:2}));
 assert.notEqual(await sha256(canonical({a:1})),await sha256(canonical({a:'1'})));
 assert.equal(await sha256('abc'),'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
});
test('canonical rejects lossy or unsupported JSON without evaluating accessors',()=>{
 for(const v of [Infinity,NaN,-Infinity,9007199254740992,undefined,()=>{},Symbol('x'),1n,new Date(),new Map(),Object.create(null),[,1],Object.assign([1],{x:2})])assert.throws(()=>canonical(v),/SOURCE_/);
 const x={};x.x=x;assert.throws(()=>canonical(x),/CYCLE/);let deep={};for(let n=0;n<34;n++)deep={next:deep};assert.throws(()=>canonical(deep),/LIMIT/);
 let evaluated=0;const access={get x(){evaluated++;return 'SECRET';}};assert.throws(()=>canonical(access),/UNSUPPORTED/);assert.equal(evaluated,0);
 assert.throws(()=>canonical({[Symbol('key')]:1}),/UNSUPPORTED/);assert.throws(()=>canonical(Object.defineProperty({},'hidden',{value:1})),/UNSUPPORTED/);
});
test('strict identity keeps v35 normalization but rejects truncation and every non-string alias',()=>{
 assert.equal(strictIdentity({deliveryReqNo:' R ',deliveryReqChangeOrder:'0002',dlvrReqDtlSeq:'3'}),'["R","2","3"]');
 assert.equal(strictIdentity({dlvrReqNo:'R',prdctSno:'3'}),'["R","0","3"]');
 for(const v of [5,null,false,{},undefined])assert.throws(()=>strictIdentity({...row(),reqNo:v}),/TYPE_INVALID/);
 assert.throws(()=>strictIdentity({...row(),dlvrReqNo:'a'.repeat(1025)}),/TOO_LONG/);
 assert.throws(()=>strictIdentity({...row(),reqNo:'a'.repeat(1025)}),/TOO_LONG/);
 assert.throws(()=>strictIdentity({...row(),dlvrReqNo:''}),/MISSING/);
 assert.throws(()=>strictIdentity({...row(),prdctSno:''}),/MISSING/);
});
test('happy page retains complete source evidence and exact classification reconciliation',async()=>{
 const a=await validate(page(1,999));assert.equal(a.rows.length,999);assert.equal(a.total,999);assert.equal(a.done,true);assert.equal(a.matched+a.excluded,999);assert.equal(a.rows[0].ordinal,1);assert.equal(a.rows.at(-1).ordinal,999);assert.equal(a.rows[0].source_date,day);assert.equal(a.rows[0].date_basis,'source');assert.equal(a.ordered_hash.length,64);assert.ok(a.payloadBytes>0);assert.ok(a.rows.some(r=>r.target_json===null&&r.excluded==='CATEGORY_NOT_ALLOWED'));
 const b=await validate(page(2,1001),2,1001);assert.equal(b.rows.length,2);assert.equal(b.rows[0].ordinal,1000);assert.equal(b.done,true);
});
test('empty date is a valid page1 observation, not a completeness or promotion result',async()=>{
 const a=await validate(page(1,0));assert.equal(a.total,0);assert.equal(a.done,true);assert.deepEqual(a.rows,[]);assert.equal(a.matched,0);assert.equal(a.excluded,0);
 for(const field of ['complete','recovered','sourceCompletenessVerified','canPromote'])assert.equal(Object.hasOwn(a,field),false);
 await assert.rejects(validate(page(2,0),2,0),/PAGE_LIMIT/);
});
test('source total cap, page range, prior total and exact envelope metadata fail closed',async()=>{
 await assert.rejects(validate(page(1,20001)),/CAPACITY/);
 await assert.rejects(validate(page(22,20000),22,20000),/PAGE_LIMIT/);
 await assert.rejects(validate(page(2,1000),2),/PREVIOUS_TOTAL/);
 await assert.rejects(validate(page(2,1001),2,1000),/TOTAL_CHANGED/);
 for(const value of [null,'',-1,1.2,'2e0',9007199254740992]){const p=page();p.response.body.totalCount=value;await assert.rejects(validate(p),/SOURCE_/);}
 for(const key of ['numOfRows','pageNo']){const p=page();p.response.body[key]=88;await assert.rejects(validate(p),/PAGE_MISMATCH/);}
});
test('short and overfull pages never receive receipts from the validator',async()=>{
 const short=page();short.response.body.items.pop();await assert.rejects(validate(short),/ROW_COUNT_MISMATCH/);
 const long=page();long.response.body.items.push({...row(),dlvrReqNo:'third'});await assert.rejects(validate(long),/ROW_COUNT_MISMATCH/);
});
test('within-page duplicate evidence is hashed, bounded and distinguishes changed content',async()=>{
 for(const changed of [false,true]){const p=page();p.response.body.items[1]={...p.response.body.items[0],...(changed?{prdctAmt:'777'}:{})};
 await assert.rejects(validate(p),e=>{assert.equal(e.message,'SOURCE_DUPLICATE_ID');assert.equal(e.conflict.count,1);const c=e.conflict.samples[0];assert.equal(c.firstPage,1);assert.equal(c.currentPage,1);assert.equal(c.samePayload,!changed);for(const k of ['identityHash','firstPayloadHash','currentPayloadHash'])assert.match(c[k],/^[a-f0-9]{64}$/);assert.ok(!JSON.stringify(e.conflict).includes('SYNTHETIC'));return true;});}
 const p=page(1,20);p.response.body.items=p.response.body.items.map(()=>({...row()}));await assert.rejects(validate(p),e=>{assert.equal(e.conflict.count,19);assert.equal(e.conflict.samples.length,8);return true;});
});
test('ordered digest catches reordering, changed raw content and excluded content',async()=>{
 const p=page(1,6),original=await validate(p);const q=structuredClone(p);q.response.body.items.reverse();assert.notEqual((await validate(q)).ordered_hash,original.ordered_hash);
 const r=structuredClone(p);r.response.body.items[0].unknownField='new';assert.notEqual((await validate(r)).ordered_hash,original.ordered_hash);
 const s=structuredClone(p);s.response.body.items[4].unknownField='excludedChanged';assert.equal(original.rows[4].target_json,null);assert.notEqual((await validate(s)).ordered_hash,original.ordered_hash);
 const reordered=JSON.parse(JSON.stringify(p,(k,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.entries(v).reverse()):v));assert.equal((await validate(reordered)).ordered_hash,original.ordered_hash);
});
test('source-date versus query fallback is explicit and malformed nonempty dates stop',async()=>{
 const p=page();delete p.response.body.items[0].dlvrReqRcptDate;const a=await validate(p);assert.equal(a.rows[0].date_basis,'query_date');assert.equal(a.rows[0].source_date,day);
 p.response.body.items[0].dlvrReqRcptDate='2026-11-04';const b=await validate(p);assert.equal(b.rows[0].date_basis,'source');assert.equal(b.rows[0].source_date,'2026-11-04');
 for(const value of ['oops','2026-02-30',11111111]){p.response.body.items[0].dlvrReqRcptDate=value;await assert.rejects(validate(p),/DATE_INVALID/);}
});
test('unsafe numbers anywhere in source, including excluded/unknown data, stop',async()=>{
 const p=page(1,6);p.response.body.items[4].unknownUnsafe=9007199254740992;await assert.rejects(validate(p),/NUMBER_OUT_OF_RANGE/);
});
test('staging binding and full response capacity reject oversized records',async()=>{
 const p=page(1,1);p.response.body.items[0].large='x'.repeat(1400000);await assert.rejects(validate(p),/RECORD_TOO_LARGE/);
 p.response.body.items[0].large='x'.repeat(LIMITS.maxResponseBytes);await assert.rejects(validate(p),/RESPONSE_TOO_LARGE/);
});
test('maximum allowed tail has exact page21 position and count',async()=>{
 const p=await validate(page(21,20000),21,20000);assert.equal(p.rows.length,20);assert.equal(p.rows[0].ordinal,19981);assert.equal(p.rows.at(-1).ordinal,20000);assert.equal(p.done,true);
});
