import { integer, norm, orgAliases, selectWinners } from './matching.mjs';
import { canonicalRegion } from './g2b-domain.mjs';
import { regionHistoryMembers } from './read-filters.mjs';

// Pure, caller-supplied snapshots only. No database, clock, providers or credentials.
// Unlike the legacy store, the presence of a project (or a finished match job)
// does NOT certify a source population. Scope-specific attestations are required.
export const PATTERN_VERSION = 'institution-patterns-v2-bounded-verified-empty';
export const SOURCE_START = '2026-01-01';
export const MAX_PATTERN_INPUT_ROWS = 20000;
export const MAX_PATTERN_INPUT_BYTES = 8 * 1024 * 1024;
export const MAX_PATTERN_YEARS = 50;
export const MAX_PATTERN_SCOPE_CELLS = 20000;
const MAX_PATTERN_GROUPS = 1000;
const TARGETS = ['LIGHTING', 'POLE'];
const text = v => String(v ?? '').trim();
const cmp = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const unique = values => [...new Set(values)].sort(cmp);
const year = value => Number.isSafeInteger(Number(value)) && Number(value) > 0 ? Number(value) : 0;
const yearsOf = values => unique((values || []).map(year).filter(v => v >= 2026));
const categoryOf = r => text(r.budget_category || r.primary_category || r.category).toUpperCase();
const idOf = r => text(r.budget_project_identity || r.budget_raw_source_key || r.source_key);
const analysisOf = r => text(r.analysis_id || r.run_key || r.job_id);
const money = v => integer(v, { missing: null });
const json = value => JSON.stringify(value, (_, v) => typeof v === 'bigint' ? v.toString() : v);
const projectKey = r => json([r.fiscal_year, r.budget_project_identity]);
const scopeKey = r => json([r.budget_region, norm(r.budget_org), r.budget_category]);
const fingerprint = r => json([r.fiscal_year, r.budget_org, r.budget_region, r.budget_category, r.budget_amount,
  r.budget_source_date, r.payload_hash || r.budget_payload_hash || '']);

// Count logical bytes, including repeated references, before parsing/copying.
// Walking in bounded pieces avoids serializing a potentially huge input first.
export function snapshotByteGuard(label, maxBytes = MAX_PATTERN_INPUT_BYTES) {
  let bytes = 0;
  const ancestors = new Set(), encoder = new TextEncoder();
  const add = n => { bytes += n; if (bytes > maxBytes) throw Error(label + '_BYTE_LIMIT'); };
  const string = value => {
    if (value.length > 65536) throw Error(label + '_STRING_LIMIT');
    const size = encoder.encode(value).byteLength;
    if (size > 65536) throw Error(label + '_STRING_LIMIT');
    // Escaped control characters also consume response/input framing bytes.
    add(encoder.encode(JSON.stringify(value)).byteLength);
  };
  function visit(value, depth = 0) {
    if (depth > 6) throw Error(label + '_DEPTH_LIMIT');
    if (typeof value === 'string' || typeof value === 'bigint') return string(String(value));
    if (value === null || typeof value !== 'object') { add(24); return; }
    if (ancestors.has(value)) throw Error(label + '_CYCLIC_INPUT');
    ancestors.add(value); add(2);
    if (Array.isArray(value)) {
      if (value.length > 256) throw Error(label + '_LIST_LIMIT');
      for (const item of value) { add(1); visit(item, depth + 1); }
    } else {
      const keys = Object.keys(value);
      if (keys.length > 128) throw Error(label + '_FIELD_LIMIT');
      for (const key of keys) { add(2); string(key); visit(value[key], depth + 1); }
    }
    ancestors.delete(value);
  }
  return visit;
}

