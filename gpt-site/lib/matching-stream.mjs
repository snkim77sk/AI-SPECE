// Bounded, pure, single-request aggregation of ALREADY-CURRENT target details.
// No network, credentials, storage calls, raw source payloads, or Number money.
import { integer, norm } from './matching.mjs';

export const MAX_REQUEST_STATE_BYTES = 1024 * 1024;
export const MAX_REQUEST_PAGE_BYTES = 2 * 1024 * 1024;
export const MAX_REQUEST_PAGE_ROWS = 500;
const FORMAT = 'g2b-request-aggregate';
const VERSION = 1;
const text = value => String(value ?? '').trim();
const encoder = new TextEncoder();
const UNION_FIELDS = ['detail_item_name', 'item_name', 'model_name', 'vendor_name'];
const IDENTITY_FIELDS = ['demand_org', 'vendor_bizno', 'contract_no', 'vendor_name'];
const cmp = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const fail = code => { throw Error(code); };

// These columns must be in the GROUP scan. In particular the normal Sites view
// projection currently omits request_name, which matching must retain.
export const REQUEST_AGGREGATE_SITES_FIELDS = [
  'source_key', 'request_no', 'sequence', 'change_order', 'final_flag',
  'source_date', 'updated_at', 'category', 'detail_code', 'detail_name',
  'request_name', 'item_id', 'item_name', 'model', 'agency', 'agency_code',
  'region', 'vendor', 'vendor_bizno', 'contract_no', 'quantity', 'unit_price',
  'amount', 'request_total',
];

// Explicit boundary adapter. All existing source strings stay as strings;
// neither Number() nor SQL REAL/INTEGER casts are used for exact values.
export function sitesDeliveryToLegacy(row) {
  if (!row || typeof row !== 'object' || Array.isArray(row)) fail('INVALID_SITES_ROW');
  for (const field of REQUEST_AGGREGATE_SITES_FIELDS) {
    const value = row[field];
    if (value !== null && value !== undefined && typeof value !== 'string') {
      fail('SITES_STRING_REQUIRED:' + field);
    }
  }
  return {
    source_key: row.source_key ?? '',
    delivery_req_no: row.request_no ?? '',
    detail_seq: row.sequence ?? '',
    delivery_change_order: row.change_order ?? '',
    is_final_delivery_request: row.final_flag ?? '',
    source_date: row.source_date ?? '',
    fetched_at: row.updated_at ?? '',
    primary_category: row.category ?? '',
    detail_item_no: row.detail_code ?? '',
    detail_item_name: row.detail_name ?? '',
    delivery_req_name: row.request_name ?? '',
    item_id: row.item_id ?? '',
    item_name: row.item_name ?? '',
    model_name: row.model ?? '',
    demand_org: row.agency ?? '',
    demand_org_code: row.agency_code ?? '',
    demand_region: row.region ?? '',
    vendor_name: row.vendor ?? '',
    vendor_bizno: row.vendor_bizno ?? '',
    contract_no: row.contract_no ?? '',
    quantity: row.quantity ?? null,
    unit_price: row.unit_price ?? null,
    amount: row.amount ?? null,
    delivery_req_total_amount: row.request_total ?? null,
  };
}

// SQLite BINARY compares UTF-8 bytes, unlike JS's UTF-16 string ordering.
// Use BINARY for page cursors; legacy ranking and unions still use JS ordering.
export function compareSqliteBinary(a, b) {
  const aa = encoder.encode(a), bb = encoder.encode(b);
  for (let i = 0; i < Math.min(aa.length, bb.length); i++) {
    if (aa[i] !== bb[i]) return aa[i] < bb[i] ? -1 : 1;
  }
  return cmp(aa.length, bb.length);
}

function safeCount(value, code, minimum = 0) {
  if (!Number.isSafeInteger(value) || value < minimum) fail(code);
  return value;
}

function revision(value) {
  return safeCount(value, 'INVALID_DATASET_REVISION');
}

