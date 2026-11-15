import test from 'node:test';
import assert from 'node:assert/strict';
import { groupRequests, latestDetails } from '../lib/matching.mjs';
import {
  MAX_REQUEST_STATE_BYTES, MAX_REQUEST_PAGE_ROWS, MAX_REQUEST_PAGE_BYTES,
  sitesDeliveryToLegacy, createRequestAccumulator, appendRequestPage,
  appendLegacyRequestPage, finishRequestAccumulator, serializeRequestAccumulator,
  deserializeRequestAccumulator, compareSqliteBinary, REQUEST_AGGREGATE_SITES_FIELDS,
} from '../lib/matching-stream.mjs';

const REVISION = 7;
const options = { datasetRevision: REVISION };
const site = (overrides = {}) => ({
  source_key: 'k000001', request_no: 'R1', sequence: '1', change_order: '0', final_flag: 'Y',
  source_date: '2026-06-15', updated_at: '2026-11-07T01:00:00.000Z', category: 'LIGHTING',
  detail_code: '39101699', detail_name: '보안등기구', request_name: '북도면 보안등 교체',
  item_id: '1234', item_name: 'LED 보안등', model: 'MODEL-50', agency: '인천광역시 옹진군',
  agency_code: 'ORG-1', region: '인천광역시', vendor: '공급업체', vendor_bizno: '111-22-33333',
  contract_no: 'C1', quantity: '0.1', unit_price: '100', amount: '10', request_total: '999999999999999999999',
  ...overrides,
});
const ordered = rows => [...rows].sort((a, b) => compareSqliteBinary(a.source_key, b.source_key));
function aggregate(rows, pageSize = 2, createOptions = {}, finishOptions = {}) {
  const input = ordered(rows);
  let state = createRequestAccumulator({ requestNo: input[0].request_no,
    sourceKey: input[0].source_key, expectedDetailRows: input.length, ...options, ...createOptions });
  for (let i = 0; i < input.length; i += pageSize) {
    state = appendRequestPage(state, input.slice(i, i + pageSize), options);
    state = deserializeRequestAccumulator(serializeRequestAccumulator(state));
  }
  return { state, result: finishRequestAccumulator(state, { ...options, ...finishOptions }) };
}
function oracle(rows) { return groupRequests(ordered(rows).map(sitesDeliveryToLegacy), { alreadyLatest: true })[0]; }

test('adapter is explicit, preserves original exact strings, omits raw and storage metadata', () => {
  const row = site({ raw: 'SENSITIVE_RAW_NOT_LOADED', amount: '900719925474099300000000', quantity: '0.00000000000000000001' });
  const legacy = sitesDeliveryToLegacy(row);
  assert.equal(legacy.delivery_req_name, row.request_name);
  assert.equal(legacy.fetched_at, row.updated_at);
  assert.equal(legacy.detail_item_name, row.detail_name);
  assert.equal(legacy.model_name, row.model);
  assert.equal(legacy.demand_org, row.agency);
  assert.equal(legacy.demand_region, row.region);
  assert.equal(legacy.vendor_name, row.vendor);
  assert.equal(legacy.amount, row.amount);
  assert.equal(legacy.quantity, row.quantity);
  assert.equal('raw' in legacy, false);
  assert.equal('request_no' in legacy, false);
  assert.ok(REQUEST_AGGREGATE_SITES_FIELDS.includes('request_name'));
  assert.ok(!REQUEST_AGGREGATE_SITES_FIELDS.includes('raw'));
  assert.throws(() => sitesDeliveryToLegacy(site({ amount: 9007199254740992 })), /SITES_STRING_REQUIRED:amount/);
});

test('every page boundary preserves the complete legacy summary exactly', () => {
  const rows = Array.from({ length: 31 }, (_, i) => site({
    source_key: 'key-' + String(i).padStart(3, '0'), sequence: String(i),
    change_order: String(i % 11), category: i % 3 ? 'LIGHTING' : 'POLE',
    source_date: `2026-${String(1 + i % 9).padStart(2, '0')}-01`,
    amount: i % 6 ? '9007199254740993' : null, quantity: i % 2 ? '0.2' : '0.01',
    item_name: '품명' + i % 4, model: '모델' + i % 7,
  }));
  const expected = oracle(rows);
  for (let pageSize = 1; pageSize <= rows.length; pageSize++) {
    const { result } = aggregate(rows, pageSize);
    assert.deepEqual(result.summary, expected, 'pageSize=' + pageSize);
    assert.equal(result.missing_amount_count, 6);
    assert.equal(result.counts.detail_rows, 31);
    assert.equal(result.counts.pages, Math.ceil(31 / pageSize));
  }
});

