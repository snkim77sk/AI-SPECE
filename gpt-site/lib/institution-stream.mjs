import {canonicalRegion} from './g2b-domain.mjs';
import {LINEAGE_BASES,LINEAGE_VERSION,lineageDay} from './institution-lineage.mjs';
import { PATTERN_VERSION, exactRatio, SOURCE_START } from './institution-patterns.mjs';
import { PATTERN_SIGNAL_KEYS, readPatternEvent, utf8Bytes, patternText } from './institution-projection.mjs';

export const PATTERN_STREAM_VERSION = 'institution-stream-v2-lineage';
export const PATTERN_STATE_BYTES = 65536;
export const PATTERN_EVENT_BATCH_ROWS = 1000;
export const PATTERN_EVENT_BATCH_BYTES = 1048576;
export const PATTERN_GROUPS_PER_APPEND = 8;
export const PATTERN_ORG_EXAMPLES = 8;
const encoder = new TextEncoder();
const counters = ['request_rows', 'observed_budget_projects', 'missing_budget_amounts', 'high_match_rows', 'candidate_match_rows',
  'high_matched_budget_projects', 'matched_budget_projects', 'missing_matched_budget_amounts', 'missing_shopping_amounts', 'lag_count'];
const totals = ['observed_budget_amount', 'matched_budget_known_amount', 'observed_shopping_amount', 'lag_sum'];
const stateCounters = ['processedProjects', 'processedRequests', 'ineligibleEvents', 'completedGroups'];
const decimal = (v, signed = false) => typeof v === 'string' && v.length <= 97 && (signed ? /^(0|-?[1-9]\d*)$/ : /^(0|[1-9]\d*)$/).test(v);
const add = (object, key, value = 1n) => { object[key] = (BigInt(object[key]) + value).toString(); if (object[key].replace('-', '').length > 96) throw Error('PATTERN_ACCUMULATOR_DIGIT_LIMIT'); };
const publicCount = value => { const n = BigInt(value); if (n > BigInt(Number.MAX_SAFE_INTEGER)) throw Error('PATTERN_COUNT_OUT_OF_RANGE'); return Number(n); };

