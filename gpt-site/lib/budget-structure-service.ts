import {database} from './g2b-service';
import {budgetStructurePlan, budgetStructureSource, budgetStructureDetails, budgetStructureResult, missingStructureDimensions, unpackStructureDetails} from './budget-structure.mjs';

type StoredRow = {source_key: string; row_json: string | null};
/** Stored data only. No configuration, credentials, provider I/O or writes. */
export async function budgetStructure(params: URLSearchParams) {
  const plan = budgetStructurePlan(params), db = database();
  const readEpoch = () => db.prepare('SELECT CAST(revision AS TEXT) revision FROM budget_epochs WHERE mode=?').bind(plan.mode).first<{revision: string}>();
  const before = await readEpoch();
  if (!before || !/^(0|[1-9]\d*)$/.test(before.revision)) throw Error('BUDGET_STRUCTURE_SOURCE_UNAVAILABLE');
  if (plan.cursor && plan.cursor.revision !== before.revision) throw Error('BUDGET_STRUCTURE_SOURCE_CHANGED_RETRY');
  const query = budgetStructureSource(plan, before.revision);
  const stored = await db.prepare(query.sql).bind(...query.args).first<StoredRow>();
  if (!stored) {
    if ((await readEpoch())?.revision !== before.revision) throw Error('BUDGET_STRUCTURE_SOURCE_CHANGED_RETRY');
    throw Error('BUDGET_STRUCTURE_NOT_FOUND');
  }
  if (!stored.row_json) throw Error('BUDGET_STRUCTURE_ROW_TOO_LARGE');
  const source = JSON.parse(stored.row_json);
  if (plan.year !== null && plan.year !== source.fiscal_year) throw Error('BUDGET_STRUCTURE_YEAR_MISMATCH');
  let details: StoredRow[] = [];
  if (!missingStructureDimensions(source).length) {
    const query = budgetStructureDetails(plan, source, before.revision);
    details = (await db.prepare(query.sql).bind(...query.args).all()).results as StoredRow[];
  }
  if ((await readEpoch())?.revision !== before.revision) throw Error('BUDGET_STRUCTURE_SOURCE_CHANGED_RETRY');
  const page = unpackStructureDetails(details, plan.limit);
  return budgetStructureResult(plan, source, page.rows, before.revision, page.done);
}