test('numeric representative change rank 10 beats 2 and request total is max, not repeated sum', () => {
  const rows = [site({ change_order: '2', final_flag: 'Y', request_name: 'rank2' }),
    site({ source_key: 'k2', sequence: '2', change_order: '제10차', final_flag: 'N', request_name: 'rank10', amount: '9007199254740993', quantity: '0.2' })];
  const { result } = aggregate(rows, 1);
  assert.deepEqual(result.summary, oracle(rows));
  assert.equal(result.summary.delivery_req_name, 'rank10');
  assert.equal(result.summary.amount, '9007199254741003');
  assert.equal(result.summary.delivery_req_total_amount, '999999999999999999999');
  assert.equal(result.summary.quantity, '0.3');
});

test('all representative rank tie-breakers match legacy across checkpoints', () => {
  for (const override of [
    { final_flag: 'Y' }, { source_date: '2026-07-01' },
    { updated_at: '2026-11-08T00:00:00.000Z' }, {},
  ]) {
    const rows = [site({ final_flag: '', request_name: 'first' }),
      site({ source_key: 'zz', sequence: '2', final_flag: '', request_name: 'last', ...override })];
    assert.deepEqual(aggregate(rows, 1).result.summary, oracle(rows));
    assert.equal(aggregate(rows, 1).result.summary.delivery_req_name, 'last');
  }
});

test('current/latest selection must occur before streaming; oracle agrees after selection', () => {
  const historical = [site({ source_key: 'a-old', change_order: '2', amount: '999999' }),
    site({ source_key: 'b-new', change_order: '10', amount: '123' }),
    site({ source_key: 'c-new', sequence: '2', amount: '456' })];
  const allLegacy = historical.map(sitesDeliveryToLegacy);
  const current = ordered(latestDetails(allLegacy));
  let state = createRequestAccumulator({ requestNo: 'R1', expectedDetailRows: current.length, ...options });
  for (const row of current) state = appendLegacyRequestPage(state, [row], options);
  const result = finishRequestAccumulator(state, options);
  assert.deepEqual(result.summary, groupRequests(allLegacy)[0]);
  assert.equal(result.summary.amount, '579');
});

test('unknown, null, empty amounts are counted while zero and negatives are exact values', () => {
  const amounts = [null, undefined, '', ' ', '0', '-2', '9,007,199,254,740,993'];
  const rows = amounts.map((amount, i) => site({ source_key: 'k' + i, sequence: String(i), amount }));
  const result = aggregate(rows, 2).result;
  assert.deepEqual(result.summary, oracle(rows));
  assert.equal(result.summary.amount, '9007199254740991');
  assert.equal(result.missing_amount_count, 4);
  assert.equal(result.counts.priced_detail_rows, 3);
});

test('decimal quantity precision, negative cancellation, commas and different scales are exact', () => {
  const quantities = ['0.100000000000000000000001', '0.200000000000000000000002',
    '1,000.000000000000000000000004', '-1000.3', null, ''];
  const rows = quantities.map((quantity, i) => site({ source_key: 'k' + i, sequence: String(i), quantity }));
  const result = aggregate(rows, 1).result;
  assert.deepEqual(result.summary, oracle(rows));
  assert.equal(result.summary.quantity, '0.000000000000000000000007');
  const zeros = [site({ quantity: '-0.000' }), site({ source_key: 'z', sequence: '2', quantity: '0.00000' })];
  assert.equal(aggregate(zeros).result.summary.quantity, '0');
});

test('minimum nonblank source date is retained, including when representative is later', () => {
  const rows = [site({ source_date: '' }), site({ source_key: 'b', sequence: '2', source_date: '2026-01-01' }),
    site({ source_key: 'z', sequence: '3', change_order: '99', source_date: '2026-12-31' })];
  const result = aggregate(rows, 1).result;
  assert.equal(result.summary.source_date, '2026-01-01');
  assert.deepEqual(result.summary, oracle(rows));
});

