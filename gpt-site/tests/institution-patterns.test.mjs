import test from 'node:test';
import assert from 'node:assert/strict';
import { organizationPatterns, patternHistoryYears, exactRatio, normalizePatternProject } from '../lib/institution-patterns.mjs';
const project = (id, over = {}) => ({ analysis_id: 'run-2026', fiscal_year: 2026, source_key: id,
  org_name: '인천광역시 옹진군', region_name: '인천광역시', primary_category: 'LIGHTING',
  project_name: '북도면 LED 가로등', budget_amount: '100', snapshot_date: '2026-03-01', ...over });
const evidence = (id, over = {}) => ({ analysis_id: 'run-2026', budget_project_identity: id,
  shopping_delivery_req_no: 'REQ-' + id, shopping_source_key: 'REQUEST:REQ-' + id,
  shopping_date: '2026-05-01', shopping_category: 'LIGHTING', shopping_amount: '60',
  score: 90, level: 'HIGH', lag_days: 10, shared_signals: ['LED', 'STREET_LIGHT'], shared_tokens: ['loc:북도'], ...over });
const coverage = (over = {}) => ({ analysis_id: 'run-2026', org_name: '인천광역시 옹진군', region_name: '인천광역시',
  fiscal_year: 2026, category: 'LIGHTING', status: 'complete', source_population_verified: true, analysis_complete: true,
  population_scope: 'FULL_FISCAL_YEAR_ORG_CATEGORY', expected_project_count: 10, expected_match_count: 6,
  evidence_ref: 'fixture:complete-source-population', ...over });
function fixture(over = {}) { return { projects: Array.from({ length: 10 }, (_, i) => project('P' + i)),
  evidence: Array.from({ length: 6 }, (_, i) => evidence('P' + i, i === 5 ? { score: 70, level: 'CANDIDATE' } : {})),
  coverage: [coverage()], fiscalYears: [2026], categories: ['LIGHTING'], ...over }; }