export function exactRatio(numerator, denominator, digits = 4) {
  const n = money(numerator), d = money(denominator);
  if (n === null || d === null || d <= 0n) return null;
  if (!Number.isSafeInteger(digits) || digits < 0 || digits > 8) throw Error('INVALID_RATIO_DIGITS');
  const scale = 10n ** BigInt(digits), negative = n < 0n;
  const scaled = (negative ? -n : n) * scale, quotient = scaled / d, remainder = scaled % d;
  // Round ties to even, matching Python's integral arithmetic convention without
  // converting monetary operands to floating point. The ratio is presentation only.
  const rounded = quotient + (remainder * 2n > d || (remainder * 2n === d && quotient % 2n) ? 1n : 0n);
  if (rounded > BigInt(Number.MAX_SAFE_INTEGER)) return null;
  return Number(negative ? -rounded : rounded) / Number(scale);
}

export function normalizePatternProject(row = {}) {
  const r = { ...row };
  return {
    ...r,
    analysis_id: analysisOf(r),
    fiscal_year: year(r.fiscal_year || r.budget_year),
    budget_project_identity: idOf(r),
    budget_raw_source_key: text(r.budget_raw_source_key || r.source_key),
    budget_org: text(r.budget_org || r.org_name || r.org),
    budget_region: canonicalRegion(r.budget_region || r.region_name || r.region),
    budget_category: categoryOf(r),
    budget_project_name: text(r.budget_project_name || r.budget_project || r.project_name || r.project),
    budget_source_date: text(r.budget_source_date || r.budget_date || r.source_date || r.snapshot_date),
    budget_amount: money(r.budget_amount)?.toString() ?? null,
    best_match_score: Number(r.best_match_score || 0),
    best_match_level: text(r.best_match_level) || 'UNMATCHED',
  };
}

function list(value, maxItems) {
  let parsed = value;
  if (!Array.isArray(parsed)) {
    try { parsed = JSON.parse(value || '[]'); } catch { return []; }
  }
  if (!Array.isArray(parsed)) return [];
  if (parsed.length > maxItems) throw Error('PATTERN_NESTED_LIST_LIMIT');
  if (parsed.some(v => typeof v !== 'string')) throw Error('INVALID_PATTERN_LIST_ITEM');
  return parsed;
}
function inScope(r, years, regions, categories) {
  return r.fiscal_year >= 2026 && (!years.length || years.includes(r.fiscal_year)) &&
    (!regions.length || regions.includes(r.budget_region)) && categories.includes(r.budget_category) &&
    (!r.budget_source_date || r.budget_source_date >= SOURCE_START) && (!r.shopping_date || r.shopping_date >= SOURCE_START) &&
    r.source_layer !== 'APPROPRIATION' && r.service !== 'AIDFA';
}
function manifestRow(raw) {
  return normalizePatternProject({ ...raw, budget_org: raw.org_name ?? raw.budget_org,
    budget_region: raw.region_name ?? raw.budget_region, budget_category: raw.category ?? raw.budget_category });
}
function missingAmount(rows, field) { return rows.some(r => r[field] === null || r[field] === undefined); }
function sum(rows, field) { return rows.reduce((total, r) => total + (money(r[field]) ?? 0n), 0n).toString(); }

/**
 * Offline/bounded-input building block, NOT a production whole-dataset scan.
 * Caller must page/aggregate in D1; this function materializes its supplied scope.
 * Guards cannot undo memory spent loading caller input. Logical input/output is
 * limited to 8 MiB, strings to 64 KiB, nested arrays to 256 items, and nesting to
 * six levels. Signals/tokens allow 64/256 items. At most 50 years, 1,000 groups,
 * and 20,000 group/year cells may be materialized; limit does not bypass guards.
 * Input contract:
 * - projects: full *provided* project documents, including unmatched documents.
 * - evidence: request-level candidates/winners; missing category/region/year may
 *   be joined ONLY from the identical project id + analysis id. All candidates
 *   must be supplied before a scope filter, so filtering cannot reassign a request.
 * - analysis_id (or run_key/job_id) must identify the same immutable snapshot in
 *   projects, evidence and coverage. Caller selects latest nonstale snapshots;
 *   mixing snapshots for the same org/category/year invalidates the denominator.
 * - coverage: {analysis_id, org_name, region_name, fiscal_year, category,
 *   status:'complete', source_population_verified:true, analysis_complete:true,
 *   population_scope:'FULL_FISCAL_YEAR_ORG_CATEGORY', expected_project_count, expected_match_count,
 *   evidence_ref}. Count must equal unique provided project ids, including zero.
 *   These are externally verified source attestations, NOT match-job status.
 * - fiscalYears/region/categories bound the requested scope. 2025 is never used.
 * Money accepts exact integer strings/BigInt/safe integers; output money is text.
 * Unknown denominators/rates/totals are null, with observed counts/sums separate.
 * One result per exact source organization + canonical region + budget category.
 * No administrative lineage or learned name association is inferred here.
 */
