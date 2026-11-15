import {budgetStructure} from '../../../../lib/budget-structure-service';
import {reportDatabaseError} from '../../../../lib/db-errors.mjs';

export const dynamic = 'force-dynamic';
export async function GET(request: Request) {
  const headers = {'Cache-Control': 'no-store'};
  try {
    return Response.json(await budgetStructure(new URL(request.url).searchParams), {headers});
  } catch (error) {
    const code = error instanceof Error && /^[A-Z0-9_]+$/.test(error.message) ? error.message : null;
    if (code) {
      const status = code === 'BUDGET_STRUCTURE_SOURCE_CHANGED_RETRY' ? 409
        : code === 'BUDGET_STRUCTURE_NOT_FOUND' ? 404
        : /^(BUDGET_STRUCTURE_(SOURCE_UNAVAILABLE|ROW_TOO_LARGE|INVALID_MATCH)|DATABASE_UNAVAILABLE)$/.test(code) ? 503 : 400;
      return Response.json({error: code}, {status, headers});
    }
    const failure = reportDatabaseError('budget_structure', error);
    return Response.json({error: failure.code, incident: failure.incident}, {status: 503, headers});
  }
}