test('verified single-category fixture matches original Python formulas and is JSON safe', () => {
  const input = fixture(), before = JSON.stringify(input), [p] = organizationPatterns(input);
  assert.equal(p.population_complete, true); assert.equal(p.historical_budget_projects, 10);
  assert.equal(p.historical_budget_amount, '1000'); assert.equal(p.high_matched_budget_projects, 5);
  assert.equal(p.matched_budget_projects, 6); assert.equal(p.candidate_matched_budget_projects, 1);
  assert.equal(p.high_match_project_rate, 0.5); assert.equal(p.matched_project_rate, 0.6);
  assert.equal(p.matched_budget_amount, '500'); assert.equal(p.actual_shopping_amount, '300');
  assert.equal(p.shopping_to_budget_amount_ratio, 0.6); assert.equal(p.average_nonnegative_lag_days, 10);
  assert.deepEqual(p.signal_counts, { LED: 5, STREET_LIGHT: 5 });
  assert.equal(JSON.stringify(input), before); assert.doesNotThrow(() => JSON.stringify(p));
});
test('matches, project rows, and complete analysis job cannot prove source denominator', () => {
  for (const input of [fixture({ coverage: [] }), fixture({ coverage: [coverage({ source_population_verified: false })] }),
    fixture({ coverage: [{ ...coverage(), source_population_verified: undefined, state: 'complete' }] })]) {
    const [p] = organizationPatterns(input);
    assert.equal(p.population_complete, false); assert.equal(p.historical_budget_projects, null);
    assert.equal(p.historical_budget_amount, null); assert.equal(p.high_match_project_rate, null);
    assert.equal(p.matched_project_rate, null); assert.equal(p.observed_budget_projects, 10);
    assert.equal(p.high_matched_budget_projects, 5); assert.equal(p.actual_shopping_amount, '300');
  }
});
test('truncated projects, truncated matches, wrong scope, duplicate manifests and changed run invalidate denominator', () => {
  for (const input of [fixture({ projects: [project('P0')] }), fixture({ evidence: [evidence('P0')] }),
    fixture({ coverage: [coverage({ population_scope: 'COLLECTED_ONLY' })] }),
    fixture({ coverage: [coverage({ analysis_id: 'other' })] }), fixture({ coverage: [coverage(), coverage()] }),
    fixture({ coverage: [coverage({ expected_project_count: '10' })] }), fixture({ coverage: [coverage({ evidence_ref: '' })] })])
    assert.equal(organizationPatterns(input)[0].population_complete, false);
});
test('mixed complete/missing years stay incomplete; no subset denominator or false multi-year coverage', () => {
  const [p] = organizationPatterns(fixture({ fiscalYears: [2026, 2027] }));
  assert.equal(p.population_complete, false); assert.equal(p.high_match_project_rate, null);
  assert.deepEqual(p.population_complete_years, [2026]); assert.deepEqual(p.missing_population_years, [2027]);
});
test('category, organization and canonical region remain separate scopes', () => {
  const extra = [project('pole', { primary_category: 'POLE' }), project('busan', { org_name: '부산광역시 중구', region_name: '부산' })];
  const all = organizationPatterns({ ...fixture(), projects: [...fixture().projects, ...extra],
    evidence: [...fixture().evidence, evidence('pole'), evidence('busan')], categories: ['LIGHTING', 'POLE'] });
  assert.equal(all.length, 3); const pole = all.find(p => p.category === 'POLE');
  assert.equal(pole.high_matched_budget_projects, 1); assert.equal(pole.population_complete, false);
  assert.equal(all.find(p => p.org_name === '인천광역시 옹진군' && p.category === 'LIGHTING').population_complete, true);
  assert.equal(organizationPatterns({ ...fixture(), region: 'unresolved' }).length, 0);
});
test('duplicate request has one global winner before filtering by category', () => {
  const ps = [project('light'), project('pole', { primary_category: 'POLE' })];
  const es = [evidence('light', { shopping_delivery_req_no: 'R', score: 80 }), evidence('pole', { shopping_delivery_req_no: 'R', score: 90 })];
  const all = organizationPatterns({ projects: ps, evidence: es, categories: ['LIGHTING'] });
  assert.equal(all[0].high_match_rows, 0); assert.equal(all[0].actual_shopping_amount, '0');
  const total = organizationPatterns({ projects: ps, evidence: [...es, es[1]], categories: ['LIGHTING', 'POLE'] });
  assert.equal(total.reduce((n, p) => n + p.high_match_rows, 0), 1);
  assert.equal(total.find(p => p.category === 'POLE').actual_shopping_amount, '60');
});
test('integrity conflicts excluded, missing money stays unknown, known sum stays exact', () => {
  const ps = [project('a', { budget_amount: '9007199254740993' }), project('b', { budget_amount: null })];
  const es = [evidence('a', { shopping_amount: '9007199254740995' }), evidence('b', { shopping_amount: '2', shopping_missing_amounts: 1 })];
  const [p] = organizationPatterns({ projects: ps, evidence: es });
  assert.equal(p.observed_budget_amount, '9007199254740993'); assert.equal(p.missing_budget_amounts, 1);
  assert.equal(p.observed_shopping_amount, '9007199254740997'); assert.equal(p.actual_shopping_amount, null);
  assert.equal(p.shopping_to_budget_amount_ratio, null); assert.equal(p.matched_budget_amount, null);
  const [excluded] = organizationPatterns({ projects: ps, evidence: es.map(e => ({ ...e, shopping_request_integrity_valid: false })) });
  assert.equal(excluded.high_match_rows, 0);
  assert.throws(() => organizationPatterns({ projects: [project('x', { budget_amount: 9007199254740992 })] }), /UNSAFE_INTEGER/);
});
test('zero population differs from missing population; no ratio invented for zero denominator', () => {
  const [p] = organizationPatterns({ coverage: [coverage({ expected_project_count: 0, expected_match_count: 0 })], fiscalYears: [2026] });
  assert.equal(p.population_complete, true); assert.equal(p.historical_budget_projects, 0);
  assert.equal(p.historical_budget_amount, '0'); assert.equal(p.high_match_project_rate, null);
});
test('conflicting duplicate project amount invalidates certification; exact duplicate is idempotent', () => {
  const f = fixture();
  const [good] = organizationPatterns({ ...f, projects: [...f.projects, f.projects[0]] });
  assert.equal(good.population_complete, true); assert.equal(good.historical_budget_projects, 10);
  const [bad] = organizationPatterns({ ...f, projects: [...f.projects, project('P0', { budget_amount: '101' })] });
  assert.equal(bad.population_complete, false); assert.ok(bad.population_issues.includes('DUPLICATE_PROJECT_CONFLICT'));
});
test('AIDFA structural rows never become project denominator; unknown category is not inferred from shopping', () => {
  assert.deepEqual(organizationPatterns({ projects: [project('x', { source_layer: 'APPROPRIATION' })] }), []);
  assert.deepEqual(organizationPatterns({ evidence: [{ ...evidence('x'), fiscal_year: 2026, budget_org: '인천광역시 옹진군', budget_region: '인천' }] }), []);
  const normalized = normalizePatternProject({ budget_year: 2026, budget_project: 'name', budget_date: '2026-01-01', budget_amount: '0' });
  assert.equal(normalized.fiscal_year, 2026); assert.equal(normalized.budget_project_name, 'name'); assert.equal(normalized.budget_amount, '0');
});
test('2025 source data is excluded; 2026 no-history is explicit; future window rolls over', () => {
  assert.deepEqual(organizationPatterns({ projects: [project('x', { fiscal_year: 2025 }), project('y', { snapshot_date: '2025-12-31' })] }), []);
  const p = organizationPatterns(fixture())[0];
  assert.deepEqual(patternHistoryYears({ targetFiscalYear: 2026, patterns: [p] }).years, []);
  assert.equal(patternHistoryYears({ targetFiscalYear: 2026 }).basis, 'NO_PRE_TARGET_HISTORY_WITHIN_SOURCE_START');
  const recent = { ...p, evidence_years: [2027, 2028], population_complete_years: [2027],
    scope: { ...p.scope, fiscal_years: [2027, 2028] },
    verified_population_scopes: [{ ...p.verified_population_scopes[0], fiscal_year: 2027 }] };
  assert.deepEqual(patternHistoryYears({ targetFiscalYear: 2029, patterns: [p, recent] }), {
    target_fiscal_year: 2029, years: [2027, 2028], population_years: [2027], evidence_only_years: [2028],
    source_start: '2026-01-01', coverage_scope: 'PROVIDED_ORGANIZATION_CATEGORY_PATTERNS_ONLY', basis: 'LATEST_AVAILABLE_PRE_TARGET_YEARS' });
  assert.equal(patternHistoryYears({ targetFiscalYear: 2027, patterns: [p], region: 'not-known' }).basis, 'REGION_UNRESOLVED');
});
test('history coverage cannot be complete when another institution only has evidence', () => {
  const p = organizationPatterns(fixture())[0];
  const other = { ...p, org_name: '인천광역시 중구', population_complete_years: [] };
  assert.deepEqual(patternHistoryYears({ targetFiscalYear: 2027, patterns: [p, other] }).population_years, []);
});
test('ratio computes with BigInt and handles missing/zero/large values', () => {
  assert.equal(exactRatio('9007199254740993', '9007199254740993'), 1);
  assert.equal(exactRatio(null, '100'), null); assert.equal(exactRatio('100', '0'), null);
  assert.equal(exactRatio('1', '3'), 0.3333); assert.equal(exactRatio('-1', '3'), -0.3333);
  assert.equal(exactRatio('12345', '10000', 3), 1.234);
});
test('explicit pre-start year filter cannot expand to newer years', () => {
  assert.deepEqual(organizationPatterns(fixture({ fiscalYears: [2025] })), []);
});
test('conflicting duplicate snapshots stay deterministic in either input order', () => {
  const f = fixture(); const ps = [...f.projects, project('P0', { budget_amount: '101' })];
  const a = organizationPatterns({ ...f, projects: ps });
  const b = organizationPatterns({ ...f, projects: [...ps].reverse(), evidence: [...f.evidence].reverse() });
  assert.deepEqual(a, b); assert.equal(a[0].population_complete, false);
  const changed = organizationPatterns({ ...f, projects: [...f.projects, project('P0', { payload_hash: 'new-revision' })] });
  assert.equal(changed[0].population_complete, false);
});
test('same source identity with contradictory category cannot certify either population', () => {
  const f = fixture(); const p = organizationPatterns({ ...f, projects: [...f.projects, project('P0', { primary_category: 'POLE' })], categories: ['LIGHTING', 'POLE'] });
  assert.equal(p.find(r => r.category === 'LIGHTING').population_complete, false);
});
test('invalid numeric evidence is rejected rather than becoming a stronger score or complete amount', () => {
  assert.throws(() => organizationPatterns(fixture({ evidence: [evidence('P0', { score: 101 })] })), /INVALID_EVIDENCE_NUMERIC_FIELD/);
  assert.throws(() => organizationPatterns(fixture({ evidence: [evidence('P0', { shopping_missing_amounts: -1 })] })), /INVALID_EVIDENCE_NUMERIC_FIELD/);
});

