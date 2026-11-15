import {budgetRegionWhere,budgetRegionMembers} from './budget-regions.mjs';
import {validDay} from './collector.mjs';
import {budgetSearchInput} from './budget-presets.mjs';
import {incheonScopeWhere} from './read-filters.mjs';
import {selectorWhere,rawField} from './budget-navigation.mjs';
const j=key=>`json_extract(row_json,'$.${key}')`;
export const BUDGET_BASIS=`CASE WHEN ${j('invalid_amount')}=1 THEN NULL ELSE COALESCE(${j('budget_amount')},${j('appropriation_amount')}) END`;
export const BUDGET_REMAINING=j('remaining_amount');
function positive(x){return `(${x} IS NOT NULL AND ${x}<>'0' AND substr(${x},1,1)<>'-')`;}
/** Integer-text order preserves large values and negative balances without SQLite casts. */
export function moneyOrder(x){return `CASE WHEN ${x} IS NULL THEN -1 WHEN substr(${x},1,1)='-' THEN 0 ELSE 1 END DESC,CASE WHEN substr(${x},1,1)='-' THEN -length(substr(${x},2)) ELSE length(${x}) END DESC,CASE WHEN substr(${x},1,1)<>'-' THEN ${x} END DESC,CASE WHEN substr(${x},1,1)='-' THEN substr(${x},2) END ASC,source_key ASC`;}
export function budgetQuery(p,today){
 const mode=p.get('mode')==='synthetic'?'synthetic':'live',service=p.get('service')==='AIDFA'?'AIDFA':'QWGJK',year=Number(p.get('year')||today.slice(0,4));if(!Number.isInteger(year)||year<2026||year>2100)throw Error('INVALID_YEAR');
 const where=['mode=?','service=?','fiscal_year=?','is_active=1'],args=[mode,service,year],targets=p.get('view')==='targets';
 if(targets){if(service!=='QWGJK')throw Error('DETAIL_BUDGET_REQUIRED');where.push("category IN ('LIGHTING','POLE')",`${j('invalid_amount')}=0`,positive(BUDGET_REMAINING));}
 for(const field of ['category','org','dept']){if((field==='org'&&p.get('orgKey'))||(field==='dept'&&p.get('deptKey')))continue;const v=p.get(field)?.slice(0,1024);if(v){where.push(field+'=?');args.push(v);}}
 budgetRegionWhere(p.get('region'),where,args);incheonScopeWhere(p,where,args,rawField('inst_nm','institution_name','schul_nm'),budgetRegionMembers('인천광역시'));
 if(service==='QWGJK'){const from=p.get('from'),to=p.get('to');if((from&&!validDay(from))||(to&&!validDay(to))||(from&&to&&from>to))throw Error('INVALID_DATE_RANGE');if(from){where.push('snapshot_date>=?');args.push(from);}if(to){where.push('snapshot_date<=?');args.push(to);}}
 selectorWhere(p,where,args);
 const {terms}=budgetSearchInput(p);const searchFields=['project','org','dept',rawField('inst_nm','institution_name','schul_nm'),rawField('fld_nm','field_name'),rawField('sect_nm','part_nm','section_name'),j('account')];for(const term of terms){where.push('('+searchFields.map(f=>f+" LIKE ? ESCAPE '\\'").join(' OR ')+')');const like='%'+term.replace(/[\\%_]/g,'\\$&')+'%';args.push(...searchFields.map(()=>like));}
 const execution=p.get('execution'),executed=j('executed_amount');if(execution){if(service!=='QWGJK')throw Error('DETAIL_BUDGET_REQUIRED');
  if(execution==='UNEXECUTED')where.push(`${executed}='0'`,positive(BUDGET_BASIS));
  else if(['PARTIAL','FULL'].includes(execution))where.push(positive(executed),positive(BUDGET_BASIS),`(length(${executed}),${executed})${execution==='PARTIAL'?'<':'>='}(length(${BUDGET_BASIS}),${BUDGET_BASIS})`);
  else throw Error('INVALID_EXECUTION_FILTER');
 }
 const sort=['REMAINING','BUDGET','RECENT','ORG'].includes(p.get('sort'))?p.get('sort'):'RECENT';
 const amount=sort==='BUDGET'?BUDGET_BASIS:BUDGET_REMAINING;const order=sort==='ORG'?'org ASC,project ASC,source_key ASC':sort==='RECENT'?'snapshot_date DESC,source_key ASC':moneyOrder('sort_amount');
 return {mode,service,year,targets,where,args,sort,amount,order};
}
export function budgetCursor(plan,p){const token=p.get('cursor');if(!token)return;let c;try{c=JSON.parse(token);}catch{throw Error('INVALID_CURSOR');}if(c.sort!==plan.sort||typeof c.key!=='string'||c.key.length>8192)throw Error('INVALID_CURSOR');
 const {where,args}=plan;
 if(plan.sort==='RECENT'){if(typeof c.date!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(c.date))throw Error('INVALID_CURSOR');where.push('(snapshot_date<? OR (snapshot_date=? AND source_key>?))');args.push(c.date,c.date,c.key);}
 else if(plan.sort==='ORG'){if(typeof c.org!=='string'||typeof c.project!=='string'||c.org.length>8192||c.project.length>16387)throw Error('INVALID_CURSOR');where.push('(org,project,source_key)>(?,?,?)');args.push(c.org,c.project,c.key);}
 else {const a=plan.amount;if(c.amount===null){where.push(`(${a} IS NULL AND source_key>?)`);args.push(c.key);}else{if(typeof c.amount!=='string'||! /^-?\d+$/.test(c.amount)||c.amount.length>8194)throw Error('INVALID_CURSOR');
  if(c.amount.startsWith('-')){const abs=c.amount.slice(1);where.push(`(${a} IS NULL OR (substr(${a},1,1)='-' AND (length(substr(${a},2)),substr(${a},2))>(?,?)) OR (${a}=? AND source_key>?))`);args.push(abs.length,abs,c.amount,c.key);}
  else {where.push(`(${a} IS NULL OR substr(${a},1,1)='-' OR ((length(${a}),${a})<(?,?)) OR (${a}=? AND source_key>?))`);args.push(c.amount.length,c.amount,c.amount,c.key);}
 }}
}