/** SQLite BINARY text order is UTF-8 bytes, not localeCompare or UTF-16 order. */
export function comparePatternKeys(a, b) {
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    if (typeof a[i] === 'number' && typeof b[i] === 'number') { if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1; continue; }
    const x = encoder.encode(String(a[i] ?? '')), y = encoder.encode(String(b[i] ?? ''));
    for (let at = 0; at < Math.min(x.length, y.length); at++) if (x[at] !== y[at]) return x[at] < y[at] ? -1 : 1;
    if (x.length !== y.length) return x.length < y.length ? -1 : 1;
  }
  return 0;
}
export const patternEventKey = e => [e.region_key, e.org_key, e.category, e.fiscal_year, e.budget_key, e.kind_order, e.source_event_key];
const groupKey = value => [value.region_key, value.org_key, value.category, value.fiscal_year];
function checkState(state) {
  if (!state || state.version !== PATTERN_STREAM_VERSION || !Number.isSafeInteger(state.minScore) || state.minScore < 0 || state.minScore > 100 || typeof state.finished !== 'boolean') throw Error('PATTERN_STATE_SCHEMA');
  patternText(state.analysisId, 'ANALYSIS_ID', 256);
  if(typeof state.lineageAsOfDate!=='string')throw Error('PATTERN_LINEAGE_CONTEXT');if(state.lineageAsOfDate)lineageDay(state.lineageAsOfDate);
  if (stateCounters.some(k => !decimal(state[k]))) throw Error('PATTERN_STATE_COUNTER');
  if (state.lastKey !== null && (!Array.isArray(state.lastKey) || state.lastKey.length !== 7 ||
    state.lastKey.some((v, i) => [3, 5].includes(i) ? !Number.isSafeInteger(v) : typeof v !== 'string'))) throw Error('PATTERN_STATE_CURSOR');
  if (state.group) {
    const g = state.group;
    if (!g.region_key || !g.org_key || !['LIGHTING', 'POLE'].includes(g.category) || !Number.isSafeInteger(g.fiscal_year) || g.fiscal_year < 2026 ||
      counters.some(k => !decimal(g[k])) || totals.some(k => !decimal(g[k], true))) throw Error('PATTERN_STATE_GROUP');
    if (!Array.isArray(g.names) || !g.names.length || g.names.length > PATTERN_ORG_EXAMPLES || typeof g.namesTruncated !== 'boolean' ||
      g.names.some(v => typeof v !== 'string' || utf8Bytes(v) > 1024) || typeof g.displayOrg !== 'string') throw Error('PATTERN_STATE_NAMES');
    if(typeof g.lineageApplied!=='boolean'||typeof g.lineageUnresolved!=='boolean'||!Array.isArray(g.sourceRegions)||g.sourceRegions.length>18||g.sourceRegions.some(r=>typeof r!=='string'||utf8Bytes(r)>256)||!g.lineageBasis||Object.keys(g.lineageBasis).length!==LINEAGE_BASES.length||LINEAGE_BASES.some(k=>!decimal(g.lineageBasis[k])))throw Error('PATTERN_STATE_LINEAGE');
    if (!g.signals || Object.keys(g.signals).length !== PATTERN_SIGNAL_KEYS.length || PATTERN_SIGNAL_KEYS.some(k => !decimal(g.signals[k]))) throw Error('PATTERN_STATE_SIGNALS');
    if (!Array.isArray(g.shoppingCategories) || g.shoppingCategories.length > 3 || g.shoppingCategories.some(v => !['LIGHTING', 'POLE', 'MIXED_TARGET'].includes(v))) throw Error('PATTERN_STATE_CATEGORIES');
  }
  if (state.project) {
    if (!state.group || typeof state.project.key !== 'string' || typeof state.project.hash !== 'string' ||
      !['SOURCE_BUDGET_AMOUNT', 'SOURCE_APPROPRIATION_FALLBACK', 'MISSING'].includes(state.project.expectedBasis) ||
      typeof state.project.matched !== 'boolean' || typeof state.project.high !== 'boolean' ||
      (state.project.expectedBudget !== null && !decimal(state.project.expectedBudget, true))) throw Error('PATTERN_STATE_PROJECT');
  }
  if (state.finished && (state.group || state.project)) throw Error('PATTERN_STATE_FINISHED_WITH_PARTIAL');
  if (utf8Bytes(JSON.stringify(state)) > PATTERN_STATE_BYTES) throw Error('PATTERN_STATE_BYTE_LIMIT');
}
export function serializePatternAccumulator(state) { checkState(state); return JSON.stringify(state); }
export function parsePatternAccumulator(serialized, { analysisId = '', minScore = 80 } = {}) {
  if (typeof serialized !== 'string' || utf8Bytes(serialized) > PATTERN_STATE_BYTES) throw Error('PATTERN_STATE_BYTE_LIMIT');
  let state; try { state = JSON.parse(serialized); } catch { throw Error('PATTERN_STATE_JSON'); }
  checkState(state);
  if (state.analysisId !== analysisId || state.minScore !== minScore) throw Error('PATTERN_STATE_CONTEXT');
  return state;
}
export function createPatternAccumulator({ analysisId = '', minScore = 80, asOfDate = '' } = {}) {
  const state = { version: PATTERN_STREAM_VERSION, analysisId, minScore, lineageAsOfDate:asOfDate, lastKey: null, group: null, project: null,
    processedProjects: '0', processedRequests: '0', ineligibleEvents: '0', completedGroups: '0', finished: false };
  checkState(state); return state;
}
function startGroup(e) {
  return { region_key: e.region_key, org_key: e.org_key, category: e.category, fiscal_year: e.fiscal_year,
    displayOrg: '', names: [], namesTruncated: false, shoppingCategories: [], sourceRegions:[], lineageBasis:Object.fromEntries(LINEAGE_BASES.map(k=>[k,'0'])),lineageApplied:false,lineageUnresolved:false,
    signals: Object.fromEntries(PATTERN_SIGNAL_KEYS.map(k => [k, '0'])),
    ...Object.fromEntries([...counters, ...totals].map(k => [k, '0'])) };
}
function name(g, org, sourceOrg, sourceRegion, basis, applied, unresolved) {
  if(!LINEAGE_BASES.includes(basis))throw Error('PATTERN_LINEAGE_BASIS');add(g.lineageBasis,basis);g.lineageApplied||=applied;g.lineageUnresolved||=unresolved;if(!g.sourceRegions.includes(sourceRegion)){g.sourceRegions.push(sourceRegion);g.sourceRegions.sort();if(g.sourceRegions.length>18)throw Error('PATTERN_LINEAGE_REGION_LIMIT');}
  if (!g.displayOrg || comparePatternKeys([org], [g.displayOrg]) < 0) g.displayOrg = org;
  if (g.names.includes(sourceOrg)) return;
  // Keep deterministic lowest examples, never an unbounded source alias set.
  g.names.push(sourceOrg); g.names.sort((a, b) => comparePatternKeys([a], [b]));
  if (g.names.length > PATTERN_ORG_EXAMPLES) { g.names.pop(); g.namesTruncated = true; }
}
function finishProject(state) {
  const p = state.project, g = state.group;
  if (!p) return;
  if (p.matched) add(g, 'matched_budget_projects');
  if (p.high) {
    add(g, 'high_matched_budget_projects');
    if (p.expectedBudget === null) add(g, 'missing_matched_budget_amounts');
    else add(g, 'matched_budget_known_amount', BigInt(p.expectedBudget));
  }
  state.project = null;
}
function finishGroup(state) {
  finishProject(state);
  const g = state.group;
  if (!g) return null;
  const high = publicCount(g.high_matched_budget_projects), matched = publicCount(g.matched_budget_projects);
  const missingShopping = publicCount(g.missing_shopping_amounts), actual = missingShopping ? null : g.observed_shopping_amount;
  const matchedBudget = g.missing_matched_budget_amounts !== '0' ? null : g.matched_budget_known_amount;
  const signals = Object.entries(g.signals).filter(([, n]) => n !== '0').sort((a, b) => BigInt(a[1]) === BigInt(b[1]) ? comparePatternKeys([a[0]], [b[0]]) : BigInt(a[1]) > BigInt(b[1]) ? -1 : 1);
  const summary = {
    org_name: g.displayOrg, region_name: g.region_key, category: g.category,
    historical_org_names: [...g.names], historical_org_names_truncated: g.namesTruncated,
    organization_lineage_applied: g.lineageApplied, organization_lineage_version:LINEAGE_VERSION, organization_lineage_unresolved:g.lineageUnresolved, historical_region_names:g.sourceRegions,
    organization_lineage_basis: Object.fromEntries(Object.entries(g.lineageBasis).filter(([,n])=>n!=='0').map(([k,n])=>[k,publicCount(n)])),
    scope: { fiscal_years: [g.fiscal_year], region: g.region_key, categories: [g.category], source_start: SOURCE_START },
    evidence_years: [g.fiscal_year], population_complete_years: [], verified_empty_population_years: [], verified_population_scopes: [], evidence_only_years: [g.fiscal_year],
    missing_population_years: [g.fiscal_year], population_complete: false, population_status: 'INCOMPLETE_OR_UNVERIFIED',
    source_population_verified: false, coverage_reasons: [{ fiscal_year: g.fiscal_year, reason: 'SOURCE_POPULATION_UNVERIFIED' }], population_issues: [],
    historical_budget_projects: null, historical_budget_amount: null,
    observed_budget_projects: publicCount(g.observed_budget_projects), observed_budget_amount: g.observed_budget_amount,
    missing_budget_amounts: publicCount(g.missing_budget_amounts),
    high_match_rows: publicCount(g.high_match_rows), candidate_match_rows: publicCount(g.candidate_match_rows),
    high_matched_budget_projects: high, matched_budget_projects: matched, candidate_matched_budget_projects: Math.max(0, matched - high),
    high_match_project_rate: null, matched_project_rate: null, high_matched_shopping_rows: publicCount(g.high_match_rows),
    matched_budget_amount: matchedBudget, actual_shopping_amount: actual, observed_shopping_amount: g.observed_shopping_amount,
    missing_shopping_amounts: missingShopping, shopping_to_budget_amount_ratio: exactRatio(actual, matchedBudget),
    average_nonnegative_lag_days: g.lag_count === '0' ? null : exactRatio(g.lag_sum, g.lag_count, 1),
    signal_counts: Object.fromEntries(signals.map(([k, v]) => [k, publicCount(v)])),
    budget_categories: [g.category], shopping_categories: [...g.shoppingCategories].sort(),
    pattern_basis: 'OBSERVED_MATCH_EVIDENCE_WITHOUT_VERIFIED_DENOMINATOR', pattern_version: PATTERN_VERSION,
    analysis_ids: [state.analysisId], warning: 'Historical evidence strength only, not a procurement probability, planned purchase, or proof of budget funding.',
    sufficient_statistics: { version: PATTERN_STREAM_VERSION, analysis_id: state.analysisId,
      fiscal_year: g.fiscal_year, category: g.category, region_key: g.region_key, org_key: g.org_key,
      ...Object.fromEntries([...counters, ...totals].map(k => [k, g[k]])), signal_counts: { ...g.signals },
      lineage_version:LINEAGE_VERSION, request_assignment_universe: 'SINGLE_IMMUTABLE_MATCHING_JOB', cross_analysis_merge_allowed: false },
  };
  const output = { region_key: g.region_key, org_key: g.org_key, category: g.category, fiscal_year: g.fiscal_year,
    high_projects: high, summary, row_json: JSON.stringify(summary) };
  state.group = null; add(state, 'completedGroups'); return output;
}