test('verified empty years remain selectable without becoming observed evidence years', () => {
  const empty = y => coverage({ fiscal_year: y, analysis_id: `run-${y}`, expected_project_count: 0, expected_match_count: 0 });
  const [mixed] = organizationPatterns({ ...fixture(), coverage: [coverage(), empty(2027)], fiscalYears: [2026, 2027] });
  assert.equal(mixed.population_complete, true);
  assert.deepEqual(mixed.evidence_years, [2026]);
  assert.deepEqual(mixed.verified_empty_population_years, [2027]);
  const history = patternHistoryYears({ targetFiscalYear: 2028, patterns: [mixed] });
  assert.deepEqual(history.years, [2026, 2027]); assert.deepEqual(history.population_years, [2026, 2027]);
  const [zero] = organizationPatterns({ coverage: [empty(2026), empty(2027)], fiscalYears: [2026, 2027] });
  assert.deepEqual(zero.evidence_years, []); assert.deepEqual(zero.verified_empty_population_years, [2026, 2027]);
  assert.deepEqual(patternHistoryYears({ targetFiscalYear: 2028, patterns: [zero] }).population_years, [2026, 2027]);
  const [unknown] = organizationPatterns({ coverage: [{ ...empty(2027), source_population_verified: false }], fiscalYears: [2027] });
  assert.deepEqual(patternHistoryYears({ targetFiscalYear: 2028, patterns: [unknown] }).years, []);
  assert.deepEqual(patternHistoryYears({ targetFiscalYear: 2028, patterns: [mixed, { ...unknown, org_name: 'another' }] }).population_years, [2026]);
  for (const invalid of [{ ...zero, verified_population_scopes: [] },
    { ...zero, verified_population_scopes: zero.verified_population_scopes.map(s => ({ ...s, expected_project_count: 1 })) }])
    assert.deepEqual(patternHistoryYears({ targetFiscalYear: 2028, patterns: [invalid] }).years, []);
});

