import { integer, norm } from './matching.mjs';
import { _SIGNAL_TERMS } from './matching-constants.mjs';
import { directPatternOrganizationKeys, directPatternOrganizationMatch, exactRatio, SOURCE_START,
  snapshotByteGuard, verifiedPatternPopulationYears } from './institution-patterns.mjs';

export const FUTURE_EVIDENCE_VERSION = 'future-sales-evidence-v5-verified-empty';
const text = v => String(v ?? '').trim();
const years = values => [...new Set((values || []).map(Number).filter(Number.isSafeInteger))].sort((a, b) => a - b);
const cmp = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const amount = v => integer(v, { missing: null });
export function futureRowSignals(row) {
  const value = ['project_name', 'field_name', 'section_name', 'account_name'].map(k => text(row[k])).join(' ').toLowerCase();
  return Object.entries(_SIGNAL_TERMS).filter(([, terms]) => terms.some(term => value.includes(term))).map(([key]) => key).sort(cmp);
}
function empty(row, level, basis, reasons = []) {
  return { ...row, historical_evidence_score: 0, historical_evidence_level: level, historical_evidence_reasons: reasons,
    historical_pattern_org: '', historical_high_projects: 0, historical_total_budget_projects: null,
    historical_high_match_project_rate: null, historical_matched_project_rate: null,
    historical_population_complete: false, historical_actual_shopping_amount: null,
    historical_shopping_to_budget_amount_ratio: null, historical_average_lag_days: null,
    historical_evidence_years: [], historical_population_complete_years: [], historical_evidence_only_years: [],
    historical_verified_empty_population_years: [],
    historical_evidence_model_version: FUTURE_EVIDENCE_VERSION, historical_pattern_match_basis: basis,
    historical_pattern_historical_org_names: [], historical_organization_lineage_applied: false,
    historical_shared_signals: [], historical_source_start: SOURCE_START,
    historical_evidence_semantics: 'EVIDENCE_STRENGTH_NOT_PROCUREMENT_PROBABILITY', confirmed_procurement: false,
    structural_budget_only: row.source_layer === 'APPROPRIATION' || row.service === 'AIDFA' };
}

/** Original point weights, stricter provenance. Requires a category-specific
 * institutionPatterns result with explicit pre-target years and region/org match.
 * A mixed/current/future history aggregate is rejected rather than partially
 * filtering already-aggregated counts. Recompute the pattern for requested years.
 * No original `organization_lineage` port is implied: unsupported lineage remains
 * separate, including a parent government that still exists after ward creation.
 */
