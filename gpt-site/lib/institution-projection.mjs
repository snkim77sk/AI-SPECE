import {institutionLineage,LINEAGE_VERSION} from './institution-lineage.mjs';
import { canonicalRegion } from './g2b-domain.mjs';
import { integer, norm } from './matching.mjs';
import { _SIGNAL_TERMS } from './matching-constants.mjs';

// Pure projection of one frozen matching snapshot. No database or provider I/O.
export const PATTERN_PROJECTION_VERSION = 'institution-projection-v2-lineage';
export const PATTERN_SOURCE_START = '2026-01-01';
export const PATTERN_MONEY_DIGITS = 72;
export const PATTERN_EVENT_BYTES = 65536;
export const PATTERN_DOCUMENT_BYTES = 49152;
export const PATTERN_SIGNAL_KEYS = Object.freeze(Object.keys(_SIGNAL_TERMS).sort());
const encoder = new TextEncoder();
const targets = new Set(['LIGHTING', 'POLE']);
const organizationBases = ['EXACT_ORG_NAME', 'ORG_ALIAS_MATCH', 'ADMIN_TRANSITION_ORG_MATCH', 'ADMIN_PARENT_WARD_SCOPE_CANDIDATE', 'ADMIN_TOP_LEVEL_MERGE_MATCH'];
const own = (o, k) => Object.hasOwn(o, k);
export const utf8Bytes = value => encoder.encode(String(value)).byteLength;
export function patternText(value, label, maxBytes = 8192, required = true) {
  if (typeof value !== 'string' || (required && !value) || utf8Bytes(value) > maxBytes || value.includes('\0')) throw Error('PATTERN_INVALID_' + label);
  return value;
}
export function patternMoney(value) {
  if (typeof value === 'string' && value.length > PATTERN_MONEY_DIGITS + 24) throw Error('PATTERN_MONEY_DIGIT_LIMIT');
  const n = integer(value, { missing: null });
  if (n === null) return null;
  const result = n.toString();
  if (result.replace('-', '').length > PATTERN_MONEY_DIGITS) throw Error('PATTERN_MONEY_DIGIT_LIMIT');
  return result;
}
function safeCount(v, label) {
  if (!Number.isSafeInteger(v) || v < 0) throw Error('PATTERN_INVALID_' + label);
  return v;
}
function date(v, label) {
  patternText(v, label, 10);
  const n = Date.parse(v + 'T00:00:00Z');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(v) || !Number.isFinite(n) || new Date(n).toISOString().slice(0, 10) !== v) throw Error('PATTERN_INVALID_' + label);
  return v;
}
export function parsePatternDocument(value) {
  if (typeof value !== 'string' || utf8Bytes(value) > PATTERN_DOCUMENT_BYTES) throw Error('PATTERN_DOCUMENT_SIZE_OR_TYPE');
  let out;
  try { out = JSON.parse(value); } catch { throw Error('PATTERN_DOCUMENT_JSON'); }
  if (!out || Array.isArray(out) || typeof out !== 'object') throw Error('PATTERN_DOCUMENT_OBJECT_REQUIRED');
  return out;
}
function analysis(value) { return patternText(value, 'ANALYSIS_ID', 256); }
function projectDocument(row, relationalProjectKey = row.source_key) {
  const p = parsePatternDocument(row.project_json);
  const key = patternText(relationalProjectKey, 'PROJECT_KEY');
  if (p.source_key !== key) throw Error('PATTERN_PROJECT_ID_CONFLICT');
  const hash = patternText(p.payload_hash, 'PAYLOAD_HASH', 128);
  const org = patternText(p.org_name, 'ORG', 1024, false).trim();
  const regionRaw = patternText(p.region_name, 'REGION', 256, false).trim();
  const region = canonicalRegion(regionRaw), orgRegion = canonicalRegion(org);
  if (orgRegion && region && orgRegion !== region) throw Error('PATTERN_ORGANIZATION_REGION_CONFLICT');
  const category = patternText(p.primary_category, 'CATEGORY', 64, false).toUpperCase();
  if (!Number.isSafeInteger(p.fiscal_year) || p.fiscal_year < 0 || p.fiscal_year > 9999) throw Error('PATTERN_INVALID_YEAR');
  const snapshot = date(p.snapshot_date, 'BUDGET_DATE');
  const excluded = [];
  if (!org || !norm(org)) excluded.push('ORGANIZATION_MISSING');
  if (!region) excluded.push('REGION_UNRESOLVED');
  if (!targets.has(category)) excluded.push('BUDGET_CATEGORY_UNSUPPORTED');
  if (p.fiscal_year < 2026) excluded.push('FISCAL_YEAR_BEFORE_SOURCE_START');
  if (snapshot < PATTERN_SOURCE_START) excluded.push('BUDGET_DATE_BEFORE_SOURCE_START');
  return { source_key: key, payload_hash: hash, org_name: org, region_name: regionRaw,
    region_key: region, org_key: norm(org), primary_category: category, fiscal_year: p.fiscal_year,
    snapshot_date: snapshot, project_name: patternText(p.project_name??'', 'PROJECT_NAME', 16384, false), budget_amount: patternMoney(p.budget_amount), appropriation_amount: patternMoney(p.appropriation_amount),
    exclusion_reasons: excluded };
}
function lineageDocument(p,asOfDate){const l=institutionLineage({org:p.org_name,region:p.region_name,projectText:p.project_name,sourceDate:p.snapshot_date,asOfDate:asOfDate||p.snapshot_date});return{...p,org_name:l.org_name,region_name:l.region_key,region_key:l.region_key,org_key:l.org_key,source_org_name:p.org_name,source_region_name:p.region_name,source_project_name:p.project_name,source_snapshot_date:p.snapshot_date,lineage_as_of:l.as_of_date,lineage_version:l.version,lineage_basis:l.basis,lineage_applied:l.applied,lineage_unresolved:l.unresolved};}
function event(kind, sourceKey, p, document) {
  const result = { kind, source_event_key: sourceKey, region_key: p.region_key, org_key: p.org_key,
    category: p.primary_category, fiscal_year: p.fiscal_year, budget_key: p.source_key,
    kind_order: kind === 'PROJECT' ? 0 : 1, row_json: JSON.stringify(document), eligible: document.exclusion_reasons.length ? 0 : 1 };
  if (utf8Bytes(result.row_json) > PATTERN_DOCUMENT_BYTES || utf8Bytes(JSON.stringify(result)) > PATTERN_EVENT_BYTES) throw Error('PATTERN_EVENT_SIZE_LIMIT');
  return result;
}

