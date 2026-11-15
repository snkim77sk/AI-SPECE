import {sourceIdentity} from './collector.mjs';
export const ACQUIRE_SQL="UPDATE jobs SET lease=?,lease_until=?,state='running',error=NULL WHERE id=? AND state IN ('ready','running','error') AND lease_until<=?";
export const STATE_SQL="UPDATE jobs SET state=?,error=NULL,lease=NULL,lease_until=0,updated_at=? WHERE id=? AND state!='complete' AND lease_until<=?";
export const ERROR_SQL="UPDATE jobs SET state='error',error=?,lease=NULL,lease_until=0,updated_at=? WHERE id=? AND lease=?";
export const QUOTA_SQL='INSERT INTO usage(day,calls) VALUES (?,1) ON CONFLICT(day) DO UPDATE SET calls=calls+1 WHERE calls<? RETURNING calls';
/** @param {any} job @param {string} token @param {any[]} rows @param {number} total @param {boolean} done @param {number} bytes @param {string} hash @param {string} stamp */
export function commitPlan(job,token,rows,total,done,bytes,hash,stamp){
 const id=job.id,guard='EXISTS (SELECT 1 FROM jobs WHERE id=? AND lease=? AND page=?)';
 const stmts=rows.map(r=>({sql:`INSERT INTO records(job_id,source_id,title,agency,amount,raw) SELECT ?,?,?,?,?,? WHERE ${guard}`,params:[id,sourceIdentity(r),String(r.prdctClsfcNoNm||r.prdctNm||''),String(r.dminsttNm||''),String(r.dlvrReqAmt||''),JSON.stringify(r),id,token,job.page]}));
 stmts.push({sql:`INSERT INTO receipts(job_id,page,count,bytes,hash,committed_at) SELECT ?,?,?,?,?,? WHERE ${guard}`,params:[id,job.page,rows.length,bytes,hash,stamp,id,token,job.page]});
 stmts.push({sql:'UPDATE jobs SET state=?,page=page+1,total=?,saved=saved+?,max_payload=MAX(max_payload,?),lease=NULL,lease_until=0,updated_at=? WHERE id=? AND lease=? AND page=?',params:[done?'complete':'ready',total,rows.length,bytes,stamp,id,token,job.page]});
 return stmts;
}
