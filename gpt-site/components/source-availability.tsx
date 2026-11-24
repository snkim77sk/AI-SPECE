import {syncAvailability} from '../lib/sync-source-summary.mjs';

const formatCount=(value:number)=>value.toLocaleString('ko-KR');
const formatTime=(value:string)=>new Date(value).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})+' KST';

export default function SourceAvailability({sync}:{sync:unknown}){
 return <div className="original-source-status">{syncAvailability(sync).map(source=><div className="original-source-state" data-tone={source.partial||source.emptyAfterData||(source.pending??0)>0?'warning':'info'} key={source.dataset}>
  <div><strong>{source.name} 최신 조회 자료</strong><span>{source.dataDay||'미확인'}</span></div>
  {source.checkedDay&&source.checkedDay!==source.dataDay&&<small>최근 확인된 수집 범위 {source.checkedDay}</small>}
  {source.emptyAfterData&&<small>{source.checkedDay}까지 확인된 페이지에는 그 이후 조회 대상 자료가 없습니다. 신규 자료가 없는지는 재확인이 필요합니다.</small>}
  {(source.saved!==null||source.partial)&&<small>{source.saved===null?'Windows 저장행 미확인':`Windows 저장 ${formatCount(source.saved)}${source.total===null?'':` / ${formatCount(source.total)}`}행`}{source.partial&&' · 수집 미완료'}</small>}
  {source.received!==null&&<small>Sites 수신 {formatCount(source.received)}행{source.pending!==null&&source.pending>0&&` · 미수신 ${formatCount(source.pending)}행`}{source.receivedAt&&` · ${formatTime(source.receivedAt)}`}</small>}
  {source.href&&<small><a href={source.href}>{source.name} 저장자료 보기 · 전국·전체기간</a></small>}
 </div>)}</div>;
}
