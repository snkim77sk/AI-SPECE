import test from 'node:test';
import assert from 'node:assert/strict';
import { projectEvent, requestEvent, utf8Bytes } from '../lib/institution-projection.mjs';
import { organizationPatterns } from '../lib/institution-patterns.mjs';
import { createPatternAccumulator, appendPatternEvents, finishPatternAccumulator, parsePatternAccumulator, serializePatternAccumulator,
  comparePatternKeys, patternEventKey, PATTERN_STATE_BYTES, PATTERN_GROUPS_PER_APPEND } from '../lib/institution-stream.mjs';
const project = (key, over = {}) => ({ source_key: key, payload_hash: 'hash-' + key, org_name: '인천광역시 옹진군', region_name: '인천광역시',
  primary_category: 'LIGHTING', fiscal_year: 2026, snapshot_date: '2026-03-01', budget_amount: '100', appropriation_amount: null, ...over });
const pe = p => projectEvent({ source_key: p.source_key, project_json: JSON.stringify(p) }, { analysisId: 'J' });
function evidence(p, requestNo, over = {}) {
  return { analysis_id: 'J', budget_project_identity: p.source_key, budget_payload_hash: p.payload_hash,
    budget_year: p.fiscal_year, budget_org: p.org_name, budget_region: p.region_name, budget_amount: p.budget_amount ?? p.appropriation_amount,
    shopping_source_key: 'REQUEST:' + requestNo, shopping_delivery_req_no: requestNo, shopping_date: '2026-05-01', shopping_category: 'LIGHTING',
    shopping_amount: '60', shopping_missing_amounts: 0, score: 90, level: 'HIGH', shared_signals: ['LED', 'STREET_LIGHT'], lag_days: 10,
    organization_basis: 'EXACT_ORG_NAME', ...over };
}
function re(p, n, over = {}) {
  const e = evidence(p, n, over);
  return requestEvent({ source_key: e.shopping_source_key, request_key: e.shopping_source_key, budget_key: p.source_key,
    score: e.score, level: e.level, project_json: JSON.stringify(p), evidence_json: JSON.stringify(e),
    request_record_key: e.shopping_source_key, request_integrity: 'VALID', request_missing_amounts: e.shopping_missing_amounts }, { analysisId: 'J' });
}
const ordered = events => events.sort((a, b) => comparePatternKeys(patternEventKey(a), patternEventKey(b)));
function consume(events, size = 250) {
  let state = createPatternAccumulator({ analysisId: 'J' }); const groups = [];
  for (let at = 0; at < events.length;) {
    const page = events.slice(at, at + size), part = appendPatternEvents(state, page);
    assert.ok(part.consumedEvents > 0); at += part.consumedEvents; state = parsePatternAccumulator(serializePatternAccumulator(part.state), { analysisId: 'J' }); groups.push(...part.groups);
  }
  const done = finishPatternAccumulator(state); groups.push(...done.groups);
  return { state: done.state, groups };
}
function small() {
  const ps = Array.from({ length: 6 }, (_, i) => project('P' + i));
  const specs = [[0, 'R0', {}], [0, 'R1', { lag_days: 20 }], [1, 'R2', { score: 70, level: 'CANDIDATE' }],
    [2, 'R3', { shopping_missing_amounts: 1, shopping_amount: '20', lag_days: -1 }], [3, 'R4', { shopping_amount: null, lag_days: null }]];
  const es = specs.map(([i, n, over]) => evidence(ps[i], n, over));
  return { projects: ps, evidence: es, events: ordered([...ps.map(pe), ...specs.map(([i, n, over]) => re(ps[i], n, over))]) };
}

