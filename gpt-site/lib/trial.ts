import { env } from 'cloudflare:workers';
import {ACQUIRE_SQL,STATE_SQL,ERROR_SQL,commitPlan} from './persistence.mjs';
import { PAGE_SIZE, MAX_BYTES, DAILY_LIMIT, ENDPOINT, sourceIdentity, parsePage, loadPage, kstDate, yesterdayKst, validDay } from './collector.mjs';
export type Job={id:string;mode:string;day:string;state:string;page:number;page_size:number;total:number|null;saved:number;calls:number;max_payload:number;lease:string|null;lease_until:number;error:string|null;created_at:string;updated_at:string};
function db(){if(!env.DB)throw new Error('DATABASE_UNAVAILABLE');return env.DB;}
// The original trial remains a synthetic-only harness; real collection uses the guarded collection service.
function liveReady(){return false;}
export async function status(id?:string|null){
 const d=db();
 const job=await (id?d.prepare('SELECT * FROM jobs WHERE id=?').bind(id):d.prepare('SELECT * FROM jobs ORDER BY created_at DESC LIMIT 1')).first<Job>();
 const logs=job?(await d.prepare('SELECT * FROM receipts WHERE job_id=? ORDER BY page DESC LIMIT 8').bind(job.id).all()).results:[];
 const records=job?(await d.prepare('SELECT source_id,title,agency,amount FROM records WHERE job_id=? ORDER BY source_id LIMIT 8').bind(job.id).all()).results:[];
 const daily=await d.prepare('SELECT calls FROM usage WHERE day=?').bind(kstDate()).first<{calls:number}>();
 return {job,receipts:logs,records,config:{liveReady:liveReady(),legacySyntheticOnly:true,pageSize:PAGE_SIZE,maxResponseBytes:MAX_BYTES,dailyLimit:DAILY_LIMIT,dailyCalls:daily?.calls||0,endpoint:ENDPOINT,defaultDay:yesterdayKst(),automaticSchedule:false,cloudMemoryMeasured:false}};
}
export async function start(mode:string,day:string,total:number){
 if(!['synthetic','live'].includes(mode)||!validDay(day)||day>yesterdayKst())throw new Error('INVALID_INPUT');
 if(mode==='live')throw new Error('LIVE_CREDENTIAL_APPROVAL_REQUIRED');
 if(mode==='synthetic'&&(!Number.isSafeInteger(total)||total<1||total>20000))throw new Error('INVALID_INPUT');
 const id=crypto.randomUUID(),now=new Date().toISOString();
 await db().prepare('INSERT INTO jobs (id,mode,day,state,page,page_size,total,saved,calls,max_payload,lease_until,created_at,updated_at) VALUES (?,?,?,\'ready\',1,?,?,0,0,0,0,?,?)').bind(id,mode,day,PAGE_SIZE,mode==='synthetic'?total:null,now,now).run();
 return status(id);
}
export async function changeState(id:string,state:string){
 if(!['paused','ready'].includes(state))throw new Error('INVALID_INPUT');
 const result=await db().prepare(STATE_SQL).bind(state,new Date().toISOString(),id,Date.now()).run();
 if(!result.meta.changes)throw new Error('BUSY_OR_COMPLETE');
 return status(id);
}
export async function step(id:string){
 const d=db(),now=Date.now(),token=crypto.randomUUID();
 const acquired=await d.prepare(ACQUIRE_SQL).bind(token,now+90000,id,now).run();
 if(!acquired.meta.changes)throw new Error('BUSY_PAUSED_OR_COMPLETE');
 const job=await d.prepare('SELECT * FROM jobs WHERE id=? AND lease=?').bind(id,token).first<Job>();if(!job)throw new Error('LEASE_LOST');
 try {
  if(job.mode!=='synthetic')throw new Error('LEGACY_TRIAL_SYNTHETIC_ONLY');
  await d.prepare('UPDATE jobs SET calls=calls+1 WHERE id=? AND lease=?').bind(id,token).run();
  const {raw,bytes}=await loadPage(job,undefined);
  const {rows,total,done}=parsePage(raw,job.page,job.page_size,job.total);
  const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(raw)));
  const hash=Array.from(new Uint8Array(digest)).map(b=>b.toString(16).padStart(2,'0')).join('');
  const plan=commitPlan(job,token,rows,total,done,bytes,hash,new Date().toISOString());
  const committed=await d.batch(plan.map(q=>d.prepare(q.sql).bind(...q.params)));
  if(!committed.at(-1)?.meta.changes)throw new Error('LEASE_LOST');
 }catch(error){
  const code=error instanceof Error&&/^[A-Z0-9_]+$/.test(error.message)?error.message:'STORAGE_OR_SOURCE_FAILED';
  await d.prepare(ERROR_SQL).bind(code,new Date().toISOString(),id,token).run();
  throw new Error(code);
 }
 return status(id);
}
