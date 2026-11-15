import {kstDate} from './collector.mjs';
export function automaticDecision(session,window,now=new Date()){
 const today=kstDate(now);
 if(!session)return {action:'create',from:window.from,to:window.to};
 if(!['complete','cancelled'].includes(session.state)){
  if(session.state==='paused')return {action:'stop',reason:'USER_PAUSED'};
  if(session.day<window.from||session.day>window.to)return {action:'stop',reason:'DATE_OUTSIDE_CURRENT_SCOPE'};
  if(session.lease_until>now.getTime())return {action:'wait',reason:'SESSION_BUSY'};
  if(session.state==='running')return {action:'stop',reason:'EXPIRED_REQUEST_REVIEW_REQUIRED'};
  if(session.state==='error'||session.error){
   if(session.error==='LOCAL_DAILY_BUDGET_REACHED'&&kstDate(new Date(session.updated_at))<today)return {action:'step',id:session.id};
   if(session.error==='SOURCE_NETWORK_OR_TIMEOUT')return now.getTime()-new Date(session.updated_at).getTime()>=900000?{action:'step',id:session.id}:{action:'wait',reason:'TRANSIENT_RETRY_COOLDOWN'};
   return {action:'stop',reason:session.error||'SESSION_ERROR'};
  }
  return {action:'step',id:session.id};
 }
 if(session.state==='cancelled')return {action:'stop',reason:'USER_CANCELLED'};
 if(session.to_day>=window.to)return {action:'wait',reason:'UP_TO_DATE'};
 const lookback=new Date(new Date(session.to_day+'T00:00:00Z').getTime()-2*86400000).toISOString().slice(0,10);
 return {action:'create',from:lookback>window.from?lookback:window.from,to:window.to};
}
