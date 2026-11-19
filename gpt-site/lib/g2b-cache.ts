/** A small D1 result cache, invalidated transactionally with every collected page. */
export async function cachedQuery<T>(d:D1Database,mode:string,identity:unknown,compute:()=>Promise<T>):Promise<T>{
 const bytes=new TextEncoder().encode(JSON.stringify(identity)),digest=await crypto.subtle.digest('SHA-256',bytes),key=mode+':'+Array.from(new Uint8Array(digest)).map(x=>x.toString(16).padStart(2,'0')).join(''),now=Date.now();
 let hit:{revision:number,payload:string|null}|null;
 try{hit=await d.prepare('SELECT e.revision,c.payload FROM dataset_epochs e LEFT JOIN query_cache c ON c.mode=e.mode AND c.cache_key=? AND c.revision=e.revision AND c.expires_at>? WHERE e.mode=?').bind(key,now,mode).first<{revision:number,payload:string|null}>();}
 catch{/* Cache is optional; a cache read failure must not block the primary stored-data query. */return compute();}
 if(hit?.payload){try{return JSON.parse(hit.payload) as T;}catch{/* Recompute damaged cache only. */}}
 const result=await compute(),payload=JSON.stringify(result);
 if(hit&&new TextEncoder().encode(payload).byteLength<=524288){
  try{await d.batch([
   d.prepare('INSERT INTO query_cache(cache_key,mode,revision,payload,created_at,expires_at) SELECT ?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM dataset_epochs WHERE mode=? AND revision=?) ON CONFLICT(cache_key) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,created_at=excluded.created_at,expires_at=excluded.expires_at').bind(key,mode,hit.revision,payload,now,now+60000,mode,hit.revision),
   d.prepare('DELETE FROM query_cache WHERE mode=? AND cache_key NOT IN(SELECT cache_key FROM query_cache WHERE mode=? ORDER BY created_at DESC LIMIT 64)').bind(mode,mode)
  ]);}catch{/* A failed optional cache write does not discard a successfully computed response. */}
 }
 return result;
}
