import test from 'node:test';
import assert from 'node:assert/strict';
import { organizationPatterns } from '../lib/institution-patterns.mjs';
import { scoreFutureBudgetEvidence, enrichFutureBudgetRows, futureRowSignals } from '../lib/future-budget-evidence.mjs';
const row = (over = {}) => ({ fiscal_year: 2027, org_name: '인천광역시 옹진군', region_name: '인천광역시',
  primary_category: 'LIGHTING', project_name: '북도면 LED 가로등', budget_amount: '1000', source_layer: 'DETAIL_EXECUTION', ...over });
function makePattern({ total = 10, high = 5, candidate = 1, year = 2026, complete = true, category = 'LIGHTING' } = {}) {
  return organizationPatterns({
    projects: Array.from({ length: total }, (_, i) => ({ analysis_id: 'run', fiscal_year: year, source_key: 'P' + i,
      org_name: '인천광역시 옹진군', region_name: '인천광역시', primary_category: category, budget_amount: '100' })),
    evidence: Array.from({ length: high + candidate }, (_, i) => ({ analysis_id: 'run', budget_project_identity: 'P' + i,
      shopping_delivery_req_no: 'R' + i, shopping_source_key: 'REQUEST:R' + i, shopping_category: category,
      shopping_amount: '60', score: i < high ? 90 : 70, level: i < high ? 'HIGH' : 'CANDIDATE',
      shared_signals: ['LED', 'STREET_LIGHT'], lag_days: 10 })),
    coverage: complete ? [{ analysis_id: 'run', fiscal_year: year, org_name: '인천광역시 옹진군', region_name: '인천광역시',
      category, status: 'complete', source_population_verified: true, analysis_complete: true,
      population_scope: 'FULL_FISCAL_YEAR_ORG_CATEGORY', expected_project_count: total, expected_match_count: high + candidate,
      evidence_ref: 'fixture:verified-source' }] : [], fiscalYears: [year], categories: [category],
  })[0];
}

