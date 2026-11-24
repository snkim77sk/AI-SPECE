import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  ORIGINAL_NAV_ITEMS, dashboardMode, originalNavHref, originalNavActive,
  exactCount, formatDashboardCount, dashboardMetrics, dashboardAwaitingInitialCollection, fetchDashboard, dashboardErrorMessage,
} from '../lib/dashboard-display.mjs';

const data = (extra = {}) => ({
  mode: 'live',
  delivery: {
    active_source_rows: '9', stored_source_rows: '12', inactive_source_rows: '3',
    latest_rows: '5', requests: '2', smart_control_rows: '1',
  },
  budgets: [
    { service: 'QWGJK', fiscal_year: 2026, active_rows: '7', target_rows: '4' },
    { service: 'QWGJK', fiscal_year: 2025, active_rows: '6', target_rows: '3' },
    { service: 'AIDFA', fiscal_year: 2026, active_rows: '8', target_rows: '500' },
  ],
  ...extra,
});
const response = (payload, ok = true) => ({ ok, json: async () => payload });

test('all eight original menu labels and exact target paths remain in order', () => {
  assert.deepEqual(ORIGINAL_NAV_ITEMS.map(item => [item.label, item.href]), [
    ['대시보드', '/dashboard'], ['예산·영업후보', '/budget'],
    ['LED 조명', '/shopping?category=LIGHTING'], ['등주', '/shopping?category=POLE'],
    ['스마트LED', '/smartled'], ['업체·단가 분석', '/vendors'],
    ['수집 상태', '/collection-monitor'], ['설정', '/settings'],
  ]);
});

test('mode is live unless synthetic is explicitly selected, and links retain category and mode', () => {
  for (const search of ['', '?mode=live', '?mode=invalid', '?q=synthetic']) assert.equal(dashboardMode(search), 'live');
  assert.equal(dashboardMode('?mode=synthetic'), 'live');
  assert.equal(originalNavHref('/shopping?category=POLE', 'synthetic'), '/shopping?category=POLE&mode=live');
  assert.equal(originalNavHref('/budget/history'), '/budget/history?mode=live');
  assert.equal(originalNavHref('/dashboard', 'invalid'), '/dashboard?mode=live');
});

test('selected navigation follows routes, submenus, category changes, and legacy root tabs', () => {
  const selected = (path, search = '') => ORIGINAL_NAV_ITEMS.filter(item => originalNavActive(item.href, path, search)).map(item => item.label);
  assert.deepEqual(selected('/'), ['대시보드']);
  assert.deepEqual(selected('/dashboard', '?mode=synthetic'), ['대시보드']);
  assert.deepEqual(selected('/shopping', '?category=LIGHTING'), ['LED 조명']);
  assert.deepEqual(selected('/shopping', '?category=POLE'), ['등주']);
  assert.deepEqual(selected('/shopping', ''), ['LED 조명']);
  assert.deepEqual(selected('/budget/history'), ['예산·영업후보']);
  assert.deepEqual(selected('/budget/project'), ['예산·영업후보']);
  assert.deepEqual(selected('/budget/observations'), ['예산·영업후보']);
  assert.deepEqual(selected('/settings'), ['설정']);
  assert.deepEqual(selected('/collection-monitor'), ['수집 상태']);
  assert.deepEqual(selected('/', '?tab=vendors'), ['업체·단가 분석']);
  assert.deepEqual(selected('/', '?tab=agencies'), ['업체·단가 분석']);
  assert.deepEqual(selected('/', '?tab=smart'), ['스마트LED']);
  assert.deepEqual(selected('/', '?tab=scope'), ['설정']);
  assert.deepEqual(selected('/', '?tab=collect'), ['설정']);
});

test('counts distinguish zero, pending, missing and invalid values without rounding', () => {
  assert.equal(formatDashboardCount('0'), '0');
  assert.equal(formatDashboardCount(null), '미확인');
  assert.equal(formatDashboardCount(undefined, true), '…');
  assert.equal(formatDashboardCount('90071992547409931234'), '90,071,992,547,409,931,234');
  for (const value of [NaN, Infinity, -1, 1.5, 'NaN', '1e3', '', false, {}, '01', Number.MAX_SAFE_INTEGER + 1]) {
    assert.equal(exactCount(value), null);
  }
});

