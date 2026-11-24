/** Production boundary. Fixture generators remain available only to offline tests. */
import {readBounded} from './collector.mjs';

export const retired = () => Response.json({error:'SYNTHETIC_RETIRED',providerCalls:0},
  {status:410,headers:{'Cache-Control':'no-store'}});
const parents=['jobs','collection_sessions','budget_sessions','matching_jobs','institution_pattern_runs','future_rank_runs'];
const idSql=parents.map(t=>`SELECT id FROM ${t} WHERE mode='synthetic' AND id=?`).concat([
  "SELECT g.id FROM g2b_recovery_generations g JOIN collection_sessions s ON s.id=g.root_id WHERE s.mode='synthetic' AND g.id=?",
  "SELECT p.id FROM collection_source_probes p JOIN collection_sessions s ON s.id=p.session_id WHERE s.mode='synthetic' AND p.id=?",
  "SELECT p.id FROM collection_source_diagnostics_v2 p JOIN collection_sessions s ON s.id=p.session_id WHERE s.mode='synthetic' AND p.id=?",
]);

export async function liveOnlyRequest(request,db){
  const url=new URL(request.url),path=url.pathname.replace(/\/+$/,'');
  if(!path.startsWith('/api/')){
    if(url.searchParams.getAll('mode').includes('synthetic')){
      url.searchParams.set('mode','live');url.searchParams.delete('id');url.searchParams.delete('cursor');
      return Response.redirect(url.toString(),307);
    }
    return null;
  }
  if(path==='/api/trial')return retired();
  if(path==='/api/maintenance/synthetic'||path==='/api/server-sync'||path==='/api/server-status')return null;
  if(url.searchParams.getAll('mode').some(x=>x!=='live'))return retired();
  let body={};
  // The existing logout form has no JSON body. Its route checks Origin and
  // revokes the session; it cannot address or mutate source-data records.
  if(request.method==='POST'&&path!=='/api/public-auth/logout'){
    try{body=(await readBounded(new Response(request.clone().body),8192)).raw;}
    catch{return Response.json({error:'INVALID_REQUEST'},{status:400});}
    if(!body||typeof body!=='object'||Array.isArray(body))return Response.json({error:'INVALID_REQUEST'},{status:400});
    if(Object.hasOwn(body,'mode')&&body.mode!=='live')return retired();
  }
  const ids=[...new Set([...url.searchParams.values(),...Object.values(body)]
    .filter(v=>typeof v==='string'&&/^[a-f0-9-]{36}$/.test(v)))];
  if(ids.length>8)return Response.json({error:'TOO_MANY_IDENTIFIERS'},{status:400});
  for(const id of ids){
    if(!db)throw Error('DATABASE_UNAVAILABLE');
    const results=await db.batch(idSql.map(sql=>db.prepare(sql+' LIMIT 1').bind(id)));
    if(results.some(r=>r.results?.length))return retired();
  }
  return null;
}