test('small fixture matches offline observed-only formula fields, with explicit provenance and sufficient stats', () => {
  const f = small(), expected = organizationPatterns({ projects: f.projects.map(p => ({ ...p, analysis_id: 'J' })), evidence: f.evidence })[0];
  const { groups, state } = consume(f.events, 2), got = groups[0].summary;
  for (const key of Object.keys(expected)) assert.deepEqual(got[key], expected[key], key);
  assert.equal(got.source_population_verified, false); assert.equal(got.historical_org_names_truncated, false);
  assert.equal(got.sufficient_statistics.lag_sum, '30'); assert.equal(got.sufficient_statistics.lag_count, '2');
  assert.equal(got.sufficient_statistics.analysis_id, 'J'); assert.equal(got.sufficient_statistics.cross_analysis_merge_allowed, false);
  assert.equal(state.processedProjects, '6'); assert.equal(state.processedRequests, '5'); assert.equal(state.finished, true);
  assert.deepEqual(JSON.parse(groups[0].row_json), got);
});
test('every page split point preserves identical completed result and does not mutate input', () => {
  const f = small(), expected = consume(f.events).groups, before = JSON.stringify(f.events);
  for (let split = 0; split <= f.events.length; split++) {
    const initial = createPatternAccumulator({ analysisId: 'J' }), old = JSON.stringify(initial);
    const a = appendPatternEvents(initial, f.events.slice(0, split)); assert.equal(JSON.stringify(initial), old);
    const b = appendPatternEvents(parsePatternAccumulator(serializePatternAccumulator(a.state), { analysisId: 'J' }), f.events.slice(split));
    const c = finishPatternAccumulator(b.state); assert.deepEqual([...a.groups, ...b.groups, ...c.groups], expected);
  }
  assert.equal(JSON.stringify(f.events), before);
});
test('one project with 10,001 requests spans pages in fixed state; request amounts and project budget counted correctly', () => {
  const p = project('P'), total = 10001;
  let state = appendPatternEvents(createPatternAccumulator({ analysisId: 'J' }), [pe(p)]).state, maxBytes = 0;
  for (let at = 0; at < total; at += 200) {
    const events = Array.from({ length: Math.min(200, total - at) }, (_, i) => re(p, String(at + i).padStart(6, '0'), { shopping_amount: '9007199254740993' }));
    const result = appendPatternEvents(state, events); assert.equal(result.groups.length, 0); state = result.state;
    const serialized = serializePatternAccumulator(state); maxBytes = Math.max(maxBytes, utf8Bytes(serialized)); state = parsePatternAccumulator(serialized, { analysisId: 'J' });
  }
  const g = finishPatternAccumulator(state).groups[0].summary;
  assert.equal(g.observed_budget_projects, 1); assert.equal(g.high_matched_budget_projects, 1); assert.equal(g.high_match_rows, total);
  assert.equal(g.matched_budget_amount, '100'); assert.equal(g.actual_shopping_amount, (9007199254740993n * BigInt(total)).toString());
  assert.equal(g.average_nonnegative_lag_days, 10); assert.ok(maxBytes < 8192, String(maxBytes));
});
test('21,003 projects in one institution do not enter a whole-array map or trip offline 20k guard', () => {
  const total = 21003; let state = createPatternAccumulator({ analysisId: 'J' }), maxBytes = 0;
  for (let at = 0; at < total; at += 250) {
    const events = Array.from({ length: Math.min(250, total - at) }, (_, i) => pe(project('P' + String(at + i).padStart(6, '0'), { budget_amount: '10' })));
    const part = appendPatternEvents(state, events); state = part.state; assert.equal(part.groups.length, 0);
    maxBytes = Math.max(maxBytes, utf8Bytes(serializePatternAccumulator(state)));
  }
  const done = finishPatternAccumulator(state), g = done.groups[0].summary;
  assert.equal(g.observed_budget_projects, total); assert.equal(g.observed_budget_amount, String(total * 10));
  assert.equal(g.high_matched_budget_projects, 0); assert.equal(g.actual_shopping_amount, '0'); assert.equal(g.historical_budget_projects, null);
  assert.ok(maxBytes < 8192, String(maxBytes)); assert.equal(done.state.processedProjects, String(total));
});
test('more than eight tiny groups return consumed cursor, leaving fetched-but-unconsumed rows for replay', () => {
  const events = ordered(Array.from({ length: 33 }, (_, i) => pe(project('P' + i, { org_name: '인천광역시 기관' + String(i).padStart(3, '0') }))));
  const first = appendPatternEvents(createPatternAccumulator({ analysisId: 'J' }), events);
  assert.equal(first.groups.length, PATTERN_GROUPS_PER_APPEND); assert.ok(first.consumedEvents < events.length);
  assert.deepEqual(first.state.lastKey, patternEventKey(events[first.consumedEvents - 1]));
  assert.equal(consume(events, 100).groups.length, 33);
});
test('year, region, organization and budget category each define a separate group', () => {
  const ps = [project('a'), project('b', { fiscal_year: 2027 }), project('c', { primary_category: 'POLE' }),
    project('d', { org_name: '부산광역시 중구', region_name: '부산광역시' })];
  const events = ordered(ps.flatMap((p, i) => [pe(p), re(p, 'R' + i)])), result = consume(events, 2);
  assert.equal(result.groups.length, 4);
  for (const g of result.groups) { assert.equal(g.summary.high_matched_budget_projects, 1); assert.deepEqual(g.summary.population_complete_years, []); assert.equal(g.summary.actual_shopping_amount, '60'); }
  assert.equal(result.groups.reduce((n, g) => n + g.summary.high_match_rows, 0), 4);
});
test('duplicate/reversed positions, wrong analysis and requests without project are rejected atomically', () => {
  const p = project('P'), event = pe(p), start = createPatternAccumulator({ analysisId: 'J' });
  const part = appendPatternEvents(start, [event]); const saved = JSON.stringify(part.state);
  assert.throws(() => appendPatternEvents(part.state, [event]), /ORDER_OR_DUPLICATE/);
  assert.throws(() => appendPatternEvents(start, [re(p, 'R')]), /REQUEST_WITHOUT_PROJECT/);
  const wrong = JSON.parse(event.row_json); wrong.analysis_id = 'OTHER';
  assert.throws(() => appendPatternEvents(start, [{ ...event, row_json: JSON.stringify(wrong) }]), /EVENT_CONTEXT/);
  assert.equal(JSON.stringify(part.state), saved);
  const p2 = project('A'); assert.throws(() => appendPatternEvents(part.state, [pe(p2)]), /ORDER_OR_DUPLICATE/);
});
test('current project hash/amount conflicts fail without changing prior partial', () => {
  const p = project('P'), state = appendPatternEvents(createPatternAccumulator({ analysisId: 'J' }), [pe(p)]).state;
  const conflicting = re(project('P', { payload_hash: 'changed' }), 'R');
  assert.throws(() => appendPatternEvents(state, [conflicting]), /REQUEST_PROJECT_CONFLICT/);
  const changedMoney = re(project('P', { budget_amount: '101' }), 'R');
  assert.throws(() => appendPatternEvents(state, [changedMoney]), /REQUEST_PROJECT_CONFLICT/);
  assert.equal(state.processedRequests, '0');
});
test('missing/null/negative/zero money and appropriation fallback remain exact with separate observed completeness', () => {
  const ps = [project('a', { budget_amount: null, appropriation_amount: '300' }), project('b', { budget_amount: '-10' }), project('c', { budget_amount: '0' })];
  const es = ordered(ps.flatMap((p, i) => [pe(p), re(p, 'R' + i, { shopping_amount: i ? '0' : null, shopping_missing_amounts: i ? 0 : 1 })]));
  const g = consume(es, 1).groups[0].summary;
  assert.equal(g.observed_budget_amount, '-10'); assert.equal(g.missing_budget_amounts, 1);
  assert.equal(g.matched_budget_amount, '290'); assert.equal(g.observed_shopping_amount, '0');
  assert.equal(g.actual_shopping_amount, null); assert.equal(g.shopping_to_budget_amount_ratio, null);
});
test('nonnegative lag and per-request signal counts survive cursor split and same-project candidates', () => {
  const p = project('P'); const es = [pe(p), re(p, 'A', { lag_days: -5 }), re(p, 'B', { lag_days: null }),
    re(p, 'C', { lag_days: 5, shared_signals: ['LED', 'LED'] }), re(p, 'D', { score: 70, level: 'CANDIDATE', lag_days: 1000 })];
  const g = consume(es, 1).groups[0].summary;
  assert.equal(g.average_nonnegative_lag_days, 5); assert.equal(g.high_match_rows, 3); assert.equal(g.candidate_match_rows, 1);
  assert.equal(g.matched_budget_projects, 1); assert.equal(g.candidate_matched_budget_projects, 0); assert.equal(g.signal_counts.LED, 3);
  assert.equal(g.organization_lineage_basis.SOURCE_ORG, 5);
});
test('ineligible events counted separately and cannot become an apparent zero or verified population', () => {
  const bad = project('A', { fiscal_year: 2025 }), good = project('B');
  const events = ordered([pe(bad), re(bad, 'A'), pe(good), re(good, 'B')]); const done = consume(events, 1);
  assert.equal(done.state.ineligibleEvents, '2'); assert.equal(done.state.processedProjects, '1'); assert.equal(done.groups.length, 1);
  const empty = consume([pe(bad)]); assert.equal(empty.groups.length, 0); assert.equal(empty.state.ineligibleEvents, '1');
});
test('raw organization examples have fixed cardinality, deterministic smallest labels and explicit truncation', () => {
  const ps = Array.from({ length: 30 }, (_, i) => project(String(i).padStart(3, '0'), { org_name: '인천광역시' + '-'.repeat(i) + '옹진군' }));
  const done = consume(ps.map(pe), 2), g = done.groups[0].summary;
  assert.equal(done.groups.length, 1); assert.equal(g.historical_org_names.length, 8); assert.equal(g.historical_org_names_truncated, true);
  assert.equal(g.organization_lineage_applied, false); assert.ok(utf8Bytes(serializePatternAccumulator(done.state)) < PATTERN_STATE_BYTES);
});
test('state serialization validates context, bounded counters and byte limit', () => {
  const s = createPatternAccumulator({ analysisId: 'J' }); assert.deepEqual(parsePatternAccumulator(serializePatternAccumulator(s), { analysisId: 'J' }), s);
  assert.throws(() => parsePatternAccumulator(serializePatternAccumulator(s), { analysisId: 'OTHER' }), /STATE_CONTEXT/);
  assert.throws(() => serializePatternAccumulator({ ...s, processedProjects: '-1' }), /STATE_COUNTER/);
  assert.throws(() => parsePatternAccumulator(' '.repeat(PATTERN_STATE_BYTES + 1), { analysisId: 'J' }), /STATE_BYTE_LIMIT/);
  assert.throws(() => appendPatternEvents(s, Array(1001).fill({})), /BATCH_ROW_LIMIT/);
  assert.throws(() => appendPatternEvents(s, Array(30).fill({ huge: 'x'.repeat(40000) })), /BATCH_BYTE_LIMIT/);
});
test('empty EOF, repeated EOF, and append after EOF do not fabricate or duplicate groups', () => {
  const empty = finishPatternAccumulator(createPatternAccumulator({ analysisId: 'J' })); assert.deepEqual(empty.groups, []);
  const done = consume([pe(project('P'))]); assert.equal(finishPatternAccumulator(done.state).groups.length, 0);
  assert.throws(() => appendPatternEvents(done.state, []), /ALREADY_FINISHED/);
});
test('UTF-8 BINARY key order handles supplementary characters unlike UTF-16 string order', () => {
  assert.equal(comparePatternKeys(['\uE000'], ['😀']), -1); assert.ok('\uE000' > '😀');
  const events = [pe(project('\uE000')), pe(project('😀'))]; assert.equal(consume(events, 1).groups[0].summary.observed_budget_projects, 2);
});
test('custom minScore does not double-count candidate rows in source evidence count', () => {
  const p = project('P'), initial = createPatternAccumulator({ analysisId: 'J', minScore: 65 });
  const part = appendPatternEvents(initial, [pe(p), re(p, 'R', { score: 70, level: 'CANDIDATE' })]);
  const g = finishPatternAccumulator(part.state).groups[0].summary;
  assert.equal(g.high_match_rows, 1); assert.equal(g.candidate_match_rows, 1); assert.equal(g.organization_lineage_basis.SOURCE_ORG, 2);
});
test('budget fallback basis cannot change while keeping same numerical amount', () => {
  const p = project('P'), part = appendPatternEvents(createPatternAccumulator({ analysisId: 'J' }), [pe(p)]);
  const e = re(p, 'R'), body = JSON.parse(e.row_json); body.budget_amount_basis = 'SOURCE_APPROPRIATION_FALLBACK';
  assert.throws(() => appendPatternEvents(part.state, [{ ...e, row_json: JSON.stringify(body) }]), /REQUEST_PROJECT_CONFLICT/);
});
test('negative fractional/unsafe source counters and state snapshots cannot be silently resumed', () => {
  const p = project('P'), state = appendPatternEvents(createPatternAccumulator({ analysisId: 'J' }), [pe(p)]).state;
  const bad = structuredClone(state); bad.group.signals.LED = '-1';
  assert.throws(() => serializePatternAccumulator(bad), /STATE_SIGNALS/);
  assert.throws(() => parsePatternAccumulator(serializePatternAccumulator(state), { analysisId: 'J', minScore: 65 }), /STATE_CONTEXT/);
});