test('sorted string unions are distinct, trimmed and stable at page boundaries', () => {
  const names = ['나', ' 가 ', '', '나', 'A', 'a', '𐀀', '\ue000'];
  const rows = names.map((s, i) => site({ source_key: 'k' + i, sequence: String(i),
    detail_name: s, item_name: s, model: s, vendor: s }));
  const result = aggregate(rows, 1).result;
  assert.deepEqual(result.summary, oracle(rows));
  assert.equal(result.summary.item_name, ['나', '가', 'A', 'a', '𐀀', '\ue000'].sort().join(' / '));
});

test('all request integrity conflicts match legacy and preserve representative fields', () => {
  const rows = [site(), site({ source_key: 'z', sequence: '2', agency: '다른 기관',
    vendor_bizno: '22222', contract_no: 'C2', vendor: '다른 업체' })];
  const result = aggregate(rows, 1).result;
  assert.deepEqual(result.summary, oracle(rows));
  assert.deepEqual(result.integrity_conflicts, ['CONTRACT_NO_CONFLICT', 'DEMAND_ORG_CONFLICT', 'VENDOR_BIZNO_CONFLICT']);
  assert.equal(result.summary.request_integrity_valid, false);
  assert.equal(result.summary.request_integrity_status, 'EXCLUDED_CONFLICT');
  assert.equal(result.summary.demand_org, '다른 기관');
});

test('vendor-name conflict is suppressed by any known business number, including in later page', () => {
  const rows = [site({ vendor: 'A', vendor_bizno: '' }),
    site({ source_key: 'k2', sequence: '2', vendor: 'B', vendor_bizno: '' }),
    site({ source_key: 'k3', sequence: '3', vendor: 'C', vendor_bizno: '111-22' })];
  assert.deepEqual(aggregate(rows.slice(0, 2), 1).result.integrity_conflicts, ['VENDOR_NAME_CONFLICT']);
  assert.deepEqual(aggregate(rows, 1).result.integrity_conflicts, []);
  assert.deepEqual(aggregate(rows, 1).result.summary, oracle(rows));
});

test('normalized identity uses last original spelling, not representative spelling', () => {
  const rows = [site({ change_order: '99', agency: '인천 광역시 옹진군', vendor_bizno: '1112233333', contract_no: 'C-1' }),
    site({ source_key: 'z', sequence: '2', agency: '인천광역시옹진군', vendor_bizno: '111-22-33333', contract_no: 'C1' })];
  const result = aggregate(rows, 1).result;
  assert.deepEqual(result.summary, oracle(rows));
  assert.equal(result.summary.demand_org, '인천광역시옹진군');
  assert.equal(result.summary.vendor_bizno, '111-22-33333');
  assert.equal(result.summary.contract_no, 'C1');
});

test('missing request number is one source only and never combined with another missing request', () => {
  const first = site({ request_no: '', agency: 'A', vendor: 'A', vendor_bizno: '' });
  const second = site({ request_no: '', source_key: 'z', agency: 'B', vendor: 'B', vendor_bizno: '' });
  for (const row of [first, second]) assert.deepEqual(aggregate([row], 1).result.summary, oracle([row]));
  let state = createRequestAccumulator({ requestNo: '', sourceKey: first.source_key, ...options });
  state = appendRequestPage(state, [first], options);
  assert.throws(() => appendRequestPage(state, [second], options), /SOURCE_FALLBACK_MISMATCH/);
  assert.equal(finishRequestAccumulator(state, { ...options, nextRequestNo: '', nextSourceKey: 'z' }).completion_proof, 'NEXT_SOURCE_FALLBACK');
  assert.throws(() => createRequestAccumulator({ requestNo: '', ...options }), /SOURCE_KEY_REQUIRED/);
  assert.throws(() => createRequestAccumulator({ requestNo: '', sourceKey: 's', expectedDetailRows: 2, ...options }), /SOURCE_FALLBACK_IS_SINGLE_ROW/);
});

