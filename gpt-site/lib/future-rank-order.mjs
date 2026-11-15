// Pure, bounded ordering of already-selected evidence rows. No I/O, scoring,
// source selection, persisted state, or claim of a complete/global population.
export const FUTURE_RANK_VERSION = 'future-rank-v1';
export const FUTURE_RANK_MAX_ROWS = 500;
export const FUTURE_RANK_MAX_BYTES = 1048576;
export const FUTURE_RANK_AMOUNT_DIGITS = 8194;
export const FUTURE_RANK_TEXT_BYTES = 65536;
const encoder = new TextEncoder();
const digitWidth = String(FUTURE_RANK_AMOUNT_DIGITS).length;
const fail = reason => { throw Error('FUTURE_RANK_' + reason); };
const cmp = (a, b) => a < b ? -1 : a > b ? 1 : 0;

function wellFormed(value) {
  for (let i = 0; i < value.length; i++) {
    const c = value.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff) {
      const next = value.charCodeAt(++i);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
    } else if (c >= 0xdc00 && c <= 0xdfff) return false;
  }
  return true;
}

function text(value, label, required = false) {
  if (typeof value !== 'string' || (required && !value.trim())) fail('INVALID_' + label);
  if (value.length > FUTURE_RANK_TEXT_BYTES) fail('TEXT_BYTE_LIMIT');
  if (!wellFormed(value) || value.includes('\0')) fail('INVALID_' + label);
  const bytes = encoder.encode(value);
  if (bytes.length > FUTURE_RANK_TEXT_BYTES) fail('TEXT_BYTE_LIMIT');
  return bytes;
}

/** Exact canonical decimal, never a Number conversion of textual money.
 * null/undefined alone mean missing; blank, decimal, grouped, exponential,
 * non-finite, unsafe numeric, and unsupported inputs fail rather than becoming 0.
 * Leading zeros/signs are normalized only in the key, never in the source row.
 */
function decimal(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value)) fail('UNSAFE_AMOUNT_NUMBER');
    return String(value);
  }
  if (typeof value !== 'string') fail('INVALID_AMOUNT');
  if (value.length > FUTURE_RANK_AMOUNT_DIGITS + 1) fail('AMOUNT_DIGIT_LIMIT');
  if (!/^[+-]?\d+$/.test(value)) fail('INVALID_AMOUNT');
  const unsigned = /^[+-]/.test(value) ? value.slice(1) : value;
  if (unsigned.length > FUTURE_RANK_AMOUNT_DIGITS) fail('AMOUNT_DIGIT_LIMIT');
  const magnitude = unsigned.replace(/^0+(?=\d)/, '');
  return value.startsWith('-') && magnitude !== '0' ? '-' + magnitude : magnitude;
}

/** Known amounts sort by key BINARY ASC in descending numeric order. Missing
 * remains a separate flag; it sorts after every known value, including negatives.
 * The fixed version/length-prefix format has a declared digit bound. Overflow
 * throws, so a wider future format requires a new version and index rebuild.
 */
export function futureAmountOrderKey(value) {
  const n = decimal(value);
  if (n === null) return { missing: 1, key: '' };
  if (n.startsWith('-')) {
    const magnitude = n.slice(1);
    return { missing: 0, key: '1' + String(magnitude.length).padStart(digitWidth, '0') + magnitude };
  }
  const complement = n.replace(/\d/g, d => String(57 - d.charCodeAt(0)));
  return { missing: 0, key: '0' + String(FUTURE_RANK_AMOUNT_DIGITS - n.length).padStart(digitWidth, '0') + complement };
}

// Hex preserves UTF-8/BINARY byte order. Complementing bytes and using a high
// terminator reverses even prefix order ("aa" before "a"). No locale or UTF-16
// comparison is involved. The output is ASCII and contains no NUL or SQL syntax.
function textKey(value, descending, label, required = false) {
  const bytes = text(value, label, required);
  let out = '';
  for (const b of bytes) out += (descending ? 255 - b : b).toString(16).padStart(2, '0');
  return out + (descending ? 'g' : '/');
}

function rowObject(row) {
  if (!row || typeof row !== 'object' || Array.isArray(row) ||
      ![null, Object.prototype].includes(Object.getPrototypeOf(row))) fail('ROW_OBJECT_REQUIRED');
}

function field(row, name) {
  const descriptor = Object.getOwnPropertyDescriptor(row, name);
  if (!descriptor) return undefined;
  if (!descriptor.enumerable || !Object.hasOwn(descriptor, 'value')) fail('NON_JSON_PROPERTY');
  return descriptor.value;
}