/** Input: {source_key,project_json}. project_json is a STRING produced from the
 * matching_budgets frozen doc by projectDocumentSQL. No current-budget join.
 * Unsupported scope returns eligible=0 with explicit reasons, not a skipped row. */
export function projectEvent(row, { analysisId, asOfDate } = {}) {
  const p = lineageDocument(projectDocument(row),asOfDate);
  return event('PROJECT', p.source_key, p, { version: PATTERN_PROJECTION_VERSION, analysis_id: analysis(analysisId), ...p });
}

/** Input: {request_key,budget_key,score,level,project_json,evidence_json,
 * request_record_key,request_integrity,request_missing_amounts}. JSON inputs are
 * STRINGs from the helpers below. request_* provenance comes from matching_requests.
 * The source request winner is immutable; no institution-local reassignment. */
export function requestEvent(row, { analysisId, asOfDate } = {}) {
  const requestKey = patternText(row.request_key, 'REQUEST_KEY'), p = projectDocument(row, row.budget_key);
  if (row.request_record_key !== requestKey) throw Error('PATTERN_REQUEST_RECORD_MISSING_OR_CONFLICT');
  if (!['VALID', 'EXCLUDED_CONFLICT'].includes(row.request_integrity)) throw Error('PATTERN_REQUEST_INTEGRITY_UNKNOWN');
  const missing = safeCount(row.request_missing_amounts, 'MISSING_AMOUNT_COUNT');
  const e = parsePatternDocument(row.evidence_json);
  const score = safeCount(row.score, 'SCORE');
  if (score > 100 || !['HIGH', 'CANDIDATE'].includes(row.level)) throw Error('PATTERN_INVALID_SCORE_OR_LEVEL');
  if (e.score !== score || e.level !== row.level) throw Error('PATTERN_RESULT_SCORE_CONFLICT');
  if (e.shopping_source_key !== requestKey || e.budget_project_identity !== p.source_key) throw Error('PATTERN_RESULT_ID_CONFLICT');
  if (e.budget_payload_hash !== p.payload_hash) throw Error('PATTERN_PROJECT_HASH_CONFLICT');
  if (e.budget_year !== p.fiscal_year || e.budget_org !== p.org_name || canonicalRegion(e.budget_region) !== p.region_key) throw Error('PATTERN_PROJECT_SCOPE_CONFLICT');
  if (safeCount(e.shopping_missing_amounts, 'EVIDENCE_MISSING_AMOUNT_COUNT') !== missing) throw Error('PATTERN_MISSING_AMOUNT_CONFLICT');
  const budget = patternMoney(e.budget_amount), expectedBudget = p.budget_amount ?? p.appropriation_amount;
  if (budget !== expectedBudget) throw Error('PATTERN_PROJECT_AMOUNT_CONFLICT');
  const shopping = patternMoney(e.shopping_amount);
  const shoppingDate = date(e.shopping_date, 'SHOPPING_DATE');
  const requestNo = patternText(e.shopping_delivery_req_no ?? '', 'REQUEST_NO', 8192, false);
  if (requestNo && requestKey !== 'REQUEST:' + requestNo) throw Error('PATTERN_REQUEST_NUMBER_CONFLICT');
  if (!Array.isArray(e.shared_signals) || e.shared_signals.length > 64 || e.shared_signals.some(s => !PATTERN_SIGNAL_KEYS.includes(s))) throw Error('PATTERN_UNKNOWN_SIGNAL');
  const signals = [...new Set(e.shared_signals)].sort();
  if (e.lag_days !== null && (!Number.isSafeInteger(e.lag_days) || Math.abs(e.lag_days) > 3660000)) throw Error('PATTERN_INVALID_LAG');
  const shoppingCategory = patternText(e.shopping_category, 'SHOPPING_CATEGORY', 64);
  if (!['LIGHTING', 'POLE', 'MIXED_TARGET'].includes(shoppingCategory)) throw Error('PATTERN_SHOPPING_CATEGORY_UNSUPPORTED');
  const basis = patternText(e.organization_basis, 'ORGANIZATION_BASIS', 128);
  if (!organizationBases.includes(basis)) throw Error('PATTERN_ORGANIZATION_BASIS_UNSUPPORTED');
  const excluded = [...p.exclusion_reasons];
  if (row.request_integrity !== 'VALID') excluded.push('REQUEST_INTEGRITY_EXCLUDED');
  if (shoppingDate < PATTERN_SOURCE_START) excluded.push('SHOPPING_DATE_BEFORE_SOURCE_START');
  const mapped=lineageDocument(p,asOfDate);
  return event('REQUEST', requestKey, mapped, { ...mapped, version: PATTERN_PROJECTION_VERSION, analysis_id: analysis(analysisId),
    source_key: requestKey, budget_key: p.source_key, budget_payload_hash: p.payload_hash,
    org_name: mapped.org_name, region_name: mapped.region_name, region_key: mapped.region_key, org_key: mapped.org_key,
    primary_category: p.primary_category, fiscal_year: p.fiscal_year,
    budget_amount: budget, budget_amount_basis: p.budget_amount !== null ? 'SOURCE_BUDGET_AMOUNT' : p.appropriation_amount !== null ? 'SOURCE_APPROPRIATION_FALLBACK' : 'MISSING',
    shopping_source_key: requestKey, shopping_delivery_req_no: requestNo, shopping_date: shoppingDate,
    shopping_category: shoppingCategory, shopping_amount: shopping, shopping_missing_amounts: missing,
    score, level: row.level, shared_signals: signals, lag_days: e.lag_days, organization_basis: basis,
    exclusion_reasons: excluded });
}