test('contradictory immutable evidence copies cannot certify amounts or population', () => {
  const base = { projects: [project('a')], evidence: [evidence('a')],
    coverage: [coverage({ expected_project_count: 1, expected_match_count: 1 })], fiscalYears: [2026] };
  const [same] = organizationPatterns({ ...base, evidence: [evidence('a'), { ...evidence('a') }] });
  assert.equal(same.population_complete, true); assert.equal(same.actual_shopping_amount, '60');
  for (const change of [{ shopping_amount: '90' }, { score: 89 }, { shopping_missing_amounts: 1 },
    { shopping_request_integrity_valid: false }, { shared_tokens: ['different'] }]) {
    const es = [evidence('a'), evidence('a', change)];
    const [bad] = organizationPatterns({ ...base, evidence: es });
    assert.equal(bad.population_complete, false); assert.equal(bad.actual_shopping_amount, null);
    assert.equal(bad.matched_budget_amount, null); assert.equal(bad.high_match_rows, 0);
    assert.ok(bad.population_issues.includes('DUPLICATE_EVIDENCE_CONFLICT'));
    assert.deepEqual(organizationPatterns({ ...base, evidence: [...es].reverse() }), [bad]);
  }
});

test('byte, nested-list, year and group-work guards fail before expanded outputs', () => {
  const wide = 'x'.repeat(65536);
  assert.throws(() => organizationPatterns({ projects: Array.from({ length: 130 }, (_, i) => project(`P${i}`, { metadata: wide })) }), /PATTERN_INPUT_BYTE_LIMIT/);
  assert.throws(() => organizationPatterns({ evidence: [evidence('a', { shared_tokens: undefined, shared_tokens_json: JSON.stringify(Array(257).fill('x')) })] }), /PATTERN_NESTED_LIST_LIMIT/);
  assert.throws(() => organizationPatterns({ evidence: [evidence('a', { shared_tokens_json: 'x'.repeat(65537) })] }), /PATTERN_INPUT_STRING_LIMIT/);
  assert.throws(() => organizationPatterns({ evidence: [evidence('a', { shared_signals: Array(65).fill('LED') })] }), /PATTERN_NESTED_LIST_LIMIT/);
  assert.throws(() => organizationPatterns({ fiscalYears: Array.from({ length: 51 }, (_, i) => 2026 + i) }), /PATTERN_YEAR_LIMIT/);
  const ps = Array.from({ length: 401 }, (_, i) => project(`P${i}`, { org_name: `인천광역시 기관${i}` }));
  assert.throws(() => organizationPatterns({ projects: ps, fiscalYears: Array.from({ length: 50 }, (_, i) => 2026 + i), limit: 1 }), /PATTERN_SCOPE_CELL_LIMIT/);
  assert.throws(() => organizationPatterns({ projects: Array.from({ length: 1001 }, (_, i) => project(`P${i}`, { org_name: `인천광역시 기관${i}` })) }), /PATTERN_GROUP_LIMIT/);
});
