import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {recentQueryDates,initialQueryDates} from '../lib/query-dates.mjs';
import {budgetCoverage} from '../lib/budget-regions.mjs';
import {budgetQuery} from '../lib/budget-query.mjs';
import {budgetSummaryPlan} from '../lib/budget-summary.mjs';
import {deliveryLocation} from '../lib/delivery-navigation.mjs';

test('seven inclusive days end yesterday in Korea, including midnight/year changes',()=>{
 assert.deepEqual(recentQueryDates(new Date('2026-11-15T10:00:00Z')),{from:'2026-11-08',to:'2026-11-14'});
 assert.deepEqual(recentQueryDates(new Date('2026-11-15T14:59:59Z')),{from:'2026-11-08',to:'2026-11-14'});
 assert.deepEqual(recentQueryDates(new Date('2026-11-15T15:00:00Z')),{from:'2026-11-09',to:'2026-11-15'});
 assert.deepEqual(recentQueryDates(new Date('2027-01-01T00:00:00Z')),{from:'2026-12-25',to:'2026-12-31'});
 const now=new Date('2026-11-15T10:00:00Z');
 assert.equal(deliveryLocation('/shopping','','data',now).from,'2026-11-08');
 assert.deepEqual(initialQueryDates(new URLSearchParams('from=&to='),now),{from:'',to:''});
 assert.deepEqual(initialQueryDates(new URLSearchParams('start_date=2026-01-01&end_date=2026-01-31'),now),{from:'2026-01-01',to:'2026-01-31'});
});

test('coverage separates source populations and never adds empty Seoul/Gyeonggi as collected regions',()=>{
 const rows=[{service:'QWGJK',region:'서울',rows:9},{service:'QWGJK',region:'전남광주',rows:4},{service:'AIDFA',region:'충남',rows:830},{service:'AIDFA',region:'충북',rows:600}];
 const coverage=budgetCoverage(rows,'QWGJK');
 assert.deepEqual(coverage.services.map(x=>[x.rows,x.regionCount]),[[13,2],[1430,2]]);
 assert.equal(coverage.choices.find(x=>x.region==='충청남도').rows,0);
 assert.equal(budgetCoverage(rows,'AIDFA').choices.find(x=>x.region==='충청남도').rows,830);
 assert.equal(coverage.regions.includes('경기도'),false);
 assert.equal(coverage.sourceCompletenessVerified,false);
});

test('stored short region names roundtrip through full-name filters without modifying source rows',()=>{
 const db=new DatabaseSync(':memory:');
 db.exec('CREATE TABLE budget_rows(mode,service,fiscal_year,is_active,region,source_key,snapshot_date,category,org,dept,project,row_json)');
 const insert=db.prepare('INSERT INTO budget_rows VALUES(?,?,?,?,?,?,?,?,?,?,?,?)');
 for(const [key,region,date,service] of [['a','충남','2026-11-14','QWGJK'],['b','충청남도','2026-11-08','QWGJK'],['c','충북','2026-11-14','QWGJK'],['d','충남','2026-11-07','QWGJK'],['e','인천','2026-11-14','QWGJK'],['f','충남','2026-01-01','AIDFA']])insert.run('live',service,2026,1,region,key,date,'OTHER','기관','부서','사업',JSON.stringify({budget_amount:'100',remaining_amount:'10'}));
 const read=extra=>{const p=new URLSearchParams({mode:'live',year:'2026',from:'2026-11-08',to:'2026-11-14',...extra}),plan=budgetQuery(p,'2026-11-15');return db.prepare(`SELECT source_key FROM budget_rows WHERE ${plan.where.join(' AND ')} ORDER BY ${plan.order}`).all(...plan.args).map(x=>x.source_key);};
 assert.deepEqual(read({region:'충청남도'}),['a','b']);
 assert.deepEqual(read({region:'충남'}),['a','b']);
 assert.deepEqual(read({region:'충청북도'}),['c']);
 assert.deepEqual(read({incheonScope:'INCHEON_ALL'}),['e']);
 assert.deepEqual(read({region:'충청남도',service:'AIDFA'}),['f']);
 assert.deepEqual(read({region:"' OR 1=1 --"}),[]);
 const p=new URLSearchParams({region:'충청남도',from:'2026-11-08',to:'2026-11-14'});
 assert.deepEqual(budgetSummaryPlan(p,'2026-11-15').where,budgetQuery(p,'2026-11-15').where);
 assert.throws(()=>read({from:'2026-02-30'}),/INVALID_DATE_RANGE/);
 assert.throws(()=>read({from:'2026-11-15',to:'2026-11-14'}),/INVALID_DATE_RANGE/);
 assert.equal(db.prepare("SELECT region FROM budget_rows WHERE source_key='a'").get().region,'충남');
 db.close();
});
