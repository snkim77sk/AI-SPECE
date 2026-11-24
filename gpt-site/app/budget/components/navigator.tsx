'use client';
import {useEffect,useState} from 'react';
import {useViewerAccess} from '../../../components/viewer-access';
import {budgetRegionMembers} from '../../../lib/budget-regions.mjs';
type Props={mode:string,service:string,year:string,region:string,orgKey:string,deptKey:string,from?:string,to?:string,onChange:(region:string,orgKey:string,deptKey:string)=>void,onServiceChange?:(service:string)=>void};
export default function BudgetNavigator({mode,service,year,region,orgKey,deptKey,from,to,onChange,onServiceChange}:Props){
 const {canRead,canManage,href}=useViewerAccess();
 const [data,setData]=useState<any>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[orgText,setOrgText]=useState(''),[orgSearch,setOrgSearch]=useState(''),[deptText,setDeptText]=useState(''),[deptSearch,setDeptSearch]=useState('');
 useEffect(()=>{setOrgText('');setOrgSearch('');setDeptText('');setDeptSearch('');},[mode,service,year,region]);
 useEffect(()=>{setDeptText('');setDeptSearch('');},[orgKey]);
 useEffect(()=>{
  if(!canRead||!year)return;
  const controller=new AbortController();setBusy(true);setError('');
  const p=new URLSearchParams({mode,service,year,region,orgKey,orgSearch,deptSearch});
  if(from!==undefined)p.set('from',from);if(to!==undefined)p.set('to',to);
  fetch(href('/api/budget/navigation?'+p),{signal:controller.signal}).then(async r=>{const x:any=await r.json();if(!r.ok)throw Error(x.error);if(!controller.signal.aborted)setData(x);}).catch(e=>{if(!controller.signal.aborted)setError('기관·부서 선택지를 불러오지 못했습니다: '+e.message);}).finally(()=>{if(!controller.signal.aborted)setBusy(false);});
  return()=>controller.abort();
 },[canRead,href,mode,service,year,region,orgKey,orgSearch,deptSearch,from,to]);
 const current=data?.service===service&&String(data?.year)===year?data:null;
 const orgs=current?.organizations||[],depts=current?.departments||[],coverage=current?.coverage;
 const sourceNames:Record<string,string>={QWGJK:'세부사업·집행 예산',AIDFA:'구조별 편성 예산'};
 if(!canRead)return null;
 return <section className="budget-navigation" aria-label="지역 기관 부서 탐색">
  <div className="budget-coverage" aria-label="전국 예산 반영 범위">
   <strong>전국 예산 수집·반영 범위</strong>
   <p>전국 수집을 진행 중입니다. 아래는 {year}년 예산 중 웹에 반영된 자료이며, 전국 수집 완료 건수가 아닙니다.</p>
   {coverage?<div className="budget-coverage-grid">{coverage.services.map((source:any)=><div key={source.service} className={source.service===service?'selected':''}><strong>{sourceNames[source.service]}</strong><span>{source.regionCount}개 원천지역 · {source.rows.toLocaleString()}건</span>{onServiceChange&&source.service!==service&&<button type="button" onClick={()=>onServiceChange(source.service)}>{sourceNames[source.service]} 보기</button>}</div>)}</div>:<p role="status">{error?'반영 범위를 확인하지 못했습니다.':'반영 범위 확인 중…'}</p>}
   <p>구조별 편성은 분야·부문별 예산 합계입니다. 개별 사업과 집행·잔액은 세부사업 자료에서 확인합니다. 기관코드와 원천 지역명은 보존합니다.</p>
   <div className="budget-region-shortcuts" aria-label="주요 조회 지역"><button type="button" onClick={()=>onChange('','','')}>전국</button><button type="button" onClick={()=>onChange('충청남도','','')}>충청남도</button><button type="button" onClick={()=>onChange('충청북도','','')}>충청북도</button></div>
  </div>
  <div className="budget-controls">
   <label>지역 선택<select aria-label="지역 선택" value={region} onChange={e=>onChange(e.target.value,'','')}><option value="">전국 · 현재 저장자료 전체</option>{region&&!coverage?.regions?.includes(region)&&<option value={region}>{region}</option>}{coverage?.choices?.map((x:any)=><option key={x.region} value={x.region}>{x.region} · {x.rows?x.rows.toLocaleString()+'건':'현재 원천 미반영'}</option>)}</select></label>
   <label>기관 선택<select aria-label="기관 선택" disabled={busy} value={orgKey} onChange={e=>onChange(region,e.target.value,'')}><option value="">전체 기관</option>{orgKey&&!orgs.some((x:any)=>x.key===orgKey)&&<option value={orgKey}>현재 선택 기관</option>}{orgs.map((x:any)=><option key={x.key} value={x.key}>{x.region} · {x.name||'기관명 미제공'} · {x.rows.toLocaleString()}건{x.baseOnly?' (기본목록, 자료 없음)':x.aliases>1?` (명칭 ${x.aliases}개)`:''}{JSON.parse(x.key)[0]==='code'?' · 코드 '+JSON.parse(x.key)[1]+'/'+JSON.parse(x.key)[2]:''}</option>)}</select></label>
   <label>부서 선택<select aria-label="부서 선택" disabled={!orgKey||busy} value={deptKey} onChange={e=>onChange(region,orgKey,e.target.value)}><option value="">전체 부서</option>{deptKey&&!depts.some((x:any)=>x.key===deptKey)&&<option value={deptKey}>현재 선택 부서</option>}{depts.map((x:any)=><option key={x.key} value={x.key}>{x.name} · {x.rows.toLocaleString()}건{x.code?' · 코드 '+x.code:''}</option>)}</select></label>
   <button type="button" onClick={()=>onChange('','','')}>기관·부서 초기화</button>
  </div>
  {region&&coverage&&!coverage.choices?.find((x:any)=>budgetRegionMembers(x.region).includes(region))?.rows&&<p className="feedback" role="status">{region}의 {sourceNames[service]} 자료는 아직 웹에 반영되지 않았습니다. 다른 예산 원천의 반영 범위를 확인하거나 자동수집 진행 상태를 확인해 주세요. {canManage&&<a href={href('/collection-monitor?mode=live')}>수집 상태 보기</a>}</p>}
  <details className="navigation-search"><summary>기관·부서 선택지 검색</summary><div className="budget-controls"><form onSubmit={e=>{e.preventDefault();setOrgSearch(orgText);}}><label>기관 선택지 검색<input aria-label="기관 선택지 검색" value={orgText} onChange={e=>setOrgText(e.target.value)} placeholder="기관명으로 좁히기" maxLength={100}/></label><button disabled={busy}>기관 찾기</button></form><form onSubmit={e=>{e.preventDefault();setDeptSearch(deptText);}}><label>부서 선택지 검색<input aria-label="부서 선택지 검색" disabled={!orgKey} value={deptText} onChange={e=>setDeptText(e.target.value)} maxLength={100}/></label><button disabled={!orgKey||busy}>부서 찾기</button></form></div></details>
  <p className="analysis-note">{busy?'선택지 조회 중. ':''}{current?.organizationMore?'기관이500개를 넘어 일부만 표시합니다. 기관 찾기로 좁혀 주세요. ':''}{current?.departmentMore?'부서가500개를 넘어 일부만 표시합니다. 부서 찾기로 좁혀 주세요. ':''}지역별 건수는 선택 회계연도의 반영량입니다. 기관·부서 목록은 조회기간을 적용합니다. ‘현재 원천 미반영’은 실제 예산이 없다는 뜻이 아닙니다.</p>
  {error&&<p role="status" className="feedback">{error}</p>}
 </section>;
}
