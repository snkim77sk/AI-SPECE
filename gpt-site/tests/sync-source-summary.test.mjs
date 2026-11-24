import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {SYNC_SOURCE_SUMMARY_SQL,syncAvailability} from '../lib/sync-source-summary.mjs';

test('later empty receipt does not advance the displayed data date or hide partial budgets',()=>{
 const db=new DatabaseSync(':memory:');
 db.exec('CREATE TABLE server_sync_pages(dataset,scope,page_no,source_rows,matched_rows,source_updated_at,received_at,source_status,source_saved_count,source_total)');
 const insert=db.prepare('INSERT INTO server_sync_pages VALUES(?,?,?,?,?,?,?,?,?,?)');
 insert.run('shopping_delivery','2026-11-14',1,999,12,'2026-11-15T00:00:00Z','2026-11-15T00:01:00Z','COMPLETE',999,999);
 insert.run('shopping_delivery','2026-11-23',1,0,0,'2026-11-24T00:00:00Z','2026-11-24T00:01:00Z','COMPLETE',0,-1);
 insert.run('budget','2026:2026-11-14',465,999,999,'2026-11-23T00:00:00Z','2026-11-23T00:01:00Z','RUNNING',464535,478628);
 insert.run('budget','2026:2026-11-14',466,999,999,'2026-11-24T00:00:00Z','2026-11-24T00:01:00Z','RUNNING',465534,478628);
 db.exec('PRAGMA query_only=ON');
 const sources=db.prepare(SYNC_SOURCE_SUMMARY_SQL).all(),[delivery,budget]=syncAvailability({sources});
 assert.equal(delivery.dataDay,'2026-11-14');assert.equal(delivery.checkedDay,'2026-11-23');assert.equal(delivery.emptyAfterData,true);
 assert.equal(budget.dataDay,'2026-11-14');assert.equal(budget.saved,465534);assert.equal(budget.total,478628);assert.equal(budget.partial,true);
 const link=new URL(delivery.href,'https://example.test');assert.equal(link.searchParams.get('region'),'');assert.equal(link.searchParams.get('from'),'');assert.equal(link.searchParams.get('to'),'2026-11-14');
 db.close();
});
test('old clients and missing, malformed or unknown metadata remain unknown',()=>{
 for(const value of [null,{}, {sources:[]},{sources:[{dataset:'budget',latest_nonempty_scope:'2026:not-a-date',latest_saved_count:-1,latest_source_total:-1}]}]){
  for(const row of syncAvailability(value)){assert.equal(row.dataDay,null);assert.equal(row.href,null);assert.equal(row.total,null);}
 }
 const [delivery]=syncAvailability({sources:[{dataset:'shopping_delivery',latest_scope:'2026-11-23',latest_nonempty_scope:null,source_rows:999,matched_rows:0}]});
 assert.equal(delivery.href,null);assert.equal(delivery.checkedDay,'2026-11-23');
});