test('original compatible fixture weights equal 95 and structural cap is 75', () => {
  const p = makePattern(), r = row(), before = JSON.stringify([r, p]);
  const scored = scoreFutureBudgetEvidence(r, p);
  assert.equal(scored.historical_evidence_score, 95); assert.equal(scored.historical_evidence_level, 'STRONG_HISTORY');
  assert.deepEqual(scored.historical_evidence_reasons, ['HISTORICAL_ORG_MATCH', 'HISTORICAL_CATEGORY_MATCH',
    'HISTORICAL_SAMPLE_5_PLUS', 'HISTORICAL_HIGH_MATCH_RATE_50_PLUS', 'REPEATED_SIGNAL:LED,STREET_LIGHT']);
  assert.equal(scored.historical_high_match_project_rate, 0.5); assert.equal(scored.historical_total_budget_projects, 10);
  assert.equal(scored.historical_actual_shopping_amount, '300'); assert.equal(scored.confirmed_procurement, false);
  const structural = scoreFutureBudgetEvidence(row({ source_layer: 'APPROPRIATION' }), p);
  assert.equal(structural.historical_evidence_score, 75); assert.equal(structural.structural_budget_only, true);
  assert.ok(structural.historical_evidence_reasons.includes('AIDFA_STRUCTURAL_CAP'));
  assert.equal(scoreFutureBudgetEvidence(row({ service: 'AIDFA' }), p).historical_evidence_score, 75);
  assert.equal(JSON.stringify([r, p]), before);
});
test('original sample and rate thresholds: 1, 3, 5; 10%, 25%, 50%', () => {
  const basicRow = row({ project_name: '사업' });
  for (const [args, expected] of [[{ high: 1, total: 20 }, 53], [{ high: 1, total: 10 }, 61],
    [{ high: 3, total: 10 }, 74], [{ high: 5, total: 10 }, 85], [{ high: 5, total: 20 }, 80]]) {
    assert.equal(scoreFutureBudgetEvidence(basicRow, makePattern(args)).historical_evidence_score, expected);
  }
});
test('unverified matches keep sample but suppress denominator/rate and complete-year bonuses', () => {
  const r = scoreFutureBudgetEvidence(row(), makePattern({ complete: false }));
  assert.equal(r.historical_evidence_score, 85); assert.equal(r.historical_population_complete, false);
  assert.equal(r.historical_total_budget_projects, null); assert.equal(r.historical_high_match_project_rate, null);
  assert.deepEqual(r.historical_population_complete_years, []);
  assert.ok(r.historical_evidence_reasons.includes('PARTIAL_YEAR_EVIDENCE_NO_MULTI_YEAR_BONUS'));
});
test('multi-year verified population bonus and 100 cap match original; evidence-only year adds no bonus', () => {
  const p = makePattern();
  const multi = { ...p, evidence_years: [2026, 2027], population_complete_years: [2026, 2027],
    scope: { ...p.scope, fiscal_years: [2026, 2027] },
    verified_population_scopes: [...p.verified_population_scopes, { ...p.verified_population_scopes[0], fiscal_year: 2027, analysis_id: 'run-2027' }] };
  assert.equal(scoreFutureBudgetEvidence(row({ fiscal_year: 2028 }), multi).historical_evidence_score, 100);
  const partial = { ...multi, population_complete: false, population_status: 'INCOMPLETE_OR_UNVERIFIED', population_complete_years: [2026] };
  const r = scoreFutureBudgetEvidence(row({ fiscal_year: 2028 }), partial);
  assert.equal(r.historical_evidence_score, 85); assert.deepEqual(r.historical_evidence_only_years, [2027]);
});
test('2026 has explicit missing prior-year evidence, never automatic 2025 expansion', () => {
  const p = makePattern();
  const r = scoreFutureBudgetEvidence(row({ fiscal_year: 2026 }), p);
  assert.equal(r.historical_evidence_score, 0); assert.equal(r.historical_total_budget_projects, null);
  assert.ok(r.historical_evidence_reasons.includes('NO_PRE_TARGET_HISTORY_WITHIN_SOURCE_START'));
  assert.equal(scoreFutureBudgetEvidence(row(), { ...p, evidence_years: [2025] }).historical_evidence_score, 0);
  assert.equal(scoreFutureBudgetEvidence(row({ fiscal_year: 0 }), p).historical_evidence_score, 0);
});
test('target year or future history invalidates entire aggregated pattern instead of leaking scores', () => {
  const p = makePattern();
  for (const years of [[2027], [2026, 2027], [2028]]) {
    assert.equal(scoreFutureBudgetEvidence(row(), { ...p, evidence_years: years }).historical_evidence_score, 0);
  }
  assert.equal(scoreFutureBudgetEvidence(row(), { ...p, scope: { ...p.scope, fiscal_years: [2026, 2027] } }).historical_evidence_score, 0);
});
test('no history/outside scope cannot claim zero known shopping or known zero project denominator', () => {
  for (const r of [scoreFutureBudgetEvidence(row()), scoreFutureBudgetEvidence(row({ primary_category: 'OTHER' }), makePattern())]) {
    assert.equal(r.historical_actual_shopping_amount, null); assert.equal(r.historical_total_budget_projects, null);
    assert.equal(r.historical_shopping_to_budget_amount_ratio, null); assert.equal(r.confirmed_procurement, false);
  }
  assert.equal(scoreFutureBudgetEvidence(row(), makePattern({ high: 0, candidate: 0 })).historical_evidence_level, 'NO_HISTORY');
});
test('category-specific counts required; unrelated category or institution never learns association', () => {
  const p = makePattern();
  assert.equal(scoreFutureBudgetEvidence(row(), makePattern({ category: 'POLE' })).historical_evidence_score, 0);
  assert.equal(scoreFutureBudgetEvidence(row({ org_name: '인천광역시 중구' }), p).historical_evidence_score, 0);
  assert.equal(scoreFutureBudgetEvidence(row({ region_name: '부산광역시' }), p).historical_evidence_score, 0);
  assert.equal(scoreFutureBudgetEvidence(row({ region_name: '' }), p).historical_evidence_score, 0);
  assert.equal(scoreFutureBudgetEvidence(row(), { ...p, scope: { ...p.scope, categories: ['LIGHTING', 'POLE'] } }).historical_evidence_score, 0);
});
test('supported region-scoped source aliases match; ambiguous aliases do not pick larger spend', () => {
  const p = makePattern();
  assert.equal(enrichFutureBudgetRows([row({ org_name: '인천옹진군' })], { patterns: [p] })[0].historical_evidence_score, 95);
  const alias = { ...p, org_name: '옹진군', actual_shopping_amount: '9007199254740995' };
  const r = enrichFutureBudgetRows([row()], { patterns: [p, alias] })[0];
  assert.equal(r.historical_evidence_score, 0); assert.equal(r.historical_pattern_match_basis, 'AMBIGUOUS_ORGANIZATION_PATTERN');
});
test('unsupported transition labels and retired org aliases do not imply a learned successor', () => {
  const p = makePattern();
  assert.equal(scoreFutureBudgetEvidence(row(), p, { patternMatchBasis: 'LINEAGE:UNVERIFIED' }).historical_evidence_score, 0);
  assert.equal(scoreFutureBudgetEvidence(row(), { ...p, organization_lineage_applied: true }).historical_evidence_score, 0);
  for (const org_name of ['인천광역시 중구', '인천광역시 서구', '인천광역시 동구'])
    assert.equal(scoreFutureBudgetEvidence(row({ org_name }), { ...p, org_name }).historical_evidence_score, 0);
  const hwaseong = { ...p, org_name: '경기도 화성시', region_name: '경기도' };
  assert.equal(scoreFutureBudgetEvidence(row({ org_name: '경기도 화성시', region_name: '경기도' }), hwaseong).historical_evidence_score, 95);
});
test('amounts stay exact and missing; sort uses BigInt with stable tie key', () => {
  const p = { ...makePattern(), actual_shopping_amount: '9007199254740993' };
  assert.equal(scoreFutureBudgetEvidence(row(), p).historical_actual_shopping_amount, '9007199254740993');
  assert.equal(scoreFutureBudgetEvidence(row(), { ...p, actual_shopping_amount: null }).historical_actual_shopping_amount, null);
  const rows = [row({ source_key: 'a', budget_amount: '9007199254740992' }), row({ source_key: 'b', budget_amount: '9007199254740993' }),
    row({ source_key: 'c', budget_amount: '0', appropriation_amount: '9999999999999999' })];
  assert.deepEqual(enrichFutureBudgetRows(rows, { patterns: [p], limit: 2 }).map(r => r.source_key), ['b', 'a']);
  assert.deepEqual(enrichFutureBudgetRows(rows, { patterns: [p] }), enrichFutureBudgetRows([...rows].reverse(), { patterns: [p] }));
  assert.throws(() => scoreFutureBudgetEvidence(row(), { ...p, actual_shopping_amount: 9007199254740992 }), /UNSAFE_INTEGER/);
});
test('signal terms reproduce original extraction, zero-count signals do not provide evidence', () => {
  assert.deepEqual(futureRowSignals(row({ project_name: '엘이디 도로 조명 보안등 경관조명', account_name: '산책로 조명 투광등 태양광등 가로등주' })),
    ['FLOOD_LIGHT', 'LANDSCAPE_LIGHT', 'LED', 'PATH_LIGHT', 'POLE', 'SECURITY_LIGHT', 'SOLAR_LIGHT', 'STREET_LIGHT']);
  assert.deepEqual(scoreFutureBudgetEvidence(row(), { ...makePattern(), signal_counts: { LED: 0, STREET_LIGHT: 0 } }).historical_shared_signals, []);
});
test('bounded building blocks fail before copying an unbounded input', () => {
  assert.throws(() => organizationPatterns({ projects: Array(20001).fill({}) }), /PATTERN_INPUT_ROW_LIMIT/);
  assert.throws(() => enrichFutureBudgetRows(Array(1001).fill(row()), { patterns: [] }), /FUTURE_EVIDENCE_INPUT_ROW_LIMIT/);
  assert.throws(() => enrichFutureBudgetRows([], { patterns: Array(1001).fill({}) }), /FUTURE_EVIDENCE_INPUT_ROW_LIMIT/);
});