export function scoreFutureBudgetEvidence(row = {}, pattern = null, { patternMatchBasis = 'DIRECT_PATTERN_ARGUMENT' } = {}) {
  const checkBytes = snapshotByteGuard('FUTURE_EVIDENCE_INPUT');
  checkBytes(row); if (pattern) checkBytes(pattern);
  return scoreBoundedFutureBudgetEvidence(row, pattern, patternMatchBasis);
}
function scoreBoundedFutureBudgetEvidence(row, pattern, patternMatchBasis) {
  const item = { ...row }, category = text(item.primary_category || item.category).toUpperCase(), target = Number(item.fiscal_year);
  const none = (level, reasons) => empty(item, level, patternMatchBasis, reasons);
  if (!['LIGHTING', 'POLE'].includes(category)) return none('OUTSIDE_LED_POLE', []);
  if (!Number.isSafeInteger(target) || target <= 0) return none('NO_HISTORY', ['TARGET_FISCAL_YEAR_MISSING']);
  if (target <= 2026) return none('NO_HISTORY', ['NO_PRE_TARGET_HISTORY_WITHIN_SOURCE_START']);
  if (!pattern) return none('NO_HISTORY', []);
  const historyYears = years(pattern.evidence_years), scopeYears = years(pattern.scope?.fiscal_years);
  const completeYears = verifiedPatternPopulationYears(pattern);
  if ((!historyYears.length && !completeYears.length) || historyYears.some(y => y < 2026 || y >= target) || scopeYears.some(y => y < 2026 || y >= target))
    return none('NO_HISTORY', ['HISTORY_OUTSIDE_PRE_TARGET_SOURCE_SCOPE']);
  if (pattern.category !== category || pattern.scope?.categories?.length !== 1 || pattern.scope.categories[0] !== category)
    return none('NO_HISTORY', ['CATEGORY_SPECIFIC_HISTORY_REQUIRED']);
  if (!directPatternOrganizationMatch(item, pattern) || text(patternMatchBasis).startsWith('LINEAGE:') || pattern.organization_lineage_applied)
    return none('NO_HISTORY', ['SUPPORTED_ORGANIZATION_EVIDENCE_REQUIRED']);
  const org = norm(item.org_name || item.institution_name).replace(/청$/, '');
  if ((norm(item.region_name).startsWith('인천') && /^(?:인천광역시|인천)?(?:중구|서구|동구)$/.test(org)) ||
    ['광주광역시', '전라남도'].includes(org))
    return none('NO_HISTORY', ['RETIRED_ORGANIZATION_LINEAGE_UNRESOLVED']);
  const highProjects = Number(pattern.high_matched_budget_projects || 0), matchedProjects = Number(pattern.matched_budget_projects || 0);
  if (!Number.isSafeInteger(highProjects) || !Number.isSafeInteger(matchedProjects) || highProjects < 0 || matchedProjects < highProjects)
    return none('NO_HISTORY', ['INVALID_HISTORICAL_SAMPLE_COUNTS']);
  const populationComplete = pattern.population_complete === true && pattern.population_status === 'VERIFIED' &&
    historyYears.every(y => completeYears.includes(y)) && scopeYears.length > 0 && scopeYears.every(y => completeYears.includes(y)) &&
    Number.isSafeInteger(pattern.historical_budget_projects) && pattern.historical_budget_projects >= matchedProjects;
  const totalProjects = populationComplete ? pattern.historical_budget_projects : null;
  const rate = populationComplete ? exactRatio(highProjects, totalProjects) : null;
  const matchedRate = populationComplete ? exactRatio(matchedProjects, totalProjects) : null;
  const evidenceOnly = historyYears.filter(y => !completeYears.includes(y));
  const history = {
    historical_pattern_org: pattern.org_name,
    historical_high_projects: highProjects, historical_total_budget_projects: totalProjects,
    historical_high_match_project_rate: rate, historical_matched_project_rate: matchedRate,
    historical_population_complete: populationComplete,
    historical_actual_shopping_amount: amount(pattern.actual_shopping_amount)?.toString() ?? null,
    historical_shopping_to_budget_amount_ratio: pattern.shopping_to_budget_amount_ratio ?? null,
    historical_average_lag_days: pattern.average_nonnegative_lag_days ?? null,
    historical_evidence_years: historyYears, historical_population_complete_years: completeYears,
    historical_verified_empty_population_years: completeYears.filter(y => !historyYears.includes(y)),
    historical_evidence_only_years: evidenceOnly,
    historical_pattern_basis: pattern.pattern_basis || '',
    historical_pattern_historical_org_names: [...(pattern.historical_org_names || [])],
    historical_scope: pattern.scope,
  };
  // A verified empty population is known zero, not missing coverage. It supplies
  // no match evidence and therefore receives no organization or year bonus.
  if (highProjects < 1 && matchedProjects < 1)
    return { ...none('NO_HISTORY', ['NO_HISTORICAL_MATCH_EVIDENCE']), ...history };
  let score = 35;
  const reasons = ['HISTORICAL_ORG_MATCH'];
  const matchingCategories = [...(pattern.budget_categories || []), ...(pattern.shopping_categories || [])].map(v => text(v).toUpperCase());
  if (matchingCategories.includes(category)) { score += 20; reasons.push('HISTORICAL_CATEGORY_MATCH'); }
  if (highProjects >= 5) { score += 20; reasons.push('HISTORICAL_SAMPLE_5_PLUS'); }
  else if (highProjects >= 3) { score += 14; reasons.push('HISTORICAL_SAMPLE_3_PLUS'); }
  else if (highProjects >= 1) { score += 6; reasons.push('HISTORICAL_SAMPLE_PRESENT'); }
  if (populationComplete && totalProjects >= 5 && rate !== null) {
    if (rate >= 0.50) { score += 10; reasons.push('HISTORICAL_HIGH_MATCH_RATE_50_PLUS'); }
    else if (rate >= 0.25) { score += 5; reasons.push('HISTORICAL_HIGH_MATCH_RATE_25_PLUS'); }
    else if (rate < 0.10) { score -= 8; reasons.push('HISTORICAL_HIGH_MATCH_RATE_LOW'); }
  }
  if (completeYears.length >= 2) { score += 10; reasons.push('MULTI_YEAR_VERIFIED_POPULATION'); }
  else if (evidenceOnly.length) reasons.push('PARTIAL_YEAR_EVIDENCE_NO_MULTI_YEAR_BONUS');
  const shared = futureRowSignals(item).filter(signal => Number(pattern.signal_counts?.[signal]) > 0);
  if (shared.length) { score += Math.min(15, 5 * shared.length); reasons.push('REPEATED_SIGNAL:' + shared.join(',')); }
  if (item.source_layer === 'APPROPRIATION' || item.service === 'AIDFA') { score = Math.min(score, 75); reasons.push('AIDFA_STRUCTURAL_CAP'); }
  else score = Math.min(score, 100);
  return { ...empty(item, 'NO_HISTORY', patternMatchBasis), ...history,
    historical_evidence_score: score,
    historical_evidence_level: score >= 70 ? 'STRONG_HISTORY' : score >= 50 ? 'HISTORY_PRESENT' : 'LIMITED_HISTORY',
    historical_evidence_reasons: reasons, historical_shared_signals: shared,
  };
}

