import {queryPlan,VIEW_FIELDS} from './g2b-query.mjs';
import {SMART_CONTROL_CODE} from './g2b-domain.mjs';
// Materialize only the outer candidates. The newer-row comparison must still
// see every category/date in the retention window, or old revisions reappear.
export function controlCountPlan(mode,window){
 const p=queryPlan(new URLSearchParams({mode,dataset:'smart',kind:'CONTROL'}),window);
 return {...p,prefix:p.prefix.replace('WITH current AS',`WITH control_candidates AS MATERIALIZED (SELECT mode,${VIEW_FIELDS.join(',')} FROM delivery_rows WHERE mode=? AND detail_code=? AND source_date>=? AND source_date<=? AND source_date>='2026-10-01' AND is_active=1 AND upper(final_flag) NOT IN ('N','NO','0')),current AS`).replace('FROM delivery_rows r WHERE','FROM control_candidates r WHERE'),args:[p.mode,SMART_CONTROL_CODE,window.from,window.to,...p.args]};
}

export function dashboardLogicalPlan(mode,window){const p=queryPlan(new URLSearchParams({mode}),window);return {...p,prefix:p.prefix.replace('delivery_rows r WHERE','delivery_rows r INDEXED BY idx_delivery_dashboard_cover WHERE').replace('INDEXED BY idx_delivery_current','INDEXED BY idx_delivery_current_cover')};}