test('dashboard metrics preserve source units and never add requests, controls, or revisions twice', () => {
  const metrics = dashboardMetrics(data());
  assert.deepEqual(metrics.map(item => item.value), ['30', '5', '12', '3', '7']);
  assert.match(metrics[0].note, /활성 원천행/);
  assert.match(metrics[1].note, /최신.*품목/);
  assert.match(metrics[4].label, /양수 잔액/);
  assert.match(metrics[4].note, /AIDFA 제외/);
});

test('unavailable aggregates stay unknown; an explicitly empty source is zero', () => {
  assert.ok(dashboardMetrics(null).every(item => item.value === null));
  assert.equal(dashboardMetrics({ delivery: { active_source_rows: '0' } })[0].value, null);
  const empty = dashboardMetrics({ delivery: { active_source_rows: '0', latest_rows: '0', stored_source_rows: '0', inactive_source_rows: '0' }, budgets: [] });
  assert.ok(empty.every(item => item.value === '0'));
  assert.equal(dashboardMetrics(data({ budgets: [{ service: 'QWGJK', active_rows: null, target_rows: null }] }))[0].value, null);
  assert.equal(dashboardMetrics(data({ budgets: [{ service: 'QWGJK', active_rows: null, target_rows: null }] }))[4].value, null);
});

test('aggregate sums remain exact beyond the JS safe integer range', () => {
  const metrics = dashboardMetrics(data({ delivery: { active_source_rows: '90071992547409931234' }, budgets: [{ service: 'QWGJK', active_rows: '2', target_rows: '90071992547409931234' }, { service: 'QWGJK', active_rows: '1', target_rows: '9' }] }));
  assert.equal(metrics[0].value, '90071992547409931237');
  assert.equal(metrics[4].value, '90071992547409931243');
});

test('fetch reads only the local dashboard endpoint and rejects mode leakage', async () => {
  const calls = [];
  const result = await fetchDashboard(async (url, options) => { calls.push({ url, options }); return response(data()); }, 'live', new AbortController().signal);
  assert.equal(result.mode, 'live');
  assert.equal(calls[0].url, '/api/dashboard?mode=live&progressive=1');
  assert.equal(calls[0].options.cache, 'no-store');
  assert.equal(calls[0].options.credentials, 'same-origin');
  await assert.rejects(fetchDashboard(async () => response(data({ mode: 'synthetic' })), 'live', new AbortController().signal), /DASHBOARD_INVALID_RESPONSE/);
});

test('a stalled request or JSON body is bounded, and its fetch is cancelled', async () => {
  let signal;
  await assert.rejects(fetchDashboard((_url, options) => { signal = options.signal; return new Promise(() => {}); }, 'live', new AbortController().signal, 10), /DASHBOARD_TIMEOUT/);
  assert.equal(signal.aborted, true);
  await assert.rejects(fetchDashboard(async () => ({ ok: true, json: () => new Promise(() => {}) }), 'live', new AbortController().signal, 10), /DASHBOARD_TIMEOUT/);
});

test('unmount/navigation cancellation suppresses obsolete results and pre-aborted reads never start', async () => {
  const abort = new AbortController();
  let resolve;
  const request = fetchDashboard(() => new Promise(done => { resolve = done; }), 'live', abort.signal);
  abort.abort();
  await assert.rejects(request, { name: 'AbortError' });
  resolve(response(data()));
  const old = new AbortController(); old.abort();
  await assert.rejects(fetchDashboard(() => { throw Error('MUST_NOT_FETCH'); }, 'live', old.signal), { name: 'AbortError' });
});

test('read failures do not produce zeros and a later refresh can recover', async () => {
  const abort = new AbortController();
  await assert.rejects(fetchDashboard(async () => response({ error: 'DASHBOARD_SOURCE_CHANGED_RETRY' }, false), 'live', abort.signal), /SOURCE_CHANGED_RETRY/);
  const fresh = await fetchDashboard(async () => response(data()), 'live', abort.signal);
  assert.equal(fresh.delivery.latest_rows, '5');
  assert.match(dashboardErrorMessage(new Error('DASHBOARD_TIMEOUT')), /15초/);
  assert.match(dashboardErrorMessage(new Error('DASHBOARD_SOURCE_CHANGED_RETRY')), /새로고침/);
  assert.doesNotMatch(dashboardErrorMessage(new Error('secret source error')), /secret/);
});

