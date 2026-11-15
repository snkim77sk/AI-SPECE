import {kstDate} from './collector.mjs';
export function budgetAutomaticDecision(s,c,service,now=new Date()){
 const today=kstDate(now),snapshot=service==='AIDFA'?today:c.to,year=c.targetYear??Number(snapshot.slice(0,4));
 const create={action:'create',service,year,snapshot};if(!s)return create;
 if(s.state==='cancelled'&&s.error==='AIDFA_DAY_ADVANCED')return create;
 if(s.state==='paused'||s.state==='cancelled')return {action:'stop',reason:s.state==='paused'?'USER_PAUSED':'USER_CANCELLED'};
 if(s.state!=='complete'){
  if(s.lease_until>now.getTime())return {action:'wait',reason:'SESSION_BUSY'};
  if(s.snapshot_date<c.from)return {action:'stop',reason:'DATE_OUTSIDE_CURRENT_SCOPE'};
  if(s.state==='error'){
   if(service==='QWGJK'&&s.error==='REPEATED_OR_OVERLAPPING_PAGE'&&s.replay_count===0)return {action:'replay',id:s.id,reason:'CONFIRMED_OVERLAP_REPLAY_ONCE'};
   if(s.error==='LOFIN_LOCAL_DAILY_BUDGET_REACHED'&&kstDate(new Date(s.updated_at))<today)return {action:'step',id:s.id};
   if(s.error==='SOURCE_NETWORK_OR_TIMEOUT')return now.getTime()-new Date(s.updated_at).getTime()>=900000?{action:'step',id:s.id}:{action:'wait',reason:'TRANSIENT_RETRY_COOLDOWN'};
   return {action:'stop',reason:s.error||'SESSION_ERROR'};
  }return {action:'step',id:s.id};
 }
 const days=(new Date(snapshot+'T00:00:00Z').getTime()-new Date(s.snapshot_date+'T00:00:00Z').getTime())/86400000;
 if(s.fiscal_year===year&&days<1)return {action:'wait',reason:'UP_TO_DATE'};return create;
}
