/** Read-only exact AIDFA -> QWGJK links. Never treat the structure's total as a
 * project's money. SQL filters before its bounded transfer to the application. */
import {boundedJsonQuery, unpackBounded} from './matching-query.mjs';

export const STRUCTURE_PAGE_LIMIT = 100;
export const STRUCTURE_ROW_BYTES = 1024 * 1024;
export const STRUCTURE_PAGE_BYTES = 1024 * 1024;
const DIMENSIONS = ['field', 'section', 'account'];
// Match Python str.strip / JS trim, not fuzzy, case-folded or space-free names.
const whitespace = [9, 10, 11, 12, 13, 32, 160, 5760, 8192, 8193, 8194, 8195, 8196, 8197, 8198, 8199, 8200, 8201, 8202, 8232, 8233, 8239, 8287, 12288, 65279];
const trim = value => `trim(CAST(${value} AS TEXT),${whitespace.map(n => `char(${n})`).join('||')})`;
const rawText = (...keys) => `COALESCE(${keys.map(key => `NULLIF(CASE WHEN json_type(raw_object,'$.${key}') IN ('text','integer','real') THEN ${trim(`json_extract(raw_object,'$.${key}')`)} END,'')`).join(',')},'')`;
const fields = {
  // normalizeBudget.org_code can fall back to wa_laf_cd. It is deliberately
  // not used: a regional code or same organization name is never enough.
  org_code: rawText('laf_cd'),
  field_code: rawText('fld_cd', 'field_code'),
  field_name: rawText('fld_nm', 'field_name'),
  section_code: rawText('sect_cd', 'part_cd', 'section_code'),
  section_name: rawText('sect_nm', 'part_nm', 'section_name'),
  account_code: rawText('acnt_dv_cd', 'account_code'),
  account_name: rawText('acnt_dv_nm', 'account_name'),
  dept_code: rawText('dept_cd', 'dpt_cd', 'deptCode', 'departmentCode'),
};
const epochFence = '(SELECT CAST(revision AS TEXT) FROM budget_epochs WHERE mode=?)=?';

function prefix(where) {
  const raw = "json_extract(row_json,'$.raw')";
  return `WITH source AS (
    SELECT mode,service,source_key,payload_hash,fiscal_year,snapshot_date,updated_at,row_json,
      CASE WHEN json_valid(${raw}) THEN CASE WHEN json_type(${raw})='object' THEN ${raw} ELSE '{}' END ELSE '{}' END raw_object
    FROM budget_rows WHERE ${where.join(' AND ')}
  ), extracted AS (
    SELECT mode,service,source_key,payload_hash,fiscal_year,snapshot_date,updated_at,row_json,
      ${Object.entries(fields).map(([name, expression]) => `${expression} ${name}`).join(',\n      ')}
    FROM source
  )`;
}

const publicJson = `json_set(json_remove(row_json,'$.raw'),
  '$.mode',mode,'$.service',service,'$.source_key',source_key,'$.payload_hash',payload_hash,
  '$.fiscal_year',fiscal_year,'$.snapshot_date',snapshot_date,'$.updated_at',updated_at,
  ${Object.keys(fields).map(name => `'$.${name}',${name}`).join(',')})`;
const projection = `source_key, CASE WHEN length(CAST(${publicJson} AS BLOB))<=${STRUCTURE_ROW_BYTES} THEN ${publicJson} ELSE NULL END row_json`;
const validKey = key => typeof key === 'string' && key.length > 0 && new TextEncoder().encode(key).length <= 8192;
const validRevision = value => typeof value === 'string' && /^(0|[1-9]\d*)$/.test(value);

export function budgetStructurePlan(input) {
  const mode = input.get('mode') || 'live', service = input.get('service') || 'AIDFA', key = input.get('key');
  if (!['live', 'synthetic'].includes(mode)) throw Error('INVALID_BUDGET_STRUCTURE_MODE');
  if (service !== 'AIDFA') throw Error('AIDFA_STRUCTURE_REQUIRED');
  if (!validKey(key)) throw Error('BUDGET_STRUCTURE_KEY_REQUIRED');
  const yearText = input.get('year'), year = yearText === null ? null : Number(yearText);
  if (year !== null && (!/^\d{4}$/.test(yearText) || year < 2026 || year > 2100)) throw Error('INVALID_YEAR');
  const limitText = input.get('limit'), limit = limitText === null ? STRUCTURE_PAGE_LIMIT : Number(limitText);
  if (!Number.isInteger(limit) || limit < 1 || limit > STRUCTURE_PAGE_LIMIT || (limitText !== null && !/^\d+$/.test(limitText))) throw Error('INVALID_BUDGET_STRUCTURE_LIMIT');
  let cursor = null;
  if (input.has('cursor')) {
    const token = input.get('cursor');
    try { if (token.length > 65536) throw Error(); cursor = JSON.parse(token); } catch { throw Error('INVALID_BUDGET_STRUCTURE_CURSOR'); }
    if (!cursor || cursor.v !== 1 || cursor.mode !== mode || cursor.sourceKey !== key || !validKey(cursor.afterKey) || !validRevision(cursor.revision)) throw Error('INVALID_BUDGET_STRUCTURE_CURSOR');
  }
  return {mode, key, year, limit, cursor};
}