test('missing source keys, crossed request identity and duplicate keys fail without state mutation', () => {
  let state = createRequestAccumulator({ requestNo: 'R1', ...options });
  for (const row of [site({ source_key: '' }), site({ source_key: undefined }), site({ request_no: 'R2' })]) {
    const before = serializeRequestAccumulator(state);
    assert.throws(() => appendRequestPage(state, [row], options), /SOURCE_KEY_REQUIRED|REQUEST_IDENTITY_MISMATCH/);
    assert.equal(serializeRequestAccumulator(state), before);
  }
  state = appendRequestPage(state, [site()], options);
  assert.throws(() => appendRequestPage(state, [site()], options), /SOURCE_KEYS_NOT_STRICTLY_INCREASING/);
  assert.throws(() => appendRequestPage(state, [site({ source_key: 'a' })], options), /SOURCE_KEYS_NOT_STRICTLY_INCREASING/);
});

test('strict SQLite BINARY page order works for supplementary unicode characters', () => {
  const rows = [site({ source_key: '𐀀', sequence: '2' }), site({ source_key: '\ue000', sequence: '1' })];
  assert.deepEqual(ordered(rows).map(r => r.source_key), ['\ue000', '𐀀']);
  assert.deepEqual(aggregate(rows, 1).result.summary, oracle(rows));
});

test('unproven or truncated requests never finish successfully', () => {
  let state = createRequestAccumulator({ requestNo: 'R1', ...options });
  assert.throws(() => finishRequestAccumulator(state, { ...options, endOfSource: true }), /EMPTY_REQUEST/);
  state = appendRequestPage(state, [site()], options);
  for (const proof of [{}, { nextRequestNo: 'R1' }, { nextRequestNo: 'R0' }, { endOfSource: false }]) {
    assert.throws(() => finishRequestAccumulator(state, { ...options, ...proof }), /REQUEST_COMPLETION_PROOF_REQUIRED/);
  }
  assert.equal(finishRequestAccumulator(state, { ...options, nextRequestNo: 'R2' }).completion_proof, 'NEXT_REQUEST');
  assert.equal(finishRequestAccumulator(state, { ...options, endOfSource: true }).completion_proof, 'SOURCE_EOF');
  let counted = createRequestAccumulator({ requestNo: 'R1', expectedDetailRows: 2, ...options });
  counted = appendRequestPage(counted, [site()], options);
  assert.throws(() => finishRequestAccumulator(counted, { ...options, endOfSource: true }), /INCOMPLETE_REQUEST/);
  assert.throws(() => appendRequestPage(counted, [site({ source_key: 'x' }), site({ source_key: 'z' })], options), /DETAIL_COUNT_EXCEEDED/);
});

test('mismatched or absent source revisions fail at append and finish', () => {
  const state = appendRequestPage(createRequestAccumulator({ requestNo: 'R1', ...options }), [site()], options);
  for (const fn of [() => appendRequestPage(state, [site({ source_key: 'z' })], { datasetRevision: 8 }),
    () => finishRequestAccumulator(state, { datasetRevision: 8, endOfSource: true })]) {
    assert.throws(fn, /DATASET_CHANGED_RESTART_REQUIRED/);
  }
  assert.throws(() => appendRequestPage(state, [site({ source_key: 'z' })]), /INVALID_DATASET_REVISION/);
  assert.throws(() => finishRequestAccumulator(state, { endOfSource: true }), /INVALID_DATASET_REVISION/);
});

test('a partial request can resume after a page containing several request boundaries', () => {
  const rows = ['R1', 'R1', 'R1', 'R2', 'R3', 'R3', 'R3'].map((request_no, i) => site({
    request_no, sequence: String(i), source_key: 'k' + i, amount: String(i + 1),
  }));
  let pending = null; const summaries = [];
  // Simulate GROUP keyset pages, persisting only the last partial request.
  for (let i = 0; i < rows.length; i += 2) {
    for (const row of rows.slice(i, i + 2)) {
      if (pending && pending.request_no !== row.request_no) {
        summaries.push(finishRequestAccumulator(pending, { ...options, nextRequestNo: row.request_no }).summary);
        pending = null;
      }
      pending ??= createRequestAccumulator({ requestNo: row.request_no, ...options });
      pending = appendRequestPage(pending, [row], options);
    }
    pending = deserializeRequestAccumulator(serializeRequestAccumulator(pending));
  }
  summaries.push(finishRequestAccumulator(pending, { ...options, endOfSource: true }).summary);
  const expected = groupRequests(rows.map(sitesDeliveryToLegacy), { alreadyLatest: true });
  assert.deepEqual(summaries.sort((a, b) => a.delivery_req_no.localeCompare(b.delivery_req_no)),
    expected.sort((a, b) => a.delivery_req_no.localeCompare(b.delivery_req_no)));
});