function alias(value) { if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(value)) throw Error('PATTERN_SQL_ALIAS'); return value; }
function fieldsSQL(a, names) { return `json_object(${names.map(k => `'${k}',json_extract(${a}.row_json,'$.${k}')`).join(',')})`; }
export const PATTERN_PROJECT_FIELDS = Object.freeze(['source_key', 'payload_hash', 'org_name', 'region_name', 'primary_category', 'fiscal_year', 'snapshot_date', 'project_name', 'budget_amount', 'appropriation_amount']);
export const PATTERN_EVIDENCE_FIELDS = Object.freeze(['budget_project_identity', 'budget_payload_hash', 'budget_year', 'budget_org', 'budget_region', 'budget_amount', 'shopping_source_key', 'shopping_delivery_req_no', 'shopping_date', 'shopping_category', 'shopping_amount', 'shopping_missing_amounts', 'score', 'level', 'shared_signals', 'lag_days', 'organization_basis']);
export function projectDocumentSQL(tableAlias = 'b') { return fieldsSQL(alias(tableAlias), PATTERN_PROJECT_FIELDS); }
export function requestEvidenceSQL(tableAlias = 'r') { return fieldsSQL(alias(tableAlias), PATTERN_EVIDENCE_FIELDS); }

/** Validate a stored compact event again at the trust boundary before aggregation.
 * Global request uniqueness is the D1 events PK's job, not an unbounded JS set. */
