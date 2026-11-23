import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {parseReviewInput,moneyLabel,reviewCsv,REVIEW_MAX_BYTES,REVIEW_MAX_ROWS} from '../lib/review-preview.mjs';

test('local contract CSV handles quoted commas/newlines, LED filtering and separate LH/SH identities',()=>{
 const csv='\uFEFF납품요구번호,품목순번,식별번호,변경차수,수요기관,품명,계약금액,납품요구일자\n'+
 '"REQ-1","1","25191340","0","한국토지주택공사","LED, 실내조명","12,000","2026-11-23"\n'+
 '"REQ-2","1","23841816","0","서울주택도시공사","등주","21000","20261122"\n'+
 '"REQ-1","1","25191340","0","한국토지주택공사","LED, 실내조명","12,000","2026-11-23"\n';
 const value=parseReviewInput(csv,'contract');
 assert.equal(value.format,'CSV/TSV');assert.equal(value.total,3);assert.equal(value.rows.length,2);assert.equal(value.duplicates,1);
 assert.equal(value.rows[0].institution,'LH');assert.equal(value.rows[1].institution,'SH');
 assert.equal(value.rows[0].source_date,'2026-11-23');assert.equal(value.rows[0].isLighting,true);
 assert.equal(value.rows[0].amount,'12000');
 assert.equal(value.ephemeral,true);
});
test('JSON contract and budgets preserve source amount units and require stable record keys',()=>{
 const rows=parseReviewInput(JSON.stringify({rows:[{dlvrReqNo:'R1',prdctSno:1,prdctIdntNo:'A',prdctAmt:1000,dminsttNm:'LH 공사',dtilPrdctClsfcNo:'3911160301'},{item_name:'LED but no contract id'}]}),'contract');
 assert.equal(rows.total,2);assert.equal(rows.excluded,1);assert.equal(rows.rows[0].amount,'1000');
 const b=parseReviewInput('세부사업명,기관명,예산액(천원),금액단위,예산잔액,기준일\n"학교 LED 공사","교육청","1,000","천원","200","20261123"\n','budget');
 assert.equal(b.rows.length,1);assert.equal(b.rows[0].project,'학교 LED 공사');
 assert.equal(b.rows[0].amount_unit,'천원');assert.equal(b.rows[0].remaining_amount,'200');
 assert.equal(b.rows[0].budget_amount,null); // unknown non-aliased header is not silently converted
});
test('budget monetary values do not invent won units or remaining balances',()=>{
 const rows=parseReviewInput(JSON.stringify([{project:'시설 개선사업',org:'기관',budget_amount:'150000',appropriation_amount:'500000',amount_unit:'미상'},{project:'조명계획',org:'기관',budget_amount:'0'}]),'budget');
 assert.equal(rows.rows[0].budget_amount,'150000');assert.equal(rows.rows[0].remaining_amount,null);assert.equal(rows.rows[0].amount_unit,'미상');
 assert.equal(rows.rows[1].budget_amount,'0');assert.equal(moneyLabel('-1200'),'-1,200');
});
test('source files reject invalid syntax, huge payloads and oversized populations',()=>{
 assert.throws(()=>parseReviewInput('"invalid,foo\nbar','contract'),/REVIEW_INVALID_CSV/);
 assert.throws(()=>parseReviewInput('{oops','contract'),/REVIEW_INVALID_JSON/);
 assert.throws(()=>parseReviewInput('[]','contract'),/REVIEW_NO_ROWS|REVIEW_INVALID_JSON_ROWS/);
 assert.throws(()=>parseReviewInput('x'.repeat(REVIEW_MAX_BYTES+1),'contract'),/REVIEW_FILE_TOO_LARGE/);
 assert.throws(()=>parseReviewInput(JSON.stringify(Array(REVIEW_MAX_ROWS+1).fill({project:'a'})),'budget'),/REVIEW_TOO_MANY_ROWS/);
 assert.throws(()=>parseReviewInput('계약번호,계약번호\nA,B','contract'),/REVIEW_INVALID_HEADERS/);
});
test('CSV output quotes formula values and rejects invalid request kind',()=>{
 const rows=[{request_no:'=HYPERLINK("https://bad.example")',agency:'-malicious',amount:'100',item_name:'LED'}];
 const csv=reviewCsv(rows,'contract');
 assert.ok(csv.startsWith('\uFEFF'));
 assert.match(csv,/"'=HYPERLINK/);
 assert.match(csv,/"'-malicious"/);
 assert.match(csv,/"100"/);
 assert.throws(()=>reviewCsv([],'secret'),/REVIEW_INVALID_KIND/);
});
test('local file review is a distinct route, read-only and never performs implicit provider calls',()=>{
 const page=readFileSync(new URL('../app/contract-review/page.tsx',import.meta.url),'utf8');
 assert.match(page,/parseReviewInput/);
 assert.match(page,/\/api\/server-sync/);
 assert.match(page,/\/api\/automation/);
 assert.match(page,/\/api\/deliveries/);
 assert.match(page,/\/api\/budget/);
 assert.doesNotMatch(page,/method:\s*['"]POST['"]/);
 assert.match(page,/임시/);
});
