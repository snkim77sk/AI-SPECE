import {guardPublicRequest,readOnlyDataView} from '../../../lib/public-auth-service';
import {createMatching,matchingWorkspace,matchingStep,matchingState,matchingResults} from '../../../lib/matching-service';import {readBounded} from '../../../lib/collector.mjs';import {reportDatabaseError} from '../../../lib/db-errors.mjs';
export const dynamic='force-dynamic';const reply=(data:unknown,status=200)=>Response.json(data,{status,headers:{'Cache-Control':'no-store'}});
const dataJobFields=['id','mode','state','phase','engine_version','g2b_epoch','budget_epoch','from_day','to_day','budget_count','request_count','candidate_count','match_count','excluded_count','created_at','updated_at','stale'];
const dataJob=(job:any)=>job?Object.fromEntries(dataJobFields.filter(key=>Object.hasOwn(job,key)).map(key=>[key,job[key]])):null;
export async function GET(request:Request){const __publicBlock=await guardPublicRequest(request);if(__publicBlock)return __publicBlock;
 try{
  const p=new URL(request.url).searchParams;
  const data=p.get('action')==='results'?await matchingResults(p,'live'):await matchingWorkspace(p.get('id'),true,'live');
  // Public/member views retain data provenance and progress, never leases,
  // checkpoint cursors or internal errors (including future added columns).
  return reply(readOnlyDataView(request)?{...data,job:dataJob(data.job),...('jobs' in data?{jobs:data.jobs.map(dataJob)}:{})}:data);
 }catch(e){
  if(e instanceof Error&&['MATCH_JOB_REQUIRED','MATCH_JOB_NOT_FOUND'].includes(e.message))return reply({error:e.message},e.message==='MATCH_JOB_REQUIRED'?400:404);
  const f=reportDatabaseError('matching_read',e);return reply({error:f.code,incident:f.incident},503);
 }
}
export async function POST(request:Request){const __publicBlock=await guardPublicRequest(request);if(__publicBlock)return __publicBlock;const origin=request.headers.get('origin');if(origin&&origin!==new URL(request.url).origin)return reply({error:'ORIGIN_NOT_ALLOWED'},403);if(!request.headers.get('content-type')?.startsWith('application/json'))return reply({error:'JSON_REQUIRED'},415);try{const {raw:b}=await readBounded(new Response(request.body),2048);if(b.action==='create')return reply(await createMatching(b.mode,b.from,b.to));if(typeof b.id!=='string'||!(/^[a-f0-9-]{36}$/.test(b.id)))throw Error('INVALID_INPUT');if(b.action==='step')return reply(await matchingStep(b.id));if(['pause','resume','cancel'].includes(b.action))return reply(await matchingState(b.id,b.action==='pause'?'paused':b.action==='resume'?'ready':'cancelled'));throw Error('INVALID_ACTION');}catch(e){return reply({error:e instanceof Error&&/^[A-Z0-9_]+$/.test(e.message)?e.message:'MATCH_REQUEST_FAILED'},409);}}