function maxBytes(value) {
  safeCount(value, 'INVALID_STATE_BYTE_LIMIT', 1);
  if (value > MAX_REQUEST_STATE_BYTES) fail('STATE_BYTE_LIMIT_TOO_HIGH');
  return value;
}

function emptyIdentity() { return { key: '', value: '', conflict: false }; }

function encodeBounded(state) {
  const json = JSON.stringify(state);
  if (encoder.encode(json).byteLength > state.max_state_bytes) fail('REQUEST_STATE_TOO_LARGE');
  return json;
}

/** @param {any} options */
export function createRequestAccumulator({
  requestNo = '', sourceKey = '', expectedDetailRows = null,
  datasetRevision, mode = 'live', scopeId = '',
  maxStateBytes = MAX_REQUEST_STATE_BYTES,
} = {}) {
  if (typeof requestNo !== 'string' || typeof sourceKey !== 'string') fail('INVALID_REQUEST_IDENTITY');
  const request = text(requestNo), source = text(sourceKey);
  if (!request && !source) fail('SOURCE_KEY_REQUIRED');
  if (!['live', 'synthetic'].includes(mode)) fail('INVALID_MODE');
  if (typeof scopeId !== 'string') fail('INVALID_SCOPE_ID');
  if (expectedDetailRows !== null) safeCount(expectedDetailRows, 'INVALID_EXPECTED_DETAIL_ROWS', 1);
  if (!request && expectedDetailRows !== null && expectedDetailRows !== 1) fail('SOURCE_FALLBACK_IS_SINGLE_ROW');
  const state = {
    format: FORMAT, version: VERSION, mode, scope_id: scopeId,
    dataset_revision: revision(datasetRevision), request_no: request,
    fallback_source_key: request ? '' : source,
    expected_detail_rows: expectedDetailRows,
    max_state_bytes: maxBytes(maxStateBytes), detail_rows: 0, page_count: 0,
    missing_amount_count: 0, first_source_key: null, after_source_key: null,
    min_source_date: '', representative: null, amount_sum: '0',
    quantity: { coefficient: '0', scale: 0 }, request_total_max: '0',
    categories: [], unions: Object.fromEntries(UNION_FIELDS.map(field => [field, []])),
    identities: Object.fromEntries(IDENTITY_FIELDS.map(field => [field, emptyIdentity()])),
  };
  encodeBounded(state);
  return state;
}

function validateState(state) {
  if (!state || state.format !== FORMAT || state.version !== VERSION) fail('INVALID_REQUEST_STATE');
  revision(state.dataset_revision);
  maxBytes(state.max_state_bytes);
  safeCount(state.detail_rows, 'INVALID_REQUEST_STATE');
  safeCount(state.page_count, 'INVALID_REQUEST_STATE');
  safeCount(state.missing_amount_count, 'INVALID_REQUEST_STATE');
  if (state.missing_amount_count > state.detail_rows) fail('INVALID_REQUEST_STATE');
  if (typeof state.request_no !== 'string' || typeof state.fallback_source_key !== 'string' ||
      (!state.request_no && !state.fallback_source_key)) fail('INVALID_REQUEST_STATE');
  if (!['live', 'synthetic'].includes(state.mode) || typeof state.scope_id !== 'string') fail('INVALID_REQUEST_STATE');
  if (state.expected_detail_rows !== null) {
    safeCount(state.expected_detail_rows, 'INVALID_REQUEST_STATE', 1);
    if (state.detail_rows > state.expected_detail_rows) fail('DETAIL_COUNT_EXCEEDED');
    if (!state.request_no && state.expected_detail_rows !== 1) fail('INVALID_REQUEST_STATE');
  }
  if (!state.request_no && state.detail_rows > 1) fail('SOURCE_FALLBACK_IS_SINGLE_ROW');
  if (typeof state.amount_sum !== 'string' || typeof state.request_total_max !== 'string') fail('INVALID_REQUEST_STATE');
  integer(state.amount_sum); integer(state.request_total_max);
  if (!state.quantity || typeof state.quantity.coefficient !== 'string') fail('INVALID_REQUEST_STATE');
  integer(state.quantity.coefficient);
  safeCount(state.quantity.scale, 'INVALID_REQUEST_STATE');
  if (state.quantity.scale > state.max_state_bytes) fail('INVALID_REQUEST_STATE');
  if (typeof state.min_source_date !== 'string' || !Array.isArray(state.categories) ||
      state.categories.some(c => !['LIGHTING', 'POLE'].includes(c))) fail('INVALID_REQUEST_STATE');
  for (const field of UNION_FIELDS) {
    if (!Array.isArray(state.unions?.[field]) || state.unions[field].some(s => typeof s !== 'string')) fail('INVALID_REQUEST_STATE');
  }
  for (const field of IDENTITY_FIELDS) {
    const value = state.identities?.[field];
    if (!value || typeof value.key !== 'string' || typeof value.value !== 'string' || typeof value.conflict !== 'boolean') fail('INVALID_REQUEST_STATE');
  }
  if (state.detail_rows) {
    if (!state.representative || typeof state.first_source_key !== 'string' || typeof state.after_source_key !== 'string' ||
        !text(state.first_source_key) || !text(state.after_source_key) ||
        compareSqliteBinary(state.first_source_key, state.after_source_key) > 0) fail('INVALID_REQUEST_STATE');
  } else if (state.representative !== null || state.first_source_key !== null || state.after_source_key !== null) {
    fail('INVALID_REQUEST_STATE');
  }
}

