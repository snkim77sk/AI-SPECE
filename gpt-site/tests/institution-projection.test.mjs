import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { projectEvent, requestEvent, readPatternEvent, projectDocumentSQL, requestEvidenceSQL, PATTERN_EVENT_BYTES, utf8Bytes } from '../lib/institution-projection.mjs';
const doc = (over = {}) => ({ source_key: 'P1', payload_hash: 'hash-P1', org_name: '인천광역시 옹진군', region_name: '인천광역시',
  primary_category: 'LIGHTING', fiscal_year: 2026, snapshot_date: '2026-03-01', budget_amount: '100', appropriation_amount: null, ...over });
function input(p = doc(), over = {}) {
  const key = 'REQUEST:R1';
  const e = { budget_project_identity: p.source_key, budget_payload_hash: p.payload_hash, budget_year: p.fiscal_year,
    budget_org: p.org_name, budget_region: p.region_name, budget_amount: p.budget_amount ?? p.appropriation_amount,
    shopping_source_key: key, shopping_delivery_req_no: 'R1', shopping_date: '2026-05-01', shopping_category: 'LIGHTING',
    shopping_amount: '60', shopping_missing_amounts: 0, score: 90, level: 'HIGH', shared_signals: ['LED', 'STREET_LIGHT'],
    lag_days: 10, organization_basis: 'EXACT_ORG_NAME', ...over };
  return { source_key: key, request_key: key, budget_key: p.source_key, score: e.score, level: e.level,
    project_json: JSON.stringify(p), evidence_json: JSON.stringify(e), request_record_key: key, request_integrity: 'VALID', request_missing_amounts: e.shopping_missing_amounts };
}
const pe = p => projectEvent({ source_key: p.source_key, project_json: JSON.stringify(p) }, { analysisId: 'J' });
const re = r => requestEvent(r, { analysisId: 'J' });

