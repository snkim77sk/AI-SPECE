import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {STAGES,FEEDBACK_TYPES,PROJECT_REPOSITORY,validateFeedback,feedbackIssueUrl} from '../lib/owner-progress.mjs';
import {accessDecision} from '../lib/public-auth-policy.mjs';
import {ORIGINAL_NAV_ITEMS,OWNER_NAV_ITEM} from '../lib/dashboard-display.mjs';
test('owner-only progress route blocks visitors and ordinary approved members',()=>{
 const admin={status:'approved',role:'admin'},member={status:'approved',role:'member'};
 for(const path of ['/owner-progress','/owner-progress/more']){
  assert.equal(accessDecision(path,'GET',null),'unauthorized');
  assert.equal(accessDecision(path,'GET',member),'forbidden');
  assert.equal(accessDecision(path,'GET',{status:'pending',role:'admin'}),'forbidden');
  assert.equal(accessDecision(path,'GET',admin),'allow');
 }
});
test('progress stages never claim latest GitHub commit or deployment is runtime verified',()=>{
 assert.ok(STAGES.some(s=>s.id==='deploy'&&s.status==='unverified'));
 assert.ok(STAGES.some(s=>s.id==='publish'&&s.status==='blocked'));
 assert.ok(STAGES.every(s=>typeof s.name==='string'&&s.name.length>0));
});
test('feedback composes a GitHub issue draft, not a silent post',()=>{
 const url=new URL(feedbackIssueUrl({type:'ui',title:'화면 필터 개선',detail:'수집 진행 현황에서 미수신 건수를 더 선명하게 보여 주세요.'}));
 assert.equal(url.origin,'https://github.com');
 assert.equal(url.pathname,'/snkim77sk/AI-SPECE/issues/new');
 assert.match(url.searchParams.get('title'),/G2B 화면 개선/);
 assert.match(url.searchParams.get('body'),/미수신 건수/);
 assert.match(url.searchParams.get('body'),/NO1 보존/);
 assert.equal(FEEDBACK_TYPES.length,5);
});
test('feedback rejects empty, invalid, and oversized changes',()=>{
 for(const sample of [
  {type:'other',title:'화면 개선',detail:'수정할 내용 여기에 상세하게 기록'},
  {type:'ui',title:'x',detail:'수정할 내용 여기에 상세하게 기록'},
  {type:'ui',title:'화면 개선',detail:'짧음'},
  {type:'ui',title:'화면 개선',detail:'x'.repeat(2401)},
 ])assert.equal(validateFeedback(sample).ok,false);
 assert.equal(validateFeedback({type:'data',title:'자료 누락 검토',detail:'어제 계약자료를 다시 조회하고 최신 수신시각을 검증해 주세요.'}).ok,true);
});
test('owner page reads receipts without any direct write or provider API calls',()=>{
 const page=readFileSync(new URL('../app/owner-progress/page.tsx',import.meta.url),'utf8');
 assert.match(page,/fetch\('\/api\/server-sync'/);
 assert.match(page,/GitHub에서 최종 등록/);
 assert.doesNotMatch(page,/method:\s*['"]POST['"]/);
 assert.doesNotMatch(page,/localStorage|sessionStorage|G2B_SERVICE_KEY|LOFIN_API_KEY/);
 assert.match(PROJECT_REPOSITORY,/github\.com/);
});

test('owner navigation is supplemental, leaving the eight existing links in order',()=>{
 assert.equal(ORIGINAL_NAV_ITEMS.length,8);
 assert.equal(OWNER_NAV_ITEM.href,'/owner-progress');
 const shell=readFileSync(new URL('../components/original-shell.tsx',import.meta.url),'utf8');
 assert.match(shell,/adminVisible\?\[\.\.\.ORIGINAL_NAV_ITEMS,OWNER_NAV_ITEM\]/);
});