export function serializeRequestAccumulator(state) {
  validateState(state);
  return encodeBounded(state);
}

export function deserializeRequestAccumulator(json, { maxStateBytes = MAX_REQUEST_STATE_BYTES } = {}) {
  maxBytes(maxStateBytes);
  if (typeof json !== 'string') fail('INVALID_REQUEST_STATE');
  if (json.length > maxStateBytes || encoder.encode(json).byteLength > maxStateBytes) fail('REQUEST_STATE_TOO_LARGE');
  const state = JSON.parse(json);
  validateState(state);
  if (state.max_state_bytes > maxStateBytes) fail('STATE_BYTE_LIMIT_TOO_HIGH');
  encodeBounded(state);
  return state;
}

function assertRevision(state, datasetRevision) {
  if (revision(datasetRevision) !== state.dataset_revision) fail('DATASET_CHANGED_RESTART_REQUIRED');
}

function latestRank(row) {
  return [BigInt(text(row.delivery_change_order).replace(/\D/g, '') || '0'),
    text(row.is_final_delivery_request).toUpperCase() === 'Y' ? 1 : 0,
    text(row.source_date), text(row.fetched_at), text(row.source_key)];
}

function compareRank(a, b) {
  const aa = latestRank(a), bb = latestRank(b);
  for (let i = 0; i < aa.length; i++) { const c = cmp(aa[i], bb[i]); if (c) return c; }
  return 0;
}

function addIdentity(identity, field, value) {
  const original = text(value);
  const key = field === 'vendor_bizno' ? original.replace(/\D/g, '') : norm(original);
  if (!key || identity.conflict) return;
  if (!identity.key) { identity.key = key; identity.value = original; }
  else if (identity.key === key) identity.value = original; // Map's last value, exactly as legacy.
  else identity.conflict = true;
}

function addDecimal(quantity, value) {
  if (value === null || value === undefined || text(value) === '') return;
  if (typeof value === 'number' && !Number.isSafeInteger(value)) fail('DECIMAL_STRING_REQUIRED');
  const s = text(value).replaceAll(',', '');
  if (!/^-?\d+(\.\d+)?$/.test(s)) fail('INVALID_DECIMAL');
  const scale = (s.split('.')[1] || '').length;
  let old = BigInt(quantity.coefficient);
  if (scale > quantity.scale) { old *= 10n ** BigInt(scale - quantity.scale); quantity.scale = scale; }
  quantity.coefficient = (old + BigInt(s.replace('.', '')) * 10n ** BigInt(quantity.scale - scale)).toString();
}