test('population booleans and year lists alone cannot activate rate or multi-year bonuses', () => {
  const p = makePattern(); const r = scoreFutureBudgetEvidence(row(), { ...p, verified_population_scopes: [] });
  assert.equal(r.historical_population_complete, false); assert.equal(r.historical_high_match_project_rate, null);
  assert.deepEqual(r.historical_population_complete_years, []); assert.equal(r.historical_evidence_score, 85);
});

test('verified coverage-only years preserve denominators and bonuses without fabricating observations', () => {
  const p = makePattern();
  const empty = { ...p.verified_population_scopes[0], fiscal_year: 2027, analysis_id: 'run-2027', expected_project_count: 0, expected_match_count: 0 };
  const mixed = { ...p, scope: { ...p.scope, fiscal_years: [2026, 2027] }, population_complete_years: [2026, 2027],
    verified_population_scopes: [...p.verified_population_scopes, empty] };
  const r = scoreFutureBudgetEvidence(row({ fiscal_year: 2028 }), mixed);
  assert.equal(r.historical_population_complete, true); assert.equal(r.historical_total_budget_projects, 10);
  assert.equal(r.historical_evidence_score, 100); assert.deepEqual(r.historical_evidence_years, [2026]);
  assert.deepEqual(r.historical_population_complete_years, [2026, 2027]);
  assert.deepEqual(r.historical_verified_empty_population_years, [2027]);
  for (const scopes of [p.verified_population_scopes, [...p.verified_population_scopes, { ...empty, expected_project_count: 1 }]]) {
    const bad = scoreFutureBudgetEvidence(row({ fiscal_year: 2028 }), { ...mixed, verified_population_scopes: scopes });
    assert.equal(bad.historical_population_complete, false); assert.equal(bad.historical_total_budget_projects, null);
    assert.equal(bad.historical_evidence_score, 85); assert.deepEqual(bad.historical_verified_empty_population_years, []);
  }
});