test('compact project event freezes source scope and retains exact amount strings', () => {
  const p = doc({ budget_amount: '9007199254740993' }), e = pe(p), d = JSON.parse(e.row_json);
  assert.equal(e.kind, 'PROJECT'); assert.equal(e.kind_order, 0); assert.equal(e.eligible, 1);
  assert.equal(e.org_key, '인천광역시옹진군'); assert.equal(d.budget_amount, '9007199254740993');
  assert.equal(d.analysis_id, 'J'); assert.equal(readPatternEvent(e, 'J').source_key, 'P1');
  assert.ok(utf8Bytes(JSON.stringify(e)) < PATTERN_EVENT_BYTES);
});
test('request wrapper cursor source_key does not replace relational budget_key', () => {
  const r = input(), before = JSON.stringify(r), e = re(r);
  assert.equal(e.kind, 'REQUEST'); assert.equal(e.budget_key, 'P1'); assert.equal(e.source_event_key, 'REQUEST:R1');
  assert.equal(e.kind_order, 1); assert.equal(e.eligible, 1); assert.equal(JSON.stringify(r), before);
});
test('unsupported source scope is explicit in eligible=0 output, not silently removed', () => {
  for (const [over, reason] of [[{ region_name: '' }, 'REGION_UNRESOLVED'], [{ org_name: '' }, 'ORGANIZATION_MISSING'],
    [{ fiscal_year: 2025 }, 'FISCAL_YEAR_BEFORE_SOURCE_START'], [{ snapshot_date: '2025-12-31' }, 'BUDGET_DATE_BEFORE_SOURCE_START'],
    [{ primary_category: 'OTHER' }, 'BUDGET_CATEGORY_UNSUPPORTED']]) {
    const p = doc(over), e = pe(p); assert.equal(e.eligible, 0); assert.ok(JSON.parse(e.row_json).exclusion_reasons.includes(reason));
    assert.equal(re(input(p)).eligible, 0);
  }
  const e = re({ ...input(), request_integrity: 'EXCLUDED_CONFLICT' });
  assert.equal(e.eligible, 0); assert.ok(JSON.parse(e.row_json).exclusion_reasons.includes('REQUEST_INTEGRITY_EXCLUDED'));
  assert.equal(re(input(doc(), { shopping_date: '2025-12-31' })).eligible, 0);
});
test('frozen project joins reject orphan, changed revision, scope and amount conflicts', () => {
  assert.throws(() => re({ ...input(), project_json: null }), /DOCUMENT_SIZE_OR_TYPE/);
  assert.throws(() => re({ ...input(), budget_key: 'wrong' }), /PROJECT_ID_CONFLICT/);
  for (const change of [{ budget_payload_hash: 'new' }, { budget_year: 2027 }, { budget_org: '인천광역시 중구' },
    { budget_region: '부산광역시' }, { budget_amount: '101' }, { budget_project_identity: 'P2' }]) assert.throws(() => re(input(doc(), change)), /CONFLICT/);
});
test('request identity, score and integrity provenance are compared with relational columns', () => {
  assert.throws(() => re({ ...input(), request_record_key: null }), /REQUEST_RECORD_MISSING_OR_CONFLICT/);
  assert.throws(() => re({ ...input(), score: 89 }), /RESULT_SCORE_CONFLICT/);
  assert.throws(() => re({ ...input(), level: 'CANDIDATE' }), /RESULT_SCORE_CONFLICT/);
  assert.throws(() => re({ ...input(), request_missing_amounts: 1 }), /MISSING_AMOUNT_CONFLICT/);
  assert.throws(() => re({ ...input(), request_integrity: 'unknown' }), /INTEGRITY_UNKNOWN/);
  assert.throws(() => re(input(doc(), { shopping_delivery_req_no: 'DIFFERENT' })), /REQUEST_NUMBER_CONFLICT/);
});
test('missing budget is not zero; appropriation fallback is a labeled distinct basis', () => {
  const p = doc({ budget_amount: null, appropriation_amount: '100' });
  assert.equal(JSON.parse(pe(p).row_json).budget_amount, null);
  const d = JSON.parse(re(input(p)).row_json);
  assert.equal(d.budget_amount, '100'); assert.equal(d.budget_amount_basis, 'SOURCE_APPROPRIATION_FALLBACK');
  const zero = doc({ budget_amount: '0', appropriation_amount: '100' });
  assert.equal(JSON.parse(re(input(zero)).row_json).budget_amount, '0');
});
test('missing shopping amount/partial sum preserve evidence while duplicate known signals collapse', () => {
  const e = re(input(doc(), { shopping_amount: null, shopping_missing_amounts: 3, shared_signals: ['LED', 'LED'] }));
  const d = JSON.parse(e.row_json); assert.equal(d.shopping_amount, null); assert.equal(d.shopping_missing_amounts, 3);
  assert.deepEqual(d.shared_signals, ['LED']);
});
test('malformed amounts, fields, date, score, lag and unbounded signals fail closed', () => {
  assert.throws(() => pe(doc({ budget_amount: 9007199254740992 })), /UNSAFE_INTEGER/);
  assert.throws(() => pe(doc({ budget_amount: '9'.repeat(73) })), /MONEY_DIGIT_LIMIT/);
  assert.throws(() => pe(doc({ snapshot_date: '2026-02-30' })), /BUDGET_DATE/);
  assert.throws(() => pe(doc({ region_name: '부산광역시' })), /ORGANIZATION_REGION_CONFLICT/);
  for (const change of [{ score: 101 }, { lag_days: 0.5 }, { shared_signals: ['MADE_UP'] }, { organization_basis: 'LEARNED_WITHOUT_PROOF' }, { shopping_missing_amounts: -1 }])
    assert.throws(() => re(input(doc(), change)), /PATTERN_/);
});
test('row and encoded UTF-8 limits apply before source JSON is parsed', () => {
  assert.throws(() => pe(doc({ org_name: '가'.repeat(400) })), /INVALID_ORG/);
  assert.throws(() => projectEvent({ source_key: 'P1', project_json: ' '.repeat(49153) }, { analysisId: 'J' }), /DOCUMENT_SIZE_OR_TYPE/);
  assert.throws(() => projectEvent({ source_key: 'P1', project_json: '[]' }, { analysisId: 'J' }), /OBJECT_REQUIRED/);
});
test('read-back detects stored event tampering/context/eligibility mismatch', () => {
  const e = pe(doc());
  assert.throws(() => readPatternEvent(e, 'OTHER'), /EVENT_CONTEXT/);
  assert.throws(() => readPatternEvent({ ...e, budget_key: 'bad' }, 'J'), /EVENT_ID_CONFLICT/);
  assert.throws(() => readPatternEvent({ ...e, org_key: 'other' }, 'J'), /EVENT_SCOPE_CONFLICT/);
  assert.throws(() => readPatternEvent({ ...e, eligible: 0 }, 'J'), /ELIGIBILITY_CONFLICT/);
  const bad = pe(doc({ snapshot_date: '2025-12-31' })), d = JSON.parse(bad.row_json); d.exclusion_reasons = [];
  assert.throws(() => readPatternEvent({ ...bad, eligible: 1, row_json: JSON.stringify(d) }, 'J'), /ELIGIBLE_SCOPE/);
});
test('minimal projection SQL returns nested signal JSON correctly without copying raw/provider payload', () => {
  const db = new DatabaseSync(':memory:');
  try {
    const p = { ...doc(), raw: { secret: 'DO_NOT_COPY' }, project_name: 'unneeded long title', aliases: ['not population proof'] };
    const project = db.prepare(`SELECT ${projectDocumentSQL('b')} AS project_json FROM (SELECT ? AS row_json) b`).get(JSON.stringify(p));
    assert.equal(pe({ ...JSON.parse(project.project_json) }).eligible, 1);
    assert.ok(!project.project_json.includes('DO_NOT_COPY')); assert.ok(!project.project_json.includes('aliases'));
    const r = input(), source = { ...JSON.parse(r.evidence_json), evidence: ['UNNEEDED_RAW_TEXT'] };
    const evidence = db.prepare(`SELECT ${requestEvidenceSQL('r')} AS evidence_json FROM (SELECT ? AS row_json) r`).get(JSON.stringify(source));
    const projected = re({ ...r, project_json: project.project_json, evidence_json: evidence.evidence_json });
    assert.deepEqual(JSON.parse(projected.row_json).shared_signals, ['LED', 'STREET_LIGHT']);
    assert.ok(!evidence.evidence_json.includes('UNNEEDED_RAW_TEXT'));
    assert.throws(() => projectDocumentSQL('b; DROP TABLE x'), /SQL_ALIAS/);
  } finally { db.close(); }
});
test('read-back rejects request-number, organization-basis and source-label tampering', () => {
  const e = re(input());
  for (const patch of [{ shopping_delivery_req_no: 'WRONG' }, { organization_basis: 'LEARNED' }, { org_name: '가'.repeat(400) }, { budget_amount_basis: 'UNPROVEN' }]) {
    const d = { ...JSON.parse(e.row_json), ...patch };
    assert.throws(() => readPatternEvent({ ...e, row_json: JSON.stringify(d) }, 'J'), /PATTERN_/);
  }
});