function renderDecimal({ coefficient, scale }) {
  const total = BigInt(coefficient), sign = total < 0n ? '-' : '';
  const digits = (total < 0n ? -total : total).toString().padStart(scale + 1, '0');
  return scale ? sign + (digits.slice(0, -scale) + '.' + digits.slice(-scale)).replace(/\.?0+$/, '') : sign + digits;
}

function checkPage(rows, adapt) {
  if (!Array.isArray(rows) || !rows.length) fail('NONEMPTY_REQUEST_PAGE_REQUIRED');
  if (rows.length > MAX_REQUEST_PAGE_ROWS) fail('REQUEST_PAGE_TOO_MANY_ROWS');
  let bytes = 0;
  return rows.map(raw => {
    const row = adapt(raw);
    // A pathological single string is rejected before allocating escaped JSON.
    for (const value of Object.values(row)) {
      if (typeof value === 'string' && value.length > MAX_REQUEST_PAGE_BYTES) fail('REQUEST_PAGE_TOO_LARGE');
    }
    const json = JSON.stringify(row);
    bytes += encoder.encode(json).byteLength;
    if (bytes > MAX_REQUEST_PAGE_BYTES) fail('REQUEST_PAGE_TOO_LARGE');
    return JSON.parse(json); // No caller object references survive into state.
  });
}

function appendPage(state, rows, { datasetRevision } = {}, adapt) {
  validateState(state);
  assertRevision(state, datasetRevision);
  // Clone at most one bounded checkpoint. Errors never mutate caller state.
  const next = JSON.parse(encodeBounded(state));
  const page = checkPage(rows, adapt);
  const unions = Object.fromEntries(UNION_FIELDS.map(f => [f, new Set(next.unions[f])]));
  const categories = new Set(next.categories);
  let amount = BigInt(next.amount_sum), requestTotal = BigInt(next.request_total_max);
  for (const row of page) {
    if (!text(row.source_key)) fail('SOURCE_KEY_REQUIRED');
    if (text(row.delivery_req_no) !== next.request_no) fail('REQUEST_IDENTITY_MISMATCH');
    if (!next.request_no && text(row.source_key) !== next.fallback_source_key) fail('SOURCE_FALLBACK_MISMATCH');
    if (next.after_source_key !== null && compareSqliteBinary(next.after_source_key, row.source_key) >= 0) {
      fail('SOURCE_KEYS_NOT_STRICTLY_INCREASING');
    }
    if (!next.request_no && next.detail_rows) fail('SOURCE_FALLBACK_IS_SINGLE_ROW');
    safeCount(next.detail_rows + 1, 'DETAIL_COUNT_OVERFLOW');
    next.detail_rows++;
    if (next.expected_detail_rows !== null && next.detail_rows > next.expected_detail_rows) fail('DETAIL_COUNT_EXCEEDED');
    next.first_source_key ??= row.source_key;
    next.after_source_key = row.source_key;
    amount += integer(row.amount);
    if (row.amount === null || row.amount === undefined || text(row.amount) === '') next.missing_amount_count++;
    const total = integer(row.delivery_req_total_amount);
    if (total > requestTotal) requestTotal = total;
    addDecimal(next.quantity, row.quantity);
    const date = text(row.source_date);
    if (date && (!next.min_source_date || date < next.min_source_date)) next.min_source_date = date;
    const category = text(row.primary_category).toUpperCase();
    if (['LIGHTING', 'POLE'].includes(category)) categories.add(category);
    if (!next.representative || compareRank(row, next.representative) > 0) next.representative = row;
    for (const field of UNION_FIELDS) { const value = text(row[field]); if (value) unions[field].add(value); }
    for (const field of IDENTITY_FIELDS) addIdentity(next.identities[field], field, row[field]);
  }
  next.amount_sum = amount.toString();
  next.request_total_max = requestTotal.toString();
  next.page_count = safeCount(next.page_count + 1, 'PAGE_COUNT_OVERFLOW');
  next.categories = [...categories].sort();
  next.unions = Object.fromEntries(UNION_FIELDS.map(f => [f, [...unions[f]].sort()]));
  encodeBounded(next); // Never return an incomplete/truncated union as success.
  return next;
}

