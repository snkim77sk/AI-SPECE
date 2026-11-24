import {guardPublicRequest} from '../../../../lib/public-auth-service';
import {budgetHistorySearch} from '../../../../lib/budget-history-service';
import {reportDatabaseError} from '../../../../lib/db-errors.mjs';

export const dynamic = 'force-dynamic';
export async function GET(request: Request) {const __publicBlock=await guardPublicRequest(request);if(__publicBlock)return __publicBlock;
  const headers = {'Cache-Control': 'no-store'};
  try {
    return Response.json(await budgetHistorySearch(new URL(request.url).searchParams), {headers});
  } catch (error) {
    const code = error instanceof Error && /^[A-Z0-9_]+$/.test(error.message) ? error.message : null;
    if (code) {
      const status = code === 'BUDGET_HISTORY_SOURCE_CHANGED_RETRY' ? 409 :
        /^(DATABASE_UNAVAILABLE|BUDGET_HISTORY_(SOURCE_NOT_FOUND|INVALID_EVIDENCE))$/.test(code) ? 503 :
        /BUDGET_HISTORY_(RECORD|BASELINE)_TOO_LARGE/.test(code) ? 422 : 400;
      return Response.json({error: code}, {status, headers});
    }
    const failure = reportDatabaseError('budget_history_search', error);
    return Response.json({error: failure.code, incident: failure.incident}, {status: 503, headers});
  }
}