export function budgetStructureSource(plan, revision) {
  return {
    sql: `${prefix(['mode=?', "service='AIDFA'", 'source_key=?', 'is_active=1', epochFence])} SELECT ${projection} FROM extracted LIMIT 1`,
    args: [plan.mode, plan.key, plan.mode, revision],
  };
}

export function missingStructureDimensions(source) {
  return [...(!source.org_code ? ['org_code'] : []), ...DIMENSIONS.filter(d => !source[d + '_code'] && !source[d + '_name'])];
}

/** Both codes present means code equality ONLY. A missing code permits a
 * nonempty exact name comparison. Conflicting codes cannot fall back to name. */
export function budgetStructureDetails(plan, source, revision) {
  const where = ['mode=?', "service='QWGJK'", 'fiscal_year=?', 'is_active=1', epochFence];
  const args = [plan.mode, source.fiscal_year, plan.mode, revision];
  if (plan.cursor) { where.push('source_key>?'); args.push(plan.cursor.afterKey); }
  const matches = ["org_code<>''", 'org_code=?'];
  args.push(source.org_code);
  for (const dimension of DIMENSIONS) {
    const code = source[dimension + '_code'], name = source[dimension + '_name'];
    if (code) {
      matches.push(`(${dimension}_code=? OR (${dimension}_code='' AND ${dimension}_name<>'' AND ${dimension}_name=?))`);
      args.push(code, name);
    } else {
      matches.push(`${dimension}_name<>'' AND ${dimension}_name=?`);
      args.push(name);
    }
  }
  return {
    // Do not apply the single-row nulling projection here: SQL SUM ignores
    // NULL and could accidentally skip an oversized row. The shared framing
    // query counts the real bytes, transfers <= 1 MiB in total, and returns an
    // explicit null sentinel only if the FIRST eligible row cannot fit.
    sql: boundedJsonQuery(prefix(where), `SELECT source_key,${publicJson} row_json FROM extracted WHERE ${matches.join(' AND ')}`, 'source_key COLLATE BINARY ASC', plan.limit, STRUCTURE_PAGE_BYTES),
    args,
  };
}

export function unpackStructureDetails(data, limit) {
  try { return unpackBounded(data, limit); } catch (error) {
    if (error instanceof Error && error.message === 'MATCH_INPUT_RECORD_TOO_LARGE') throw Error('BUDGET_STRUCTURE_ROW_TOO_LARGE');
    throw error;
  }
}

function dimensionEvidence(source, detail, dimension) {
  const sourceCode = source[dimension + '_code'], detailCode = detail[dimension + '_code'];
  const sourceName = source[dimension + '_name'], detailName = detail[dimension + '_name'];
  const basis = sourceCode && detailCode ? 'CODE' : 'NAME';
  if (basis === 'CODE' ? sourceCode !== detailCode : !sourceName || sourceName !== detailName) throw Error('BUDGET_STRUCTURE_INVALID_MATCH');
  // The selected AIDFA record is already returned once. Reference its complete
  // dimension values rather than multiplying a potentially wide source label
  // by every code-matched project. Detail values stay explicit for the UI.
  return {basis, appropriation_ref: {code: `appropriation.${dimension}_code`, name: `appropriation.${dimension}_name`}, detail_code: detailCode, detail_name: detailName};
}

export function budgetStructureResult(plan, source, details, revision, done = details.length <= plan.limit) {
  const missingDimensions = missingStructureDimensions(source), hasMore = !done;
  if (hasMore && details.length === 0) throw Error('BUDGET_STRUCTURE_INVALID_MATCH');
  const rows = details.slice(0, plan.limit).map(detail => {
    if (!source.org_code || detail.org_code !== source.org_code || detail.fiscal_year !== source.fiscal_year || detail.mode !== plan.mode || detail.service !== 'QWGJK') throw Error('BUDGET_STRUCTURE_INVALID_MATCH');
    return {...detail, appropriation_source_key: source.source_key,
      match_basis: 'EXACT_YEAR_ORG_FIELD_SECTION_ACCOUNT',
      match_evidence: {
        fiscal_year: {basis: 'EXACT', appropriation: source.fiscal_year, detail: detail.fiscal_year},
        org_code: {basis: 'CODE', appropriation_ref: 'appropriation.org_code', detail: detail.org_code},
        ...Object.fromEntries(DIMENSIONS.map(d => [d, dimensionEvidence(source, detail, d)])),
      },
    };
  });
  return {
    mode: plan.mode, service: 'AIDFA', detailService: 'QWGJK', year: source.fiscal_year,
    appropriation: source, rows, hasMore, limit: plan.limit, maxPageBytes: STRUCTURE_PAGE_BYTES,
    next: hasMore ? JSON.stringify({v: 1, mode: plan.mode, sourceKey: source.source_key, revision, afterKey: rows.at(-1).source_key}) : null,
    linkable: missingDimensions.length === 0, missingDimensions,
    source: 'STORED_CURRENT_ONLY', scope: 'EXACT_STRUCTURE_LINKS', sourceRevision: revision,
    sourceCompletenessVerified: false, providerCalls: 0, source_io_performed: false,
    missingMoney: 'NULL_NOT_ZERO', invalidMoney: 'PRESERVED_UNCHANGED',
    amountSemantics: 'AIDFA_STRUCTURE_TOTAL_IS_NOT_QWGJK_PROJECT_AMOUNT',
    nameNormalization: 'TRIM_ONLY_EXACT_CASE_SENSITIVE',
  };
}