export function readPatternEvent(raw, analysisId) {
  if (!raw || typeof raw !== 'object' || !['PROJECT', 'REQUEST'].includes(raw.kind)) throw Error('PATTERN_EVENT_KIND');
  if (utf8Bytes(JSON.stringify(raw)) > PATTERN_EVENT_BYTES) throw Error('PATTERN_EVENT_SIZE_LIMIT');
  const d = parsePatternDocument(raw.row_json);
  patternText(d.org_name, 'ORG', 1024, false); patternText(d.region_name, 'REGION', 256, false);
  if (d.version !== PATTERN_PROJECTION_VERSION || d.analysis_id !== analysis(analysisId)) throw Error('PATTERN_EVENT_CONTEXT');
  if(d.snapshot_date!==d.source_snapshot_date||d.project_name!==d.source_project_name)throw Error('PATTERN_LINEAGE_SOURCE_CONFLICT');
  const lineage=institutionLineage({org:d.source_org_name,region:d.source_region_name,projectText:d.source_project_name,sourceDate:d.source_snapshot_date,asOfDate:d.lineage_as_of});
  if(d.lineage_version!==LINEAGE_VERSION||d.lineage_basis!==lineage.basis||d.lineage_applied!==lineage.applied||d.lineage_unresolved!==lineage.unresolved||d.org_name!==lineage.org_name||d.region_key!==lineage.region_key||d.org_key!==lineage.org_key)throw Error('PATTERN_LINEAGE_CONFLICT');
  const key = patternText(raw.source_event_key, 'EVENT_KEY');
  const budget = patternText(raw.budget_key, 'PROJECT_KEY');
  if (key !== d.source_key || budget !== (raw.kind === 'PROJECT' ? d.source_key : d.budget_key)) throw Error('PATTERN_EVENT_ID_CONFLICT');
  if (raw.kind_order !== (raw.kind === 'PROJECT' ? 0 : 1)) throw Error('PATTERN_EVENT_KIND_ORDER');
  if (raw.region_key !== d.region_key || raw.org_key !== d.org_key || raw.category !== d.primary_category || raw.fiscal_year !== d.fiscal_year ||
    canonicalRegion(d.region_name) !== raw.region_key || norm(d.org_name) !== raw.org_key) throw Error('PATTERN_EVENT_SCOPE_CONFLICT');
  if (!Array.isArray(d.exclusion_reasons) || d.exclusion_reasons.length > 8 || d.exclusion_reasons.some(v => typeof v !== 'string' || v.length > 80) ||
    raw.eligible !== (d.exclusion_reasons.length ? 0 : 1)) throw Error('PATTERN_EVENT_ELIGIBILITY_CONFLICT');
  if (raw.eligible && (!raw.region_key || !raw.org_key || !targets.has(raw.category) || !Number.isSafeInteger(raw.fiscal_year) || raw.fiscal_year < 2026)) throw Error('PATTERN_EVENT_ELIGIBLE_SCOPE');
  if (raw.kind === 'PROJECT') {
    patternText(d.payload_hash, 'PAYLOAD_HASH', 128); date(d.snapshot_date, 'BUDGET_DATE');
    if (raw.eligible && d.snapshot_date < PATTERN_SOURCE_START) throw Error('PATTERN_EVENT_ELIGIBLE_SCOPE');
    if (patternMoney(d.budget_amount) !== d.budget_amount || patternMoney(d.appropriation_amount) !== d.appropriation_amount) throw Error('PATTERN_EVENT_MONEY');
  } else {
    date(d.shopping_date, 'SHOPPING_DATE'); patternText(d.budget_payload_hash, 'PAYLOAD_HASH', 128);
    const requestNo = patternText(d.shopping_delivery_req_no, 'REQUEST_NO', 8192, false);
    if (requestNo && key !== 'REQUEST:' + requestNo) throw Error('PATTERN_REQUEST_NUMBER_CONFLICT');
    if (!organizationBases.includes(d.organization_basis)) throw Error('PATTERN_ORGANIZATION_BASIS_UNSUPPORTED');
    if (!['SOURCE_BUDGET_AMOUNT', 'SOURCE_APPROPRIATION_FALLBACK', 'MISSING'].includes(d.budget_amount_basis)) throw Error('PATTERN_AMOUNT_BASIS_UNSUPPORTED');
    if (raw.eligible && d.shopping_date < PATTERN_SOURCE_START) throw Error('PATTERN_EVENT_ELIGIBLE_SCOPE');
    if (d.shopping_source_key !== key) throw Error('PATTERN_EVENT_ID_CONFLICT');
    if (!own(d, 'lag_days') || (d.lag_days !== null && (!Number.isSafeInteger(d.lag_days) || Math.abs(d.lag_days) > 3660000))) throw Error('PATTERN_INVALID_LAG');
    if (!Number.isSafeInteger(d.score) || d.score < 0 || d.score > 100 || !['HIGH', 'CANDIDATE'].includes(d.level)) throw Error('PATTERN_INVALID_SCORE_OR_LEVEL');
    safeCount(d.shopping_missing_amounts, 'MISSING_AMOUNT_COUNT');
    if (!Array.isArray(d.shared_signals) || d.shared_signals.length > PATTERN_SIGNAL_KEYS.length || d.shared_signals.some(s => !PATTERN_SIGNAL_KEYS.includes(s)) || new Set(d.shared_signals).size !== d.shared_signals.length) throw Error('PATTERN_UNKNOWN_SIGNAL');
    if (patternMoney(d.shopping_amount) !== d.shopping_amount || patternMoney(d.budget_amount) !== d.budget_amount) throw Error('PATTERN_EVENT_MONEY');
    if (!['LIGHTING', 'POLE', 'MIXED_TARGET'].includes(d.shopping_category)) throw Error('PATTERN_SHOPPING_CATEGORY_UNSUPPORTED');
  }
  return d;
}
