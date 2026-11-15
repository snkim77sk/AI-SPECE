import {searchRows,rowsCsv} from '../../../lib/g2b-service';
import {reportDatabaseError} from '../../../lib/db-errors.mjs';
export const dynamic='force-dynamic';
export async function GET(request:Request){try{const p=new URL(request.url).searchParams,result=await searchRows(p);if(p.get('format')==='csv')return new Response(rowsCsv(result),{headers:{'Content-Type':'text/csv; charset=utf-8','Content-Disposition':`attachment; filename="g2b-${result.mode}-page.csv"`,'Cache-Control':'no-store'}});return Response.json(result,{headers:{'Cache-Control':'no-store'}});}catch(e){const failure=reportDatabaseError('deliveries',e);return Response.json({error:failure.code,incident:failure.incident},{status:503,headers:{'Cache-Control':'no-store'}});}}