test('dashboard has original card order, bounded fetch, refresh, collapsed details and history support', () => {
  const page = readFileSync(new URL('../app/dashboard/page.tsx', import.meta.url), 'utf8');
  assert.ok(page.indexOf('G2B vNext 대시보드') < page.indexOf('저장자료 주요 현황'));
  assert.ok(page.indexOf('운영 버전') < page.indexOf('배포 HEAD'));
  assert.ok(page.indexOf('배포 HEAD') < page.indexOf('저장소 조회'));
  assert.ok(page.indexOf('dashboardMetrics(data).map') < page.indexOf('수집 준비상태'));
  assert.match(page, /<details className="original-card original-detail">/);
  assert.match(page, /fetchDashboard\(\(url:string,options:RequestInit\)=>fetch\(viewerHref\(url\),options\), mode, abort.signal\)/);
  assert.match(page, /addEventListener\('popstate'/);
  assert.match(page, /history.pushState/);
  assert.match(page, /g2b:mode-change/);
  assert.match(page, /전체 현황 새로고침/);
  assert.doesNotMatch(page, /4\.1\.307|로그아웃|Cafe24 통합 운영/);
});


test('initial collection notice requires known empty live data and both keys explicitly absent', () => {
  const empty = { mode: 'live', delivery: { stored_source_rows: '0', active_source_rows: '0', inactive_source_rows: '0', latest_rows: '0' }, budgets: [], readiness: { g2b: { hasKey: false }, lofin: { hasKey: false } } };
  assert.equal(dashboardAwaitingInitialCollection(empty), true);
  assert.equal(dashboardAwaitingInitialCollection(null), false);
  assert.equal(dashboardAwaitingInitialCollection({ ...empty, mode: 'synthetic' }), false);
  assert.equal(dashboardAwaitingInitialCollection({ ...empty, readiness: undefined }), false);
  assert.equal(dashboardAwaitingInitialCollection({ ...empty, readiness: { g2b: { hasKey: true }, lofin: { hasKey: false } } }), false);
  assert.equal(dashboardAwaitingInitialCollection({ ...empty, readiness: { g2b: { hasKey: false }, lofin: { hasKey: true } } }), false);
  assert.equal(dashboardAwaitingInitialCollection({ ...empty, budgets: [{ service: 'AIDFA', stored_rows: '1', active_rows: '0', inactive_rows: '1' }] }), false);
  assert.equal(dashboardAwaitingInitialCollection({ ...empty, delivery: { ...empty.delivery, stored_source_rows: '2', inactive_source_rows: '2' } }), false);
  assert.equal(dashboardAwaitingInitialCollection({ ...empty, delivery: { ...empty.delivery, latest_rows: null } }), false);
});

test('progressive dashboard completes pending stages without returning partial data',async()=>{let calls=0;const r=await fetchDashboard(async()=>++calls<3?{ok:true,status:202,json:async()=>({error:'DASHBOARD_AGGREGATE_PENDING'})}:response(data()),'live',new AbortController().signal);assert.equal(calls,3);assert.equal(r.mode,'live');});
test('progressive dashboard has a bounded pending loop',async()=>{let calls=0;await assert.rejects(fetchDashboard(async()=>{calls++;return {ok:true,status:202,json:async()=>({error:'DASHBOARD_AGGREGATE_PENDING'})};},'live',new AbortController().signal),/DASHBOARD_PROGRESS_LIMIT/);assert.equal(calls,18);});

test('transient overload retries once then returns complete dashboard',async()=>{let calls=0;const r=await fetchDashboard(async()=>++calls===1?{ok:false,status:503,json:async()=>({error:'DATABASE_OVERLOADED'})}:response(data()),'live',new AbortController().signal);assert.equal(calls,2);assert.equal(r.mode,'live');});
test('schema errors are never retried',async()=>{let calls=0;await assert.rejects(fetchDashboard(async()=>{calls++;return {ok:false,status:503,json:async()=>({error:'DATABASE_SCHEMA'})};},'live',new AbortController().signal),/DATABASE_SCHEMA/);assert.equal(calls,1);});

test('source change restarts before displaying a complete snapshot',async()=>{let calls=0;const r=await fetchDashboard(async()=>++calls===1?{ok:false,status:409,json:async()=>({error:'DASHBOARD_SOURCE_CHANGED_RETRY'})}:response(data()),'live',new AbortController().signal);assert.equal(calls,2);assert.equal(r.mode,'live');});
