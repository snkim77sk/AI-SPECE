import test from 'node:test';import assert from 'node:assert/strict';import {DatabaseSync} from 'node:sqlite';import {readFileSync,readdirSync} from 'node:fs';import {cachedQuery} from '../lib/g2b-cache.ts';
function setup(){const db=new DatabaseSync(':memory:');for(const f of readdirSync(new URL('../drizzle/',import.meta.url)).filter(x=>x.endsWith('.sql')).sort())db.exec(readFileSync(new URL('../drizzle/'+f,import.meta.url),'utf8'));const d={prepare(sql){return {bind(...p){return {first:async()=>db.prepare(sql).get(...p)||null,_run:()=>db.prepare(sql).run(...p)}}}},async batch(q){db.exec('BEGIN');try{const r=q.map(x=>x._run());db.exec('COMMIT');return r;}catch(e){db.exec('ROLLBACK');throw e;}}};return {db,d};}
test('bounded query cache hits only same mode, key and committed dataset version',async()=>{const {db,d}=setup();let computes=0;const calc=async()=>({n:++computes});assert.equal((await cachedQuery(d,'synthetic',['one'],calc)).n,1);assert.equal((await cachedQuery(d,'synthetic',['one'],calc)).n,1);assert.equal((await cachedQuery(d,'live',['one'],calc)).n,2);db.exec("UPDATE dataset_epochs SET revision=1 WHERE mode='synthetic'");assert.equal((await cachedQuery(d,'synthetic',['one'],calc)).n,3);db.close();});
test('changed epoch while computing never stores a stale result',async()=>{const {db,d}=setup();await cachedQuery(d,'synthetic',['race'],async()=>{db.exec("UPDATE dataset_epochs SET revision=revision+1 WHERE mode='synthetic'");return {n:1};});assert.equal(db.prepare('SELECT COUNT(*) n FROM query_cache').get().n,0);db.close();});
test('query cache stores at most 64 results per mode and avoids large payloads',async()=>{const {db,d}=setup();for(let i=0;i<70;i++)await cachedQuery(d,'synthetic',[i],async()=>({n:i}));assert.equal(db.prepare('SELECT COUNT(*) n FROM query_cache').get().n,64);await cachedQuery(d,'synthetic',['large'],async()=>({text:'x'.repeat(524288)}));assert.equal(db.prepare('SELECT COUNT(*) n FROM query_cache').get().n,64);db.close();});

test('optional cache lookup failure does not suppress the primary stored-data read',async()=>{
 const broken={prepare(){throw Error('D1_ERROR: no such table: query_cache');}};
 let calls=0;
 assert.deepEqual(await cachedQuery(broken,'live',['cached-read-down'],async()=>({rows:++calls})),{rows:1});
 assert.equal(calls,1);
 await assert.rejects(cachedQuery(broken,'live',['source-down'],async()=>{throw Error('PRIMARY_SOURCE_READ_FAILED');}),/PRIMARY_SOURCE_READ_FAILED/);
});