test('all verified empty history is known zero with no evidence score; unverified emptiness stays unknown', () => {
  const coverage = y => ({ analysis_id: `run-${y}`, fiscal_year: y, org_name: '인천광역시 옹진군', region_name: '인천광역시',
    category: 'LIGHTING', status: 'complete', source_population_verified: true, analysis_complete: true,
    population_scope: 'FULL_FISCAL_YEAR_ORG_CATEGORY', expected_project_count: 0, expected_match_count: 0, evidence_ref: 'fixture:empty' });
  const [zero] = organizationPatterns({ coverage: [coverage(2026), coverage(2027)], fiscalYears: [2026, 2027] });
  const r = scoreFutureBudgetEvidence(row({ fiscal_year: 2028 }), zero);
  assert.equal(r.historical_evidence_score, 0); assert.equal(r.historical_evidence_level, 'NO_HISTORY');
  assert.equal(r.historical_population_complete, true); assert.equal(r.historical_total_budget_projects, 0);
  assert.equal(r.historical_actual_shopping_amount, '0'); assert.equal(r.historical_high_match_project_rate, null);
  assert.deepEqual(r.historical_evidence_years, []); assert.deepEqual(r.historical_verified_empty_population_years, [2026, 2027]);
  assert.ok(!r.historical_evidence_reasons.includes('MULTI_YEAR_VERIFIED_POPULATION'));
  const unknown = scoreFutureBudgetEvidence(row({ fiscal_year: 2028 }), { ...zero, verified_population_scopes: [] });
  assert.equal(unknown.historical_population_complete, false); assert.equal(unknown.historical_total_budget_projects, null);
  assert.equal(unknown.historical_actual_shopping_amount, null);
});

test('indexed aliases preserve ambiguity, regional rejection and supported alias results at maximum rows', () => {
  const patterns = Array.from({ length: 1000 }, (_, i) => ({ category: 'LIGHTING', org_name: `서울특별시 기관${i}`, region_name: '서울특별시' }));
  const rows = Array.from({ length: 1000 }, (_, i) => row({ org_name: `서울특별시 다른기관${i}`, region_name: '서울특별시', source_key: `P${i}` }));
  const results = enrichFutureBudgetRows(rows, { patterns, limit: 1 });
  assert.equal(results.length, 1); assert.equal(results[0].historical_evidence_score, 0);
  const p = makePattern();
  assert.equal(enrichFutureBudgetRows([row()], { patterns: [p, { ...p }] })[0].historical_pattern_match_basis, 'AMBIGUOUS_ORGANIZATION_PATTERN');
  assert.equal(enrichFutureBudgetRows([row({ org_name: '부산광역시 옹진군' })], { patterns: [p] })[0].historical_evidence_score, 0);
});

test('future guards bound logical input and repeated output metadata even with a small result limit', () => {
  const p = makePattern(), name = 'x'.repeat(65536);
  assert.throws(() => enrichFutureBudgetRows(Array.from({ length: 130 }, () => row({ metadata: name })), { patterns: [] }), /FUTURE_EVIDENCE_INPUT_BYTE_LIMIT/);
  const repeated = { ...p, historical_org_names: Array(16).fill(name) };
  assert.throws(() => enrichFutureBudgetRows(Array.from({ length: 10 }, () => row()), { patterns: [repeated], limit: 1 }), /FUTURE_EVIDENCE_OUTPUT_BYTE_LIMIT/);
  assert.throws(() => scoreFutureBudgetEvidence(row(), { ...p, historical_org_names: Array(257).fill('x') }), /FUTURE_EVIDENCE_INPUT_LIST_LIMIT/);
});
