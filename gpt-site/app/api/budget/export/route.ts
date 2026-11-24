import {guardPublicRequest} from '../../../../lib/public-auth-service';
import {exportBudget} from '../../../../lib/export-service';
import {reportDatabaseError} from '../../../../lib/db-errors.mjs';
export const dynamic='force-dynamic';
export async function GET(request:Request){const __publicBlock=await guardPublicRequest(request);if(__publicBlock)return __publicBlock;try{return await exportBudget(new URL(request.url).searchParams);}catch(e){const code=e instanceof Error&&/^[A-Z0-9_]+$/.test(e.message)?e.message:null;if(code)return Response.json({error:code},{status:409,headers:{'Cache-Control':'no-store'}});const f=reportDatabaseError('budget_export',e);return Response.json({error:f.code,incident:f.incident},{status:503,headers:{'Cache-Control':'no-store'}});}}
