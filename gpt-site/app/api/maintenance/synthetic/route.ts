import {database} from '../../../../lib/g2b-service';
import {readBounded} from '../../../../lib/collector.mjs';
import {retirementStatus,retirementBatch,retireBackedUpBatch} from '../../../../lib/synthetic-retirement.mjs';
import {databaseErrorCode} from '../../../../lib/db-errors.mjs';
export const dynamic='force-dynamic';
const reply=(data:unknown,status=200)=>Response.json(data,{status,headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
// Shared maintenance is protected by this Site's existing owner-only platform gate.
export async function GET(request:Request){
  try{const p=new URL(request.url).searchParams,d=database();return reply(p.has('table')?await retirementBatch(d,p.get('table'),Number(p.get('after')||0)):await retirementStatus(d));}
  catch(e){
    const code=e instanceof Error&&/^RETIREMENT_[A-Z_]+$/.test(e.message)?e.message:'RETIREMENT_READ_'+databaseErrorCode(e);
    const phase=(e as {retirementPhase?:string})?.retirementPhase;
    // These fixed read statements bind only a timestamp, cursor and numeric row IDs.
    // Classify structural D1 failures without logging statements or row contents.
    const cause=(e as Error)?.cause;
    const message=[(e as Error)?.message,cause instanceof Error?cause.message:''].join(' ').toLowerCase();
    const reasons=['not authorized','not supported','invalid column','too many','no such','syntax','rowid','parameter','bind','union','pragma','not allowed','memory','timeout','length','SQLITE_AUTH'.toLowerCase()].filter(x=>message.includes(x));
    return reply({error:code,phase,reasons,providerCalls:0},409);
  }
}
export async function POST(request:Request){
  const origin=request.headers.get('origin');if(origin&&origin!==new URL(request.url).origin)return reply({error:'ORIGIN_NOT_ALLOWED'},403);
  if(!request.headers.get('content-type')?.startsWith('application/json'))return reply({error:'JSON_REQUIRED'},415);
  try{const {raw}=await readBounded(new Response(request.body),16384);return reply(await retireBackedUpBatch(database(),raw));}
  catch(e){const code=e instanceof Error&&/^RETIREMENT_[A-Z_]+$/.test(e.message)?e.message:'RETIREMENT_DELETE_FAILED';return reply({error:code,providerCalls:0},409);}
}
