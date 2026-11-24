// Request-bounded aggregate work; only validated results under the exact source
// identity are reusable. Never returns partial counts as a completed dashboard.
export async function dashboardStage(db,{key,mode,revision,identity,stage,validate,compute,assertCurrent}){
 const cacheKey=key+':'+stage,now=Date.now();
 const hit=await db.prepare('SELECT payload FROM query_cache WHERE cache_key=? AND mode=? AND CAST(revision AS TEXT)=? AND expires_at>? AND length(CAST(payload AS BLOB))<=524288').bind(cacheKey,mode,revision,now).first();
 if(hit){try{const value=JSON.parse(hit.payload);if(value.identity===identity&&value.stage===stage&&validate(value.value))return value.value;}catch{}}
 const value=await compute();if(!validate(value))throw Error('DASHBOARD_INVALID_STAGE');await assertCurrent();
 const payload=JSON.stringify({identity,stage,value});if(new TextEncoder().encode(payload).byteLength>524288)throw Error('DASHBOARD_STAGE_TOO_LARGE');
 await db.prepare('INSERT INTO query_cache(cache_key,mode,revision,payload,created_at,expires_at) VALUES(?,?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,created_at=excluded.created_at,expires_at=excluded.expires_at').bind(cacheKey,mode,revision,payload,now,now+60000).run();
 await db.prepare('DELETE FROM query_cache WHERE mode=? AND cache_key NOT IN(SELECT cache_key FROM query_cache WHERE mode=? ORDER BY created_at DESC,cache_key DESC LIMIT 64)').bind(mode,mode).run();
 await assertCurrent();throw Error('DASHBOARD_AGGREGATE_PENDING');
}