export function organizationPatterns({ projects = [], evidence = [], coverage = [], fiscalYears = [],
  region = '', categories = TARGETS, minScore = 80, limit = 200 } = {}) {
  if (projects.length + evidence.length + coverage.length > MAX_PATTERN_INPUT_ROWS) throw Error('PATTERN_INPUT_ROW_LIMIT');
  if (fiscalYears.length > MAX_PATTERN_YEARS) throw Error('PATTERN_YEAR_LIMIT');
  const checkBytes = snapshotByteGuard('PATTERN_INPUT');
  checkBytes({ fiscalYears, region, categories });
  for (const rows of [projects, evidence, coverage]) for (const row of rows) checkBytes(row);
  const selectedYears = yearsOf(fiscalYears), cats = unique(categories.map(v => text(v).toUpperCase()).filter(v => TARGETS.includes(v)));
  if (!Number.isSafeInteger(minScore) || minScore < 0 || minScore > 100) throw Error('INVALID_MIN_SCORE');
  if (fiscalYears.length && !selectedYears.length) return [];
  const selectedRegion = canonicalRegion(region);
  if (text(region) && !selectedRegion) return [];
  const regions = selectedRegion ? regionHistoryMembers(selectedRegion) : [];
  const normalized = projects.map(normalizePatternProject), lookup = new Map();
  for (const p of normalized) {
    const key = json([p.analysis_id, p.budget_project_identity]);
    const current = lookup.get(key);
    if (!current) lookup.set(key, p);
    else if (fingerprint(current) !== fingerprint(p)) lookup.set(key, {
      ...(fingerprint(current) < fingerprint(p) ? current : p), ambiguous: true });
  }
  const normalizedEvidence = evidence.map(raw => {
    const p = lookup.get(json([analysisOf(raw), idOf(raw)]));
    const usable = p && !p.ambiguous ? p : {};
    const r = normalizePatternProject({ ...usable, ...raw,
      fiscal_year: raw.fiscal_year || raw.budget_year || usable.fiscal_year,
      budget_category: raw.budget_category || usable.budget_category,
      budget_org: raw.budget_org || usable.budget_org,
      budget_region: raw.budget_region || usable.budget_region,
      budget_amount: Object.hasOwn(raw, 'budget_amount') ? raw.budget_amount : usable.budget_amount });
    return { ...r, score: Number(raw.score || 0), level: text(raw.level),
      shared_signals: list(raw.shared_signals ?? raw.shared_signals_json, 64).map(text).filter(Boolean),
      shared_tokens: list(raw.shared_tokens ?? raw.shared_tokens_json, 256),
      shopping_amount: money(raw.shopping_amount)?.toString() ?? null,
      shopping_missing_amounts: Number(raw.shopping_missing_amounts || raw.missing_amount_count || 0),
      shopping_request_integrity_valid: raw.shopping_request_integrity_valid !== false && raw.request_integrity_valid !== false,
    };
  }).filter(r => {
    if (!Number.isSafeInteger(r.score) || r.score < 0 || r.score > 100 ||
      !Number.isSafeInteger(r.shopping_missing_amounts) || r.shopping_missing_amounts < 0) throw Error('INVALID_EVIDENCE_NUMERIC_FIELD');
    return true;
  }).filter(r => r.budget_project_identity && r.fiscal_year >= 2026);
  const copies = new Map(), conflicts = new Set();
  const requestKey = r => json([r.analysis_id, r.budget_project_identity,
    r.shopping_delivery_req_no ? ['REQUEST', text(r.shopping_delivery_req_no)] :
      r.shopping_source_key ? ['SOURCE', text(r.shopping_source_key)] :
        ['FALLBACK', r.shopping_date, r.shopping_org, r.shopping_category, r.shopping_item, r.shopping_delivery_name, r.shopping_vendor]]);
  const evidenceFingerprint = r => json([fingerprint(r), r.score, r.level, r.shopping_amount, r.shopping_missing_amounts, r.shopping_request_integrity_valid,
    r.shopping_date, r.shopping_org, r.shopping_category, r.shopping_item, r.shopping_delivery_name, r.shopping_vendor,
    r.shopping_source_key, r.shopping_delivery_req_no, r.organization_basis, r.lag_days,
    [...r.shared_signals].sort(cmp), [...r.shared_tokens].sort(cmp)]);
  for (const e of normalizedEvidence) {
    const key = requestKey(e), signature = evidenceFingerprint(e);
    if (copies.has(key) && copies.get(key) !== signature) conflicts.add(key);
    else copies.set(key, signature);
  }
  // Conflicting copies cannot be made authoritative by choosing the closer
  // monetary match. Exact copies remain idempotent under global assignment.
  const winners = selectWinners(normalizedEvidence.filter(r => r.shopping_request_integrity_valid && !conflicts.has(requestKey(r))))
    .filter(r => inScope(r, selectedYears, regions, cats));
  const groups = new Map();
  function group(r) {
    if (!r.budget_org || !r.budget_region || !r.budget_category) return null;
    const key = scopeKey(r);
    if (!groups.has(key)) groups.set(key, { rows: [], evidence: [], manifests: [], org: r.budget_org,
      region: r.budget_region, category: r.budget_category, names: new Set(), issues: new Set() });
    const g = groups.get(key); g.names.add(r.budget_org); return g;
  }
  for (const p of normalized.filter(r => inScope(r, selectedYears, regions, cats))) {
    const g = group(p); if (!g) continue;
    if (lookup.get(json([p.analysis_id, p.budget_project_identity]))?.ambiguous) g.issues.add('DUPLICATE_PROJECT_CONFLICT');
    if (!p.budget_project_identity) g.issues.add('PROJECT_ID_MISSING');
    else g.rows.push(p);
  }
  for (const e of winners) { const g = group(e); if (g) g.evidence.push(e); }
  for (const e of normalizedEvidence) if (conflicts.has(requestKey(e)) && inScope(e, selectedYears, regions, cats)) {
    const g = group(e); if (g) g.issues.add('DUPLICATE_EVIDENCE_CONFLICT');
  }
  for (const raw of coverage) {
    const r = manifestRow(raw);
    if (inScope(r, selectedYears, regions, cats)) { const g = group(r); if (g) g.manifests.push({ ...raw, ...r }); }
  }
  if (groups.size > MAX_PATTERN_GROUPS) throw Error('PATTERN_GROUP_LIMIT');
  let scopeCells = 0;
  for (const g of groups.values()) {
    const count = selectedYears.length || new Set([...g.rows, ...g.evidence, ...g.manifests].map(r => r.fiscal_year)).size;
    if (count > MAX_PATTERN_YEARS) throw Error('PATTERN_YEAR_LIMIT');
    scopeCells += count;
    if (scopeCells > MAX_PATTERN_SCOPE_CELLS) throw Error('PATTERN_SCOPE_CELL_LIMIT');
  }
  const result = [];
  for (const g of groups.values()) {
    const population = new Map();
    for (const p of g.rows) {
      const key = projectKey(p), old = population.get(key);
      if (old && old.budget_amount !== p.budget_amount) g.issues.add('DUPLICATE_PROJECT_CONFLICT');
      if (!old || p.best_match_score > old.best_match_score ||
        (p.best_match_score === old.best_match_score && fingerprint(p) < fingerprint(old))) population.set(key, p);
    }
    const observedYears = unique([...g.rows, ...g.evidence, ...g.manifests].map(r => r.fiscal_year));
    const scopeYears = selectedYears.length ? selectedYears : observedYears;
    const completeYears = [], coverageReasons = [];
    for (const y of scopeYears) {
      const ps = g.rows.filter(r => r.fiscal_year === y), es = g.evidence.filter(r => r.fiscal_year === y);
      const ms = g.manifests.filter(r => r.fiscal_year === y);
      const ids = unique([...ps, ...es, ...ms].map(r => r.analysis_id).filter(Boolean));
      const keys = new Set(ps.map(r => r.budget_project_identity));
      const manifest = ms.length === 1 ? ms[0] : null;
      const verified = manifest && manifest.status === 'complete' && manifest.source_population_verified === true &&
        manifest.analysis_complete === true && manifest.population_scope === 'FULL_FISCAL_YEAR_ORG_CATEGORY' &&
        text(manifest.evidence_ref) && manifest.analysis_id && ids.length === 1 && ids[0] === manifest.analysis_id &&
        [...ps, ...es].every(r => r.analysis_id === manifest.analysis_id) &&
        Number.isSafeInteger(manifest.expected_project_count) && manifest.expected_project_count >= 0 &&
        manifest.expected_project_count === keys.size && Number.isSafeInteger(manifest.expected_match_count) &&
        manifest.expected_match_count >= 0 && manifest.expected_match_count === es.length && es.every(r => keys.has(r.budget_project_identity)) && !g.issues.size;
      if (verified) completeYears.push(y);
      else coverageReasons.push({ fiscal_year: y, reason: !ms.length ? 'SOURCE_POPULATION_UNVERIFIED' :
        ms.length !== 1 ? 'AMBIGUOUS_COVERAGE_MANIFEST' : ids.length > 1 ? 'MULTIPLE_ANALYSIS_SNAPSHOTS' : 'COVERAGE_MANIFEST_MISMATCH' });
    }
    const complete = scopeYears.length > 0 && completeYears.length === scopeYears.length;
    const best = new Map();
    for (const e of g.evidence) { const key = projectKey(e), old = best.get(key); if (!old || e.score > old.score ||
      (e.score === old.score && e.level === 'HIGH' && old.level !== 'HIGH')) best.set(key, e); }
    const high = g.evidence.filter(r => r.score >= minScore);
    const highByProject = new Map();
    for (const e of high) {
      const key = projectKey(e), old = highByProject.get(key);
      if (!old || (money(e.budget_amount) ?? -1n) > (money(old.budget_amount) ?? -1n)) highByProject.set(key, e);
    }
    const highProjects = complete ? [...population.values()].filter(p => (best.get(projectKey(p))?.score ?? 0) >= minScore) : [...highByProject.values()];
    const matchedProjects = complete ? [...population.values()].filter(p => ['HIGH', 'CANDIDATE'].includes(best.get(projectKey(p))?.level)) : [...best.values()];
    const pop = [...population.values()], highCount = highProjects.length, matchedCount = matchedProjects.length;
    const missingShopping = high.filter(r => r.shopping_amount === null || r.shopping_missing_amounts > 0).length;
    const observedShopping = sum(high, 'shopping_amount');
    const conflictingEvidence = g.issues.has('DUPLICATE_EVIDENCE_CONFLICT');
    const matchedBudget = conflictingEvidence || missingAmount(highProjects, 'budget_amount') ? null : sum(highProjects, 'budget_amount');
    const actualShopping = missingShopping || conflictingEvidence ? null : observedShopping;
    const lags = high.map(r => r.lag_days).filter(v => v !== null && v !== undefined && Number.isSafeInteger(Number(v)) && Number(v) >= 0).map(Number);
    const signals = new Map();
    for (const r of high) for (const s of new Set(r.shared_signals)) signals.set(s, (signals.get(s) || 0) + 1);
    const evidenceYears = unique([...g.rows, ...g.evidence].map(r => r.fiscal_year));
    result.push({
      org_name: [...g.names].sort(cmp)[0], region_name: g.region, category: g.category,
      historical_org_names: [...g.names].sort(cmp), organization_lineage_applied: false,
      organization_lineage_basis: { SOURCE_ORG: g.rows.length + g.evidence.length },
      scope: { fiscal_years: scopeYears, region: g.region, categories: [g.category], source_start: SOURCE_START },
      evidence_years: evidenceYears, population_complete_years: completeYears,
      verified_empty_population_years: completeYears.filter(y => !evidenceYears.includes(y)),
      verified_population_scopes: g.manifests.filter(m => completeYears.includes(m.fiscal_year)).map(m => ({
        fiscal_year: m.fiscal_year, category: m.budget_category, analysis_id: m.analysis_id,
        expected_project_count: m.expected_project_count, expected_match_count: m.expected_match_count,
        evidence_ref: m.evidence_ref })).sort((a, b) => a.fiscal_year - b.fiscal_year),
      evidence_only_years: evidenceYears.filter(y => !completeYears.includes(y)),
      missing_population_years: scopeYears.filter(y => !completeYears.includes(y)),
      population_complete: complete, population_status: complete ? 'VERIFIED' : pop.length ? 'INCOMPLETE_OR_UNVERIFIED' : 'MISSING',
      coverage_reasons: coverageReasons, population_issues: [...g.issues].sort(cmp),
      historical_budget_projects: complete ? pop.length : null,
      historical_budget_amount: complete && !missingAmount(pop, 'budget_amount') ? sum(pop, 'budget_amount') : null,
      observed_budget_projects: pop.length, observed_budget_amount: sum(pop, 'budget_amount'),
      missing_budget_amounts: pop.filter(r => r.budget_amount === null).length,
      high_match_rows: high.length, candidate_match_rows: g.evidence.filter(r => r.level === 'CANDIDATE').length,
      high_matched_budget_projects: highCount, matched_budget_projects: matchedCount,
      candidate_matched_budget_projects: Math.max(0, matchedCount - highCount),
      high_match_project_rate: complete ? exactRatio(highCount, pop.length) : null,
      matched_project_rate: complete ? exactRatio(matchedCount, pop.length) : null,
      high_matched_shopping_rows: high.length, matched_budget_amount: matchedBudget,
      actual_shopping_amount: actualShopping, observed_shopping_amount: observedShopping, missing_shopping_amounts: missingShopping,
      shopping_to_budget_amount_ratio: exactRatio(actualShopping, matchedBudget),
      average_nonnegative_lag_days: lags.length ? exactRatio(lags.reduce((a, b) => a + BigInt(b), 0n), lags.length, 1) : null,
      signal_counts: Object.fromEntries([...signals].sort((a, b) => b[1] - a[1] || cmp(a[0], b[0]))),
      budget_categories: [g.category], shopping_categories: unique(high.map(r => text(r.shopping_category)).filter(Boolean)),
      pattern_basis: complete ? 'VERIFIED_SOURCE_POPULATION_AND_MATCH_EVIDENCE' : 'OBSERVED_MATCH_EVIDENCE_WITHOUT_VERIFIED_DENOMINATOR',
      pattern_version: PATTERN_VERSION, analysis_ids: unique([...g.rows, ...g.evidence, ...g.manifests].map(r => r.analysis_id).filter(Boolean)),
      warning: 'Historical evidence strength only, not a procurement probability, planned purchase, or proof of budget funding.',
    });
  }
  result.sort((a, b) => b.high_matched_budget_projects - a.high_matched_budget_projects ||
    (b.historical_budget_projects ?? -1) - (a.historical_budget_projects ?? -1) ||
    cmp(money(b.actual_shopping_amount) ?? -1n, money(a.actual_shopping_amount) ?? -1n) ||
    cmp(b.org_name, a.org_name) || cmp(a.region_name, b.region_name) || cmp(a.category, b.category));
  const checkOutput = snapshotByteGuard('PATTERN_OUTPUT');
  for (const pattern of result) checkOutput(pattern);
  return result.slice(0, Math.max(1, Math.min(Number(limit) || 200, 1000)));
}