export function appendRequestPage(state, sitesRows, options) {
  return appendPage(state, sitesRows, options, sitesDeliveryToLegacy);
}

// For equivalence verification or callers that already applied the adapter.
export function appendLegacyRequestPage(state, legacyRows, options) {
  return appendPage(state, legacyRows, options, row => {
    if (!row || typeof row !== 'object' || Array.isArray(row)) fail('INVALID_LEGACY_ROW');
    return row;
  });
}

function integrityConflicts(state) {
  if (!state.request_no) return [];
  const ids = state.identities, issues = [];
  if (ids.demand_org.conflict) issues.push('DEMAND_ORG_CONFLICT');
  if (ids.vendor_bizno.conflict) issues.push('VENDOR_BIZNO_CONFLICT');
  else if (!ids.vendor_bizno.key && ids.vendor_name.conflict) issues.push('VENDOR_NAME_CONFLICT');
  if (ids.contract_no.conflict) issues.push('CONTRACT_NO_CONFLICT');
  return issues.sort();
}

function completionProof(state, { nextRequestNo, nextSourceKey, endOfSource } = {}) {
  if (state.expected_detail_rows !== null) {
    if (state.detail_rows !== state.expected_detail_rows) fail('INCOMPLETE_REQUEST');
    return 'EXPECTED_COUNT';
  }
  if (endOfSource === true) return 'SOURCE_EOF';
  if (typeof nextRequestNo === 'string') {
    const next = text(nextRequestNo);
    if (compareSqliteBinary(state.request_no, next) < 0) return 'NEXT_REQUEST';
    if (!state.request_no && next === '' && typeof nextSourceKey === 'string' &&
        compareSqliteBinary(state.after_source_key, nextSourceKey) < 0) return 'NEXT_SOURCE_FALLBACK';
  }
  fail('REQUEST_COMPLETION_PROOF_REQUIRED');
}

export function finishRequestAccumulator(state, options = {}) {
  validateState(state);
  assertRevision(state, options.datasetRevision);
  encodeBounded(state);
  if (!state.detail_rows) fail('EMPTY_REQUEST');
  const proof = completionProof(state, options);
  const issues = integrityConflicts(state), rep = state.representative;
  const summary = {
    ...rep,
    source_key: state.request_no ? 'REQUEST:' + state.request_no : rep.source_key,
    delivery_req_no: state.request_no,
    source_date: state.min_source_date,
    request_integrity_valid: !issues.length,
    request_integrity_status: issues.length ? 'EXCLUDED_CONFLICT' : 'VALID',
    request_integrity_issues: issues,
    primary_categories: [...state.categories],
    primary_category: state.categories.length === 1 ? state.categories[0] : 'MIXED_TARGET',
    detail_seq: '', amount: state.amount_sum, quantity: renderDecimal(state.quantity),
    request_detail_rows: state.detail_rows,
    amount_basis: 'SUM_LATEST_TARGET_DETAIL_ITEM_AMOUNT',
    unit_price: '0', unit_price_basis: 'REQUEST_AGGREGATE_NOT_APPLICABLE',
    delivery_req_total_amount: state.request_total_max,
  };
  for (const field of UNION_FIELDS) summary[field] = state.unions[field].join(' / ');
  for (const field of ['demand_org', 'vendor_bizno', 'contract_no']) {
    const id = state.identities[field];
    if (id.key && !id.conflict) summary[field] = id.value;
  }
  // Unions grow when joined with delimiters; bound the final deliverable too.
  if (encoder.encode(JSON.stringify(summary)).byteLength > state.max_state_bytes) fail('REQUEST_SUMMARY_TOO_LARGE');
  return {
    summary, missing_amount_count: state.missing_amount_count,
    integrity_conflicts: issues,
    counts: { detail_rows: state.detail_rows, priced_detail_rows: state.detail_rows - state.missing_amount_count,
      missing_amount_rows: state.missing_amount_count, pages: state.page_count },
    completion_proof: proof,
  };
}