/** SQL order: score DESC, missing ASC, amount DESC, org DESC, project DESC,
 * source_key ASC, all text using UTF-8/BINARY collation. source_key is unique
 * within a run; keep immutable mode/service/run/scope/version outside this key.
 * Names are not trimmed or normalized. Null/missing names retain empty-name
 * ordering, but source_key must be a nonempty, well-formed string.
 */
export function futureRankKey(row) {
  rowObject(row);
  const score = field(row, 'historical_evidence_score');
  if (!Number.isSafeInteger(score) || score < 0 || score > 100) fail('INVALID_SCORE');
  const invalid = field(row, 'invalid_amount');
  if (invalid !== undefined && invalid !== 0 && invalid !== false)
    fail('INVALID_AMOUNT_FLAG');
  // Validate both fields, even an unused fallback, rather than hiding corruption.
  const budget = decimal(field(row, 'budget_amount')), appropriation = decimal(field(row, 'appropriation_amount'));
  const amount = futureAmountOrderKey(budget ?? appropriation);
  return 'F01:' + String(100 - score).padStart(3, '0') + ':' + amount.missing + ':' + amount.key + ':' +
    textKey(field(row, 'org_name') ?? '', true, 'ORG') + textKey(field(row, 'project_name') ?? '', true, 'PROJECT') +
    textKey(field(row, 'source_key'), false, 'SOURCE_KEY', true);
}

export function compareFutureRankRows(a, b) {
  return cmp(futureRankKey(a), futureRankKey(b));
}

// Validate bounded, ordinary JSON data before serialization. This rejects fields
// JSON.stringify would silently omit/coerce, getters, cycles, exotic objects and
// sparse arrays. Unknown ordinary JSON fields are preserved, not projected away.
// The byte budget includes canonical JSON row data AND its persisted rank key.
function boundedJsonGuard(maxBytes) {
  let bytes = 2, nodes = 0;
  const ancestors = new Set();
  const add = n => { bytes += n; if (bytes > maxBytes) fail('BYTE_LIMIT'); };
  function quoted(value) {
    if (value.length > maxBytes - bytes) fail('BYTE_LIMIT');
    if (!wellFormed(value)) fail('INVALID_JSON_UNICODE');
    const result = JSON.stringify(value);
    add(encoder.encode(result).length);
    return result;
  }
  function visit(value, depth = 0) {
    if (++nodes > 32768) fail('JSON_NODE_LIMIT');
    if (depth > 16) fail('JSON_DEPTH_LIMIT');
    if (typeof value === 'string') return quoted(value);
    if (value === null || typeof value === 'boolean') {
      const result = String(value); add(result.length); return result;
    }
    if (typeof value === 'number') {
      if (!Number.isFinite(value) || Object.is(value, -0) || (Number.isInteger(value) && !Number.isSafeInteger(value)))
        fail('INVALID_JSON_NUMBER');
      const result = JSON.stringify(value); add(result.length); return result;
    }
    if (typeof value !== 'object') fail('NON_JSON_VALUE');
    const array = Array.isArray(value);
    if (!array && ![null, Object.prototype].includes(Object.getPrototypeOf(value))) fail('NON_JSON_OBJECT');
    if (array && Object.getPrototypeOf(value) !== Array.prototype) fail('NON_JSON_ARRAY');
    if (array && value.length > 4096) fail('JSON_LIST_LIMIT');
    if (ancestors.has(value)) fail('CYCLIC_INPUT');
    const keys = Reflect.ownKeys(value);
    if (keys.some(k => typeof k !== 'string')) fail('NON_JSON_PROPERTY');
    const fields = array ? keys.filter(k => k !== 'length') : keys;
    if (fields.length > (array ? 4096 : 256)) fail('JSON_FIELD_LIMIT');
    if (array && (fields.length !== value.length || fields.some((k, i) => k !== String(i)))) fail('NON_JSON_ARRAY');
    const values = new Map();
    for (const key of fields) {
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (!descriptor.enumerable || !Object.hasOwn(descriptor, 'value')) fail('NON_JSON_PROPERTY');
      values.set(key, descriptor.value);
    }
    ancestors.add(value); add(2);
    const parts = (array ? fields : fields.sort()).map((key, i) => {
      if (i) add(1);
      const prefix = array ? '' : quoted(key) + (add(1), ':');
      return prefix + visit(values.get(key), depth + 1);
    });
    ancestors.delete(value);
    return (array ? '[' : '{') + parts.join(',') + (array ? ']' : '}');
  }
  return {
    row(value, first) {
      // Each framed item is [row, rank_key]; the enclosing array costs 2 bytes.
      if (!first) add(1);
      const start = bytes;
      add(3);
      const fingerprint = visit(value);
      const key = futureRankKey(value);
      quoted(key);
      return { fingerprint, key, bytes: bytes - start };
    },
    get bytes() { return bytes; },
  };
}