// Coverage-only years are known history only with the same scoped attestation
// required for a populated year, and explicit zero project/match counts. Neither
// an unverified empty input nor a boolean/year-list assertion proves that zero.
export function verifiedPatternPopulationYears(pattern = {}) {
  const scopeYears = yearsOf(pattern.scope?.fiscal_years), evidenceYears = yearsOf(pattern.evidence_years);
  if (pattern.scope?.categories?.length !== 1 || pattern.scope.categories[0] !== pattern.category) return [];
  const scopes = Array.isArray(pattern.verified_population_scopes) ? pattern.verified_population_scopes : [];
  return yearsOf(pattern.population_complete_years).filter(y => {
    if (!scopeYears.includes(y)) return false;
    const matches = scopes.filter(s => s.fiscal_year === y && s.category === pattern.category);
    if (matches.length !== 1) return false;
    const s = matches[0];
    return Boolean(text(s.analysis_id) && text(s.evidence_ref) &&
      Number.isSafeInteger(s.expected_project_count) && s.expected_project_count >= 0 &&
      Number.isSafeInteger(s.expected_match_count) && s.expected_match_count >= 0 &&
      (evidenceYears.includes(y) || (s.expected_project_count === 0 && s.expected_match_count === 0)));
  });
}

export function patternHistoryYears({ targetFiscalYear, window = 2, patterns = [], region = '', categories = TARGETS } = {}) {
  if (patterns.length > MAX_PATTERN_GROUPS) throw Error('PATTERN_GROUP_LIMIT');
  const checkBytes = snapshotByteGuard('PATTERN_HISTORY_INPUT');
  for (const pattern of patterns) checkBytes(pattern);
  const target = year(targetFiscalYear), size = Math.max(1, Math.min(Number(window) || 2, 5));
  const result = { target_fiscal_year: target, years: [], population_years: [], evidence_only_years: [], source_start: SOURCE_START,
    coverage_scope: 'PROVIDED_ORGANIZATION_CATEGORY_PATTERNS_ONLY' };
  if (!target) throw Error('TARGET_FISCAL_YEAR_REQUIRED');
  if (target <= 1) return { ...result, basis: 'NO_PRE_TARGET_YEAR' };
  if (target <= 2026) return { ...result, basis: 'NO_PRE_TARGET_HISTORY_WITHIN_SOURCE_START' };
  const r = canonicalRegion(region);
  if (text(region) && !r) return { ...result, basis: 'REGION_UNRESOLVED' };
  const members = r ? regionHistoryMembers(r) : [], cats = categories.map(v => text(v).toUpperCase());
  const selected = patterns.filter(p => (!members.length || members.includes(canonicalRegion(p.region_name))) && cats.includes(p.category));
  const histories = selected.map(p => ({ observed: yearsOf(p.evidence_years), scoped: yearsOf(p.scope?.fiscal_years),
    complete: verifiedPatternPopulationYears(p) }));
  const available = unique(histories.flatMap(p => [...p.observed, ...p.complete]).filter(y => y < target)).slice(-size);
  const populations = available.filter(y => { const ps = histories.filter(p => p.scoped.includes(y) || p.observed.includes(y) || p.complete.includes(y));
    return ps.length > 0 && ps.every(p => p.complete.includes(y)); });
  return { ...result, years: available, population_years: populations,
    evidence_only_years: available.filter(y => !populations.includes(y)),
    basis: available.length ? 'LATEST_AVAILABLE_PRE_TARGET_YEARS' : 'NO_STORED_PRE_TARGET_HISTORY' };
}

// Used by future evidence: only source-name aliases within an explicit region.
export function directPatternOrganizationKeys(row) {
  const region = canonicalRegion(row.region_name || row.budget_region);
  const name = row.org_name || row.institution_name || row.budget_org;
  if (!region || (canonicalRegion(name) && canonicalRegion(name) !== region)) return [];
  return [...orgAliases(name, region)].map(alias => json([region, alias]));
}
export function directPatternOrganizationMatch(row, pattern) {
  const b = new Set(directPatternOrganizationKeys({ org_name: pattern.org_name, region_name: pattern.region_name }));
  return directPatternOrganizationKeys(row).some(alias => b.has(alias));
}
