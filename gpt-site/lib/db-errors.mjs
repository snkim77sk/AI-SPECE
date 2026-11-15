/** Classify, but never log SQL text, bindings, credentials, or source data. */
export function databaseErrorCode(error){
 const message=[error?.message,error?.cause?.message].filter(x=>typeof x==='string').join(' ').toLowerCase();
 const cases=[[/busy|locked|lock timeout/,'DATABASE_BUSY'],[/overload|too many concurrent|rate limit/,'DATABASE_OVERLOADED'],[/timeout|timed out/,'DATABASE_TIMEOUT'],[/reset|network|connection|temporarily unavailable/,'DATABASE_CONNECTION'],[/memory|allocation|resource limit/,'DATABASE_RESOURCE_LIMIT'],[/too many sql|too many variables|statement.*limit/,'DATABASE_STATEMENT_LIMIT'],[/no such table|no such column|schema|syntax/,'DATABASE_SCHEMA'],[/integer overflow/,'DATABASE_AMOUNT_OVERFLOW'],[/d1_error/,'DATABASE_D1_ERROR']];
 return cases.find(([pattern])=>pattern.test(message))?.[1]||'DATABASE_UNKNOWN';
}
export function reportDatabaseError(area,error){const code=databaseErrorCode(error),incident=crypto.randomUUID();console.error(JSON.stringify({area,code,incident}));return {code,incident};}