/** Prepare one persistable row, including lossless canonical source JSON. The
 * byte count is UTF-8 length of [[row, rank_key]], also used by the merge guard.
 * No rank or source fields are added to, removed from, or changed in row_json.
 */
export function prepareFutureRankRow(row, { byteLimit = FUTURE_RANK_MAX_BYTES } = {}) {
  if (!Number.isSafeInteger(byteLimit) || byteLimit < 2 || byteLimit > FUTURE_RANK_MAX_BYTES) fail('INVALID_BYTE_LIMIT');
  rowObject(row);
  const guard = boundedJsonGuard(byteLimit), entry = guard.row(row, true);
  return { version: FUTURE_RANK_VERSION, source_key: row.source_key, rank_key: entry.key,
    row_json: entry.fingerprint, byteLength: guard.bytes };
}

/** Trust-boundary verification for rows prepared by prepareFutureRankRow.
 * Canonical JSON is required; rank-affecting changes, versions, keys and source
 * identities fail closed. This is not a signature of non-ranking metadata.
 * The immutable run context and payload integrity are the caller's concern.
 */
export function verifyFutureRankRow(record, options) {
  if (!record || record.version !== FUTURE_RANK_VERSION) fail('VERSION_MISMATCH');
  if (typeof record.row_json !== 'string' || record.row_json.length > FUTURE_RANK_MAX_BYTES) fail('INVALID_STORED_JSON');
  let row;
  try { row = JSON.parse(record.row_json); } catch { fail('INVALID_STORED_JSON'); }
  const prepared = prepareFutureRankRow(row, options);
  if (prepared.source_key !== record.source_key) fail('SOURCE_IDENTITY_MISMATCH');
  if (prepared.rank_key !== record.rank_key) fail('ORDER_KEY_MISMATCH');
  if (prepared.row_json !== record.row_json) fail('NONCANONICAL_STORED_JSON');
  return row;
}

/** Merge two bounded selections and retain <=limit rows, preserving source
 * objects/fields without mutating either input. Each input is <=500 rows; all
 * inputs, including rank losers, must fit the byte/schema guards. A byte limit
 * never silently reduces the requested result count. At most 1000 rows/keys are
 * considered in one call, and at most 500 are returned.
 *
 * Duplicates (even identical retries) and conflicting rows for the same exact
 * source_key fail. This call cannot detect identities dropped
 * by earlier merges: persisted global uniqueness belongs in a D1 unique index.
 * Omitted rows are only rank-limit omissions from THIS call, never a population
 * coverage count. The caller must not infer global completeness from this API.
 */
export function mergeFutureRankRows(selected = [], incoming = [], { limit = FUTURE_RANK_MAX_ROWS, byteLimit = FUTURE_RANK_MAX_BYTES } = {}) {
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > FUTURE_RANK_MAX_ROWS) fail('INVALID_LIMIT');
  if (!Number.isSafeInteger(byteLimit) || byteLimit < 2 || byteLimit > FUTURE_RANK_MAX_BYTES) fail('INVALID_BYTE_LIMIT');
  for (const rows of [selected, incoming]) {
    if (!Array.isArray(rows) || rows.length > FUTURE_RANK_MAX_ROWS) fail('INPUT_ROW_LIMIT');
  }
  const guard = boundedJsonGuard(byteLimit), identities = new Map(), keys = new Set(), entries = [];
  for (const rows of [selected, incoming]) for (const row of rows) {
    // Validate JSON before evaluating ranking fields so getters cannot run.
    rowObject(row);
    const { fingerprint, key, bytes } = guard.row(row, entries.length === 0);
    const identity = row.source_key;
    if (identities.has(identity)) fail(identities.get(identity) === fingerprint ? 'DUPLICATE_SOURCE_IDENTITY' : 'CONFLICTING_SOURCE_IDENTITY');
    if (keys.has(key)) fail('DUPLICATE_ORDER_KEY');
    identities.set(identity, fingerprint); keys.add(key);
    entries.push({ row, key, bytes });
  }
  entries.sort((a, b) => cmp(a.key, b.key));
  const retained = entries.slice(0, limit);
  const inputBytes = guard.bytes;
  const outputBytes = 2 + retained.reduce((n, e, i) => n + e.bytes + (i ? 1 : 0), 0);
  return { version: FUTURE_RANK_VERSION, coverage: 'SUPPLIED_ROWS_ONLY',
    rows: retained.map(e => e.row), rankKeys: retained.map(e => e.key),
    consideredRows: entries.length, omittedRows: entries.length - retained.length, inputBytes, outputBytes };
}
