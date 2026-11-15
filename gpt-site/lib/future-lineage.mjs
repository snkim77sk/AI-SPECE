import{institutionLineage,LINEAGE_VERSION}from'./institution-lineage.mjs';
import{scoreFutureBudgetEvidence}from'./future-budget-evidence.mjs';
export function futureTargetLineage(row,year){const beginning=String(year)+'-01-01';let l;try{l=institutionLineage({org:row.org_name||'',region:row.region_name||'',projectText:row.project_name||'',sourceDate:row.snapshot_date,asOfDate:row.snapshot_date>beginning?row.snapshot_date:beginning});}catch(e){if(!['LINEAGE_REGION_CONFLICT','LINEAGE_TEXT_INVALID','LINEAGE_DATE_REQUIRED','LINEAGE_DATE_INVALID'].includes(e.message))throw e;l={version:LINEAGE_VERSION,org_name:row.org_name,region_key:row.region_name,applied:false,basis:e.message,unresolved:true};}return{lineage:l,row:{...row,org_name:l.org_name,region_name:l.region_key}};}
/** Only the validated immutable runtime merge may enter this adapter. Pure
 * arbitrary-pattern scoring still rejects unsupported lineage. Source fields
 * are restored in the result; lineage identity is separate evidence metadata. */
export function scorePersistedFutureEvidence(row,pattern,target,options={}){
 const l=target.lineage;if(l.version!==LINEAGE_VERSION)throw Error('FUTURE_LINEAGE_VERSION');
 if(pattern&&pattern.organization_lineage_version!==LINEAGE_VERSION)throw Error('FUTURE_HISTORY_LINEAGE_VERSION');
 const usable=!l.unresolved&&pattern&&!pattern.organization_lineage_unresolved?pattern:null;
 const scoreOptions={...options,patternMatchBasis:usable&&(l.applied||usable.organization_lineage_applied)?'SUPPORTED_CONFIGURED_LINEAGE':options.patternMatchBasis};
 const scored=scoreFutureBudgetEvidence(target.row,usable?{...usable,organization_lineage_applied:false}:null,scoreOptions);
 return{...scored,...row,historical_target_lineage_basis:l.basis,historical_target_lineage_org:l.org_name,historical_target_lineage_region:l.region_key,historical_target_lineage_unresolved:l.unresolved,historical_organization_lineage_applied:!!usable?.organization_lineage_applied||l.applied,historical_organization_lineage_version:LINEAGE_VERSION,historical_organization_lineage_basis:usable?.organization_lineage_basis||{},...(l.unresolved?{historical_evidence_reasons:['TARGET_ORGANIZATION_LINEAGE_UNRESOLVED']}:pattern?.organization_lineage_unresolved?{historical_evidence_reasons:['HISTORY_ORGANIZATION_LINEAGE_UNRESOLVED']}:{} )};
}