/** Input must be globally ordered by patternEventKey using SQLite BINARY text
 * ordering. The D1 PK(run,kind,source_event_key) supplies global duplicate-ID
 * protection; strict cursor checks reject duplicate or reversed event positions.
 * Returns <=8 completed groups and consumedEvents. Only state.lastKey is safe to
 * commit as a cursor: fetched-but-unconsumed rows must be read again next step. */
export function appendPatternEvents(input, events) {
  checkState(input);
  if (input.finished) throw Error('PATTERN_ALREADY_FINISHED');
  if (!Array.isArray(events) || events.length > PATTERN_EVENT_BATCH_ROWS) throw Error('PATTERN_EVENT_BATCH_ROW_LIMIT');
  let inputBytes = 0;
  for (const e of events) { inputBytes += utf8Bytes(JSON.stringify(e)); if (inputBytes > PATTERN_EVENT_BATCH_BYTES) throw Error('PATTERN_EVENT_BATCH_BYTE_LIMIT'); }
  const state = structuredClone(input), groups = [];
  let consumedEvents = 0, processedProjects = 0, processedRequests = 0, ineligibleEvents = 0;
  for (const e of events) {
    const d = readPatternEvent(e, state.analysisId), key = patternEventKey(e);
    if(state.lineageAsOfDate&&d.lineage_as_of!==state.lineageAsOfDate)throw Error('PATTERN_LINEAGE_CONTEXT');
    if (state.lastKey && comparePatternKeys(key, state.lastKey) <= 0) throw Error('PATTERN_EVENT_ORDER_OR_DUPLICATE');
    const changesGroup = state.group && comparePatternKeys(groupKey(e), groupKey(state.group)) !== 0;
    // Stop before consuming the next group when the output budget is reached.
    if (e.eligible && changesGroup && groups.length >= PATTERN_GROUPS_PER_APPEND) break;
    if (!e.eligible) {
      add(state, 'ineligibleEvents'); ineligibleEvents++; state.lastKey = key; consumedEvents++; continue;
    }
    if (changesGroup) groups.push(finishGroup(state));
    if (!state.group) state.group = startGroup(e);
    const g = state.group;
    name(g, d.org_name,d.source_org_name,canonicalRegion(d.source_region_name),d.lineage_basis,d.lineage_applied,d.lineage_unresolved);
    if (e.kind === 'PROJECT') {
      finishProject(state);
      state.project = { key: e.budget_key, hash: d.payload_hash, expectedBudget: d.budget_amount ?? d.appropriation_amount,
        expectedBasis: d.budget_amount !== null ? 'SOURCE_BUDGET_AMOUNT' : d.appropriation_amount !== null ? 'SOURCE_APPROPRIATION_FALLBACK' : 'MISSING', matched: false, high: false };
      add(g, 'observed_budget_projects');
      if (d.budget_amount === null) add(g, 'missing_budget_amounts'); else add(g, 'observed_budget_amount', BigInt(d.budget_amount));
      add(state, 'processedProjects'); processedProjects++;
    } else {
      const p = state.project;
      if (!p || p.key !== e.budget_key) throw Error('PATTERN_REQUEST_WITHOUT_PROJECT');
      if (p.hash !== d.budget_payload_hash || p.expectedBudget !== d.budget_amount || p.expectedBasis !== d.budget_amount_basis) throw Error('PATTERN_REQUEST_PROJECT_CONFLICT');
      p.matched = true; add(g, 'request_rows');
      if (d.level === 'CANDIDATE') add(g, 'candidate_match_rows');
      if (d.score >= state.minScore) {
        p.high = true; add(g, 'high_match_rows');
        if (d.shopping_amount !== null) add(g, 'observed_shopping_amount', BigInt(d.shopping_amount));
        if (d.shopping_amount === null || d.shopping_missing_amounts > 0) add(g, 'missing_shopping_amounts');
        if (d.lag_days !== null && d.lag_days >= 0) { add(g, 'lag_sum', BigInt(d.lag_days)); add(g, 'lag_count'); }
        for (const signal of d.shared_signals) add(g.signals, signal);
        if (!g.shoppingCategories.includes(d.shopping_category)) g.shoppingCategories.push(d.shopping_category);
      }
      add(state, 'processedRequests'); processedRequests++;
    }
    state.lastKey = key; consumedEvents++;
  }
  checkState(state);
  return { state, groups, consumedEvents, processedProjects, processedRequests, ineligibleEvents };
}
/** Call only when the service proves true EOF, not a short byte-framed page.
 * Repeated finalization is harmless: no completed group is emitted twice. */
export function finishPatternAccumulator(input) {
  checkState(input);
  if (input.finished) return { state: structuredClone(input), groups: [], consumedEvents: 0, processedProjects: 0, processedRequests: 0, ineligibleEvents: 0 };
  const state = structuredClone(input), group = finishGroup(state); state.finished = true; checkState(state);
  return { state, groups: group ? [group] : [], consumedEvents: 0, processedProjects: 0, processedRequests: 0, ineligibleEvents: 0 };
}