test('UTF-8 byte bound fails whole page with no truncation, even below JS character limit', () => {
  const state = createRequestAccumulator({ requestNo: 'R1', ...options });
  const before = serializeRequestAccumulator(state);
  const rows = Array.from({ length: 300 }, (_, i) => site({ source_key: 'k' + String(i).padStart(3, '0'),
    sequence: String(i), item_name: '가'.repeat(1200) + i }));
  assert.throws(() => appendRequestPage(state, rows, options), /REQUEST_STATE_TOO_LARGE/);
  assert.equal(serializeRequestAccumulator(state), before);
  assert.throws(() => finishRequestAccumulator(state, { ...options, endOfSource: true }), /EMPTY_REQUEST/);
  assert.throws(() => createRequestAccumulator({ requestNo: 'R1', ...options, maxStateBytes: MAX_REQUEST_STATE_BYTES + 1 }), /STATE_BYTE_LIMIT_TOO_HIGH/);
});

test('page size and bytes are explicitly bounded', () => {
  const state = createRequestAccumulator({ requestNo: 'R1', ...options });
  assert.throws(() => appendRequestPage(state, [], options), /NONEMPTY_REQUEST_PAGE_REQUIRED/);
  assert.throws(() => appendRequestPage(state, Array(MAX_REQUEST_PAGE_ROWS + 1).fill(site()), options), /REQUEST_PAGE_TOO_MANY_ROWS/);
  assert.throws(() => appendRequestPage(state, [site({ item_name: 'x'.repeat(MAX_REQUEST_PAGE_BYTES + 1) })], options), /REQUEST_PAGE_TOO_LARGE/);
  assert.throws(() => deserializeRequestAccumulator(' '.repeat(MAX_REQUEST_STATE_BYTES + 1)), /REQUEST_STATE_TOO_LARGE/);
});

test('invalid exact numbers fail atomically rather than rounding or using binary floats', () => {
  const state = createRequestAccumulator({ requestNo: 'R1', ...options });
  for (const overrides of [{ amount: '1.2' }, { quantity: '1e3' }, { request_total: 'NaN' }]) {
    const before = serializeRequestAccumulator(state);
    assert.throws(() => appendRequestPage(state, [site(), site({ source_key: 'z', sequence: '2', ...overrides })], options), /INVALID_INTEGER|INVALID_DECIMAL/);
    assert.equal(serializeRequestAccumulator(state), before);
  }
});

test('20,000 details retain bounded state and match the legacy oracle', () => {
  const rows = Array.from({ length: 20000 }, (_, i) => site({ source_key: 'k' + String(i).padStart(6, '0'),
    sequence: String(i), amount: i % 11 ? '9007199254740993' : null,
    quantity: i % 2 ? '0.12345678901234567890' : '-0.00000000000000000001',
    item_name: '품명' + i % 25, model: '모델' + i % 25, change_order: String(i % 12),
  }));
  const { state, result } = aggregate(rows, 500);
  assert.deepEqual(result.summary, oracle(rows));
  assert.equal(result.counts.detail_rows, 20000);
  assert.equal(result.missing_amount_count, 1819);
  assert.ok(Buffer.byteLength(serializeRequestAccumulator(state)) < 10000);
});

test('seeded randomized data matches every complete batch without floating point', () => {
  let seed = 17;
  const random = max => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed % max; };
  for (let batch = 0; batch < 25; batch++) {
    const rows = Array.from({ length: 1 + random(80) }, (_, i) => site({
      source_key: 'k' + String(i).padStart(4, '0'), sequence: String(i),
      amount: random(6) ? String(BigInt(random(999999)) * 10000000000000001n) : null,
      quantity: random(3) ? `${random(100)}.${String(random(999999)).padStart(6, '0')}` : null,
      category: random(2) ? 'LIGHTING' : 'POLE', change_order: String(random(100)),
      agency: random(3) ? '인천 옹진군' : '인천옹진군', vendor_bizno: random(8) ? '111-22' : '',
      item_name: '제품' + random(20), vendor: '업체' + random(4), contract_no: random(10) ? 'C1' : 'C2',
    }));
    assert.deepEqual(aggregate(rows, 1 + random(20)).result.summary, oracle(rows));
  }
});
