import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import {SYNC_SOURCE_SUMMARY_SQL,syncAvailability} from '../lib/sync-source-summary.mjs';

function receiptDatabase(){
 const db=new DatabaseSync(':memory:');
 db.exec(readFileSync(new URL('../drizzle/0020_colossal_the_hand.sql',import.meta.url),'utf8'));
 return db;
}

function insertReceipt(db,{
 dataset='budget',scope='2026:2026-11-23',generation='active',page=1,rows=999,matched=rows,
 sourceAt='2026-11-24T00:00:00Z',receivedAt=sourceAt,state='RUNNING',saved=rows,total=-1,
}={}){
 const id=[dataset,scope,generation,page].join('/');
 db.prepare(`INSERT INTO server_sync_pages
  (id,dataset,scope,generation,page_no,revision,content_hash,source_rows,matched_rows,
   source_saved_count,source_total,source_status,source_updated_at,received_at)
  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
  id,dataset,scope,generation,page,'revision','hash',rows,matched,saved,total,state,sourceAt,receivedAt);
}

test('later empty receipt does not advance the displayed data date or hide partial budgets',()=>{
 const db=receiptDatabase();
 insertReceipt(db,{dataset:'shopping_delivery',scope:'2026-11-14',matched:12,sourceAt:'2026-11-15T00:00:00Z',state:'COMPLETE',total:999});
 insertReceipt(db,{dataset:'shopping_delivery',scope:'2026-11-23',rows:0,state:'COMPLETE'});
 insertReceipt(db,{scope:'2026:2026-11-14',page:465,sourceAt:'2026-11-23T00:00:00Z',saved:464535,total:478628});
 insertReceipt(db,{scope:'2026:2026-11-14',page:466,saved:465534,total:478628});
 db.exec('PRAGMA query_only=ON');
 const sources=db.prepare(SYNC_SOURCE_SUMMARY_SQL).all(),[delivery,budget]=syncAvailability({sources});
 assert.equal(delivery.dataDay,'2026-11-14');assert.equal(delivery.checkedDay,'2026-11-23');assert.equal(delivery.emptyAfterData,true);
 assert.equal(delivery.received,0);assert.equal(delivery.pending,0);
 assert.equal(budget.dataDay,'2026-11-14');assert.equal(budget.saved,465534);assert.equal(budget.total,478628);assert.equal(budget.partial,true);
 const link=new URL(delivery.href,'https://example.test');assert.equal(link.searchParams.get('region'),'');assert.equal(link.searchParams.get('from'),'');assert.equal(link.searchParams.get('to'),'2026-11-14');
 db.close();
});

test('complete Windows checkpoint remains ahead until every current-generation page is received',()=>{
 const db=receiptDatabase();
 insertReceipt(db,{generation:'older',sourceAt:'2026-11-23T18:00:00Z',rows:999,state:'COMPLETE',total:999});
 insertReceipt(db,{scope:'2026:2026-11-22',sourceAt:'2026-11-24T03:00:00Z',rows:999,state:'COMPLETE',total:999});
 insertReceipt(db,{total:2997});
 insertReceipt(db,{page:3,sourceAt:'2026-11-24T00:03:00Z',receivedAt:'2026-11-24T01:00:00Z',saved:2997,total:2997,state:'COMPLETE'});

 const read=db.prepare(SYNC_SOURCE_SUMMARY_SQL);
 db.exec('PRAGMA query_only=ON');
 let sources=read.all(),budget=syncAvailability({sources})[1];
 assert.equal(sources[0].source_rows,3996);
 assert.equal(sources[0].latest_scope_received_pages,2);
 assert.equal(budget.checkedDay,'2026-11-23');
 assert.equal(budget.partial,false);
 assert.equal(budget.saved,2997);
 assert.equal(budget.received,1998);
 assert.equal(budget.pending,999);
 assert.equal(budget.receivedAt,'2026-11-24T01:00:00Z');

 db.exec('PRAGMA query_only=OFF');
 insertReceipt(db,{page:2,sourceAt:'2026-11-24T00:02:00Z',receivedAt:'2026-11-24T01:02:00Z',saved:1998,total:2997});
 db.exec('PRAGMA query_only=ON');
 sources=read.all();
 budget=syncAvailability({sources})[1];
 assert.equal(sources[0].latest_scope_received_pages,3);
 assert.equal(budget.saved,2997);
 assert.equal(budget.received,2997);
 assert.equal(budget.pending,0);
 assert.equal(budget.receivedAt,'2026-11-24T01:02:00Z');
 db.close();
});
test('old clients and missing, malformed or unknown metadata remain unknown',()=>{
 for(const value of [null,{}, {sources:[]},{sources:[{dataset:'budget',latest_nonempty_scope:'2026:not-a-date',latest_saved_count:-1,latest_source_total:-1}]}]){
  for(const row of syncAvailability(value)){assert.equal(row.dataDay,null);assert.equal(row.href,null);assert.equal(row.total,null);assert.equal(row.received,null);assert.equal(row.pending,null);}
 }
 const [delivery]=syncAvailability({sources:[{dataset:'shopping_delivery',latest_scope:'2026-11-23',latest_nonempty_scope:null,source_rows:999,matched_rows:0}]});
 assert.equal(delivery.href,null);assert.equal(delivery.checkedDay,'2026-11-23');
 assert.equal(delivery.received,null);
});
