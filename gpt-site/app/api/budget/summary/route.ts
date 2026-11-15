import {budgetSummary} from '../../../../lib/budget-summary-service';
import {reportDatabaseError} from '../../../../lib/db-errors.mjs';

export const dynamic = 'force-dynamic';
export async function GET(request: Request) {
  const headers = {'Cache-Control': 'no-store'};
  try {
    return Response.json(await budgetSummary(new URL(request.url).searchParams), {headers});
  } catch (error) {
    const code = error instanceof Error && /^[A-Z0-9_]+$/.test(error.message) ? error.message : null;
    if (code) {
      const status = code === 'BUDGET_SUMMARY_SOURCE_CHANGED_RETRY' ? 409 : /^(BUDGET_SUMMARY_(SOURCE_NOT_FOUND|INVALID_AGGREGATE|UNSAFE_AGGREGATE)|DATABASE_UNAVAILABLE)$/.test(code) ? 503 : 400;
      return Response.json({error: code}, {status, headers});
    }
    const failure = reportDatabaseError('budget_summary', error);
    return Response.json({error: failure.code, incident: failure.incident}, {status: 503, headers});
  }
}