export function enrichFutureBudgetRows(rows = [], { patterns = [], limit = null } = {}) {
  if (rows.length > 1000 || patterns.length > 1000) throw Error('FUTURE_EVIDENCE_INPUT_ROW_LIMIT');
  const checkBytes = snapshotByteGuard('FUTURE_EVIDENCE_INPUT');
  for (const values of [rows, patterns]) for (const value of values) checkBytes(value);
  const index = new Map();
  for (let i = 0; i < patterns.length; i++) {
    const p = patterns[i];
    for (const alias of directPatternOrganizationKeys({ org_name: p.org_name, region_name: p.region_name })) {
      const key = JSON.stringify([p.category, alias]);
      if (!index.has(key)) index.set(key, new Set());
      index.get(key).add(i);
    }
  }
  const checkOutput = snapshotByteGuard('FUTURE_EVIDENCE_OUTPUT');
  const scored = rows.map(row => {
    const category = text(row.primary_category || row.category).toUpperCase(), candidates = new Set();
    for (const alias of directPatternOrganizationKeys(row)) {
      for (const i of index.get(JSON.stringify([category, alias])) || []) {
        candidates.add(i); if (candidates.size > 1) break;
      }
      if (candidates.size > 1) break;
    }
    // Unlike the original max-rank alias index, never choose one of several
    // plausible institutions or overlapping snapshots by its larger spend.
    const result = candidates.size > 1 ? empty(row, 'NO_HISTORY', 'AMBIGUOUS_ORGANIZATION_PATTERN', ['AMBIGUOUS_ORGANIZATION_PATTERN']) :
      scoreBoundedFutureBudgetEvidence(row, patterns[candidates.values().next().value] || null,
        candidates.size ? 'DIRECT_ORG_ALIAS' : 'SOURCE_ORG');
    checkOutput(result);
    return result;
  });
  scored.sort((a, b) => b.historical_evidence_score - a.historical_evidence_score ||
    cmp(amount(b.budget_amount ?? b.appropriation_amount) ?? 0n, amount(a.budget_amount ?? a.appropriation_amount) ?? 0n) ||
    cmp(text(b.org_name), text(a.org_name)) || cmp(text(b.project_name), text(a.project_name)) || cmp(text(a.source_key), text(b.source_key)));
  return limit === null ? scored : scored.slice(0, Math.max(1, Number(limit) || 1));
}
