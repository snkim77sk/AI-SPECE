import{futureResults,futureWorkspace}from'../../../lib/future-service';
import{reportDatabaseError}from'../../../lib/db-errors.mjs';
export const dynamic='force-dynamic';
const reply=(data:unknown,status=200)=>Response.json(data,{status,headers:{'Cache-Control':'no-store'}});
export async function GET(request:Request){try{const p=new URL(request.url).searchParams;return reply(p.get('action')==='results'?await futureResults(p):await futureWorkspace(p.get('mode')||'live'));}catch(e){const code=e instanceof Error&&/^[A-Z0-9_]+$/.test(e.message)?e.message:null;if(code)return reply({error:code},409);const f=reportDatabaseError('future_evidence',e);return reply({error:f.code,incident:f.incident},503);}}
