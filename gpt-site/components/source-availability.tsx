import {syncAvailability} from '../lib/sync-source-summary.mjs';

export default function SourceAvailability({sync}:{sync:unknown}){
 return <div className="original-source-status">{syncAvailability(sync).map(source=><div className="original-source-state" data-tone={source.partial||source.emptyAfterData?'warning':'info'} key={source.dataset}>
  <div><strong>{source.name} 최신 반영 범위</strong><span>{source.dataDay||'미확인'}</span></div>
  {source.emptyAfterData&&<small>{source.checkedDay}까지 확인된 페이지에는 그 이후 조회 대상 자료가 없습니다. 신규 자료가 없는지는 재확인이 필요합니다.</small>}
  {source.partial&&<small>{source.name} 수집 미완료 · {source.saved?.toLocaleString('ko-KR')??'미확인'} / {source.total?.toLocaleString('ko-KR')??'전체 미확인'}행</small>}
  {source.href&&<small><a href={source.href}>{source.name} 저장자료 보기 · 전국·전체기간</a></small>}
 </div>)}</div>;
}
