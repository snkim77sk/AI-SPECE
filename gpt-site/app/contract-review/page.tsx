'use client';
import {useEffect,useMemo,useRef,useState} from 'react';
import {parseReviewInput,reviewCsv,moneyLabel,REVIEW_MAX_BYTES} from '../../lib/review-preview.mjs';
import {DELIVERY_REGIONS} from '../../lib/delivery-navigation.mjs';
import SourceAvailability from '../../components/source-availability';
import {useViewerAccess} from '../../components/viewer-access';

type ReviewRow=Record<string,any>;
type Imported={kind:string;total:number;excluded:number;duplicates:number;rows:ReviewRow[];format:string;ephemeral:boolean};
type SiteRows={rows:ReviewRow[];next:any;mode?:string;service?:string};
const messages:Record<string,string>={REVIEW_FILE_TOO_LARGE:'파일이 2MiB를 초과합니다.',REVIEW_TOO_MANY_ROWS:'최대 2,500행까지만 검토할 수 있습니다.',REVIEW_NO_ROWS:'제목 행과 자료 행을 확인해 주세요.',REVIEW_INVALID_HEADERS:'CSV 제목 열을 확인해 주세요.',REVIEW_INVALID_CSV:'CSV 형식을 확인해 주세요.',REVIEW_INVALID_JSON:'JSON 문법을 확인해 주세요.',REVIEW_INVALID_JSON_ROWS:'JSON 배열 또는 rows 배열이 필요합니다.'};
const errorLabel=(e:unknown)=>messages[(e as Error)?.message]||'파일 형식·자료 건수를 확인해 주세요.';
const safeIso=(v:unknown)=>typeof v==='string'&&Number.isFinite(Date.parse(v))?new Date(v).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})+' KST':'미확인';
const heading=(kind:'contract'|'budget')=>kind==='contract'?'신규 계약·납품요구':'지방재정 예산';
const baseFields=(kind:'contract'|'budget')=>kind==='contract'?['source_date','request_no','agency','vendor','item_name','amount']:['snapshot_date','project','org','budget_amount','appropriation_amount','remaining_amount','amount_unit','service'];
const localLabels:Record<string,string>={source_date:'계약/납품일',request_no:'요구·계약번호',agency:'수요기관',vendor:'업체',item_name:'품명',amount:'금액(원천 단위)',snapshot_date:'예산 기준일',project:'사업명',org:'기관',budget_amount:'예산액',appropriation_amount:'편성액',remaining_amount:'잔액',amount_unit:'금액단위',service:'원천'};
const siteLabels:Record<string,string>={source_date:'납품일자',request_no:'납품요구번호',agency:'수요기관',vendor:'업체',item_name:'품명',amount:'품목금액',snapshot_date:'예산 기준일',project:'사업명',org:'기관',budget_amount:'예산액',appropriation_amount:'편성액',remaining_amount:'잔액',amount_unit:'단위(미확인시 확인 필요)',service:'예산 원천'};
const numberKeys=new Set(['amount','budget_amount','appropriation_amount','remaining_amount']);
function Records({rows,kind,local}:{rows:ReviewRow[];kind:'contract'|'budget';local:boolean}){
 const fields=kind==='contract'
  ?['source_date','request_no','change_order','contract_no','agency','vendor','item_id','item_name','model','amount']
  :['snapshot_date','region','project','org','budget_amount','appropriation_amount','executed_amount','remaining_amount','amount_unit'];
 const labels:Record<string,string>={...(local?localLabels:siteLabels),change_order:'변경차수',contract_no:'계약번호',item_id:'물품식별번호',model:'모델',region:'지역',executed_amount:'집행액'};
 return <div className="data-table-wrap"><table><thead><tr>{fields.map(f=><th key={f}>{labels[f]}</th>)}</tr></thead><tbody>
 {rows.slice(0,100).map((r,i)=><tr key={(r.request_no||r.project||'row')+':'+i}>{fields.map(k=><td key={k}>{numberKeys.has(k)||k==='executed_amount'?moneyLabel(r[k]):String(r[k]??(k==='amount_unit'?'원천 단위 미확인':'—'))}</td>)}</tr>)}
 {!rows.length&&<tr><td colSpan={fields.length}>표시할 항목이 없습니다. 이는 원천 전체 자료가 0건임을 의미하지 않습니다.</td></tr>}
 </tbody></table></div>;
}
export default function ContractReview(){
 const {canRead,canManage,preview,href}=useViewerAccess();
 const access=useRef({canRead,canManage,href});access.current={canRead,canManage,href};
 const [kind,setKind]=useState<'contract'|'budget'>('contract');
 const [local,setLocal]=useState<Imported|null>(null);
 const [localError,setLocalError]=useState('');
 const [filter,setFilter]=useState('');
 const [lightingOnly,setLightingOnly]=useState(false);
 const [institution,setInstitution]=useState('');
 const [siteCategory,setSiteCategory]=useState('');
 const [siteInstitution,setSiteInstitution]=useState('');
 const [siteRegion,setSiteRegion]=useState('');
 const [fileName,setFileName]=useState('');
 const [server,setServer]=useState<any>(null);
 const [sourceError,setSourceError]=useState('');
 const [loading,setLoading]=useState(false);
 const [site,setSite]=useState<SiteRows|null>(null);
 const [siteKind,setSiteKind]=useState<'contract'|'budget'>('contract');
 const [next,setNext]=useState<any>(null);
 const [lastAt,setLastAt]=useState('');
 const [status,setStatus]=useState<any>(null);
 const [statusError,setStatusError]=useState('');
 const [showOperations,setShowOperations]=useState(false);
 const [from,setFrom]=useState('');
 const [to,setTo]=useState('');
 const [year,setYear]=useState(()=>String(new Date(Date.now()+9*3600000).getUTCFullYear()));
 const [service,setService]=useState('QWGJK');
 const fileInput=useRef<HTMLInputElement>(null);
 const requestId=useRef(0);
 const localImportId=useRef(0);
 useEffect(()=>{
  localImportId.current++;setLocal(null);setFileName('');setLocalError('');
  if(fileInput.current)fileInput.current.value='';
  setShowOperations(false);setServer(null);setStatus(null);setStatusError('');
  if(!canManage)return;
  let alive=true;
  const read=async(path:string)=>{
   const response=await fetch(access.current.href(path),{cache:'no-store',signal:AbortSignal.timeout(10000)});
   if(response.status===403||response.status===401)return null;
   if(!response.ok)throw Error('STATUS_UNAVAILABLE');
   return response.json();
  };
  Promise.allSettled([read('/api/server-sync'),read('/api/automation')]).then(([sync,automation])=>{
   if(!alive||!access.current.canManage)return;
   if(sync.status==='fulfilled'&&automation.status==='fulfilled'&&sync.value===null&&automation.value===null)return;
   setShowOperations(true);
   if(sync.status==='fulfilled')setServer(sync.value);
   else setStatusError('마지막 서버 동기화 시각을 확인할 수 없습니다.');
   if(automation.status==='fulfilled')setStatus(automation.value);
   else setStatusError(value=>value+' Sites 자체 수집 준비상태를 확인할 수 없습니다.');
  });
  return()=>{alive=false;};
 },[canManage,preview]);
 const localRows=useMemo(()=>{if(!local||local.kind!==kind)return [];const terms=filter.trim().toLowerCase();return local.rows.filter(r=>(!lightingOnly||r.isLighting===true)&&(!institution||r.institution===institution)&&(!terms||Object.entries(r).some(([k,v])=>!['duplicateKey'].includes(k)&&typeof v==='string'&&v.toLowerCase().includes(terms))));},[kind,local,filter,lightingOnly,institution]);
 async function importLocal(file:File|undefined){
  if(!access.current.canManage)return;
  const id=++localImportId.current;
  setLocalError('');setFileName('');setLocal(null);
  if(!file)return;
  if(file.size>REVIEW_MAX_BYTES){setLocalError('파일이 2MiB를 초과합니다.');return;}
  if(!/\.(csv|tsv|json)$/i.test(file.name)){setLocalError('CSV·TSV·JSON만 지원합니다. XLSX는 기존 예산 화면의 내보내기만 지원합니다.');return;}
  try{const body=await file.text();if(!access.current.canManage||id!==localImportId.current)return;const out=parseReviewInput(body,kind);setLocal(out);setFileName(file.name);}catch(e){if(access.current.canManage&&id===localImportId.current)setLocalError(errorLabel(e));}
 }
 async function readSite(cursor?:any){
  if(!access.current.canRead)return;
  const id=++requestId.current;
  setLoading(true);setSourceError('');setSite(null);setNext(null);
  try{
   const p=new URLSearchParams({mode:'live',q:filter.trim().slice(0,100),category:siteCategory});
   if(kind==='contract'){
    if(from&&to&&from>to)throw Error('INVALID_DATE_RANGE');
    if(from)p.set('from',from);if(to)p.set('to',to);
    p.set('size','50');if(siteInstitution)p.set('institution',siteInstitution);
    if(cursor){p.set('afterDate',cursor.afterDate);p.set('afterKey',cursor.afterKey);}
   }else{
    p.set('service',service);p.set('year',year);if(siteRegion)p.set('region',siteRegion);
    if(cursor)p.set('cursor',cursor);
   }
   const response=await fetch(access.current.href((kind==='contract'?'/api/deliveries?':'/api/budget?')+p),{cache:'no-store',signal:AbortSignal.timeout(15000)});
   const data:any=await response.json();if(!response.ok)throw Error(typeof data.error==='string'?data.error:'SITE_READ_FAILED');
   if(!Array.isArray(data.rows))throw Error('SITE_INVALID_RESPONSE');
   if(id===requestId.current){setSite(data);setSiteKind(kind);setNext(data.next||null);setLastAt(new Date().toISOString());}
  }catch(e){if(id===requestId.current)setSourceError('저장자료 조회 실패 ('+((e as Error)?.message||'NETWORK_ERROR').slice(0,65)+').');}
  finally{if(id===requestId.current)setLoading(false);}
 }
 function invalidateSite(){requestId.current++;setLoading(false);setSite(null);setNext(null);setSourceError('');}
 useEffect(()=>{if(canRead)void readSite();else invalidateSite();return()=>{requestId.current++;};},[canRead,preview]);
 const latest=server?.sources?.map((s:any)=>s.received_at).filter(Boolean).sort().at(-1);
 const sources=Array.isArray(server?.sources)?server.sources:[];
 const ready=(name:'g2b'|'lofin')=>status?.[name]?.liveReady===true;
 function download(){
  if(!access.current.canManage||!local||local.kind!==kind)return;
  const blob=new Blob([reviewCsv(localRows,kind)],{type:'text/csv;charset=utf-8;'});
  const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='G2B_'+(kind==='contract'?'contract':'budget')+'_review.csv';link.click();URL.revokeObjectURL(url);
 }
 if(!canRead)return <main className="budget-shell"><p role="status">자료 조회 권한을 확인하고 있습니다.</p></main>;
 return <main className="budget-shell">
  <header><a href={href('/dashboard?mode=live')}>대시보드</a><a href={href('/budget?mode=live')}>기존 예산 상세</a>{canManage&&<a href={href('/collection-monitor?mode=live')}>수집 상태</a>}<span>비공개 · 계약·예산 검토</span></header>
  <section className="original-card"><h2>신규 계약·예산 통합 검토</h2><p>Windows 서버가 응답하지 않아도 GPT Sites에 이미 저장된 자료는 조회할 수 있습니다. 새로운 자료가 도착하지 않으면 최신으로 표시하지 않습니다. {canManage&&'수동 파일은 브라우저 메모리에서만 검토하며 업로드·DB 저장·외부 API 호출을 하지 않습니다.'}</p><p><strong>주요 범위:</strong> LED 조명·등주 · LH·SH · 기관별 계약 및 집행/편성 예산. 계약자료와 예산사업을 자동으로 동일 계약이라고 단정하지 않습니다.</p></section>
  {canManage&&showOperations&&<section className="original-card" aria-label="자료 신선도">
   <h3>자료 출처와 갱신 상태</h3>
   {statusError&&<p className="feedback" role="status">{statusError}</p>}
   <p>Windows → Sites 최근 전송: <strong>{safeIso(latest)}</strong> · 영수증 {sources.reduce((n:number,s:any)=>n+Number(s.pages||0),0).toLocaleString()}페이지 (최신 전체 계약 수 아님)</p>
   <p>{sources.length?sources.map((s:any)=><span key={s.dataset}>{s.dataset}: {safeIso(s.received_at)} · 원천행 {Number(s.source_rows||0).toLocaleString()}건 / 반영행 {Number(s.matched_rows||0).toLocaleString()}건　</span>):'서버 동기화 영수증이 없거나 조회 불가합니다.'}</p>
   {server&&<SourceAvailability sync={server}/>}
   <p>Sites 직접 수집: 나라장터 <strong>{ready('g2b')?'설정상 허용 · 실행 기록 별도':'승인·키·한도 검증 필요'}</strong> · 지방재정 <strong>{ready('lofin')?'설정상 허용 · 실행 기록 별도':'승인·키·한도 검증 필요'}</strong></p>
   <p>자동 수집 일정: <strong>{status?.scheduleInstalled===true?'설치 보고됨 (동작 검증 별도)':'설치 확인 안 됨'}</strong>. 이 화면에서 제공기관 호출은 하지 않습니다.</p>
   <a href={href('/settings?section=collection&mode=live')}>나라장터 기존 수집 관리</a> · <a href={href('/budget?section=collection&mode=live')}>지방재정 기존 수집 관리</a>
  </section>}
  <section className="panel">
   <h2>1. GPT Sites 저장자료 검색</h2>
   <div className="budget-controls"><label>자료 원천<select value={kind} onChange={e=>{invalidateSite();setKind(e.target.value as 'contract'|'budget');setSiteCategory('');setLocal(null);setFileName('');setLocalError('');if(fileInput.current)fileInput.current.value='';}}><option value="contract">나라장터 납품요구·계약 참고</option><option value="budget">지방재정 예산사업</option></select></label>
   <label>{kind==='contract'?'기관·업체·계약번호·식별번호·모델':'사업명·기관 검색'}<input aria-label="통합 검색어" value={filter} onChange={e=>{invalidateSite();setFilter(e.target.value);}} maxLength={100}/></label>
   <label>품목·사업 분류<select value={siteCategory} onChange={e=>{invalidateSite();setSiteCategory(e.target.value);}}><option value="">전체</option><option value="LIGHTING">조명</option><option value="POLE">등주</option></select></label>
   {kind==='contract'?<>
    <label>조회 시작일<input type="date" value={from} onChange={e=>{invalidateSite();setFrom(e.target.value);}}/></label>
    <label>조회 종료일<input type="date" value={to} onChange={e=>{invalidateSite();setTo(e.target.value);}}/></label>
    <label>수요기관 구분<select value={siteInstitution} onChange={e=>{invalidateSite();setSiteInstitution(e.target.value);}}><option value="">전체</option><option value="LH">LH</option><option value="SH">SH</option></select></label>
   </>:<>
    <label>예산 원천<select value={service} onChange={e=>{invalidateSite();setService(e.target.value);}}><option value="QWGJK">세부사업 집행</option><option value="AIDFA">연도별 편성</option></select></label>
    <label>회계연도<input type="number" min="2026" max="2100" value={year} onChange={e=>{invalidateSite();setYear(e.target.value);}}/></label>
    <label>지역<select value={siteRegion} onChange={e=>{invalidateSite();setSiteRegion(e.target.value);}}><option value="">전국</option>{DELIVERY_REGIONS.map(region=><option key={region}>{region}</option>)}</select></label>
   </>}
   <button type="button" disabled={loading} onClick={()=>void readSite()}>{loading?'조회 중…':'Sites 저장자료 조회'}</button></div>
   <p className="analysis-note">{kind==='contract'?'날짜를 비워두면 저장된 전체 조회 기간에서 최근순으로 찾습니다.':'예산 금액은 원천 값을 표시합니다. 단위를 확인할 수 없는 경우 미확인으로 표시합니다.'}</p>
   {sourceError&&<p className="feedback" role="alert">{sourceError}</p>}
   {site&&siteKind===kind&&<><p role="status">사이트 저장자료 {site.rows.length}건 · 조회 {safeIso(lastAt)}{canManage?' · 자료 반영시각은 위의 동기화 상태를 참고하세요.':' · 저장 당시 자료를 표시하며 현재 원천 전체의 최신 상태를 보장하지 않습니다.'}</p><Records rows={site.rows} kind={kind} local={false}/>{next&&<button type="button" disabled={loading} onClick={()=>void readSite(next)}>다음 저장자료 페이지</button>}</>}
  </section>
  {canManage&&<section className="panel">
   <h2>2. 신규 계약·예산 파일 즉시 검토 (서버 연결 불필요)</h2><p>기관에서 받은 CSV·TSV·JSON 자료를 파일 선택 후 바로 분류합니다. 원본 전체가 저장되거나 다른 사용자에게 공유되지 않습니다. 파일을 다시 선택하거나 페이지를 닫으면 이 화면의 임시 검토자료가 사라집니다.</p>
   <div className="budget-controls"><input ref={fileInput} aria-label="계약 또는 예산 CSV JSON 파일" type="file" accept=".csv,.tsv,.json,text/csv,application/json" onChange={e=>void importLocal(e.target.files?.[0])}/>
   <button type="button" onClick={()=>{if(!access.current.canManage)return;localImportId.current++;setLocal(null);setFileName('');setLocalError('');if(fileInput.current)fileInput.current.value='';}}>임시 검토 초기화</button></div>
   {localError&&<p role="alert" className="feedback">{localError}</p>}
   {local&&local.kind===kind&&<><p role="status"><strong>{fileName}</strong> · {local.format} 원본 {local.total}행 / 식별된 검토 {local.rows.length}행 / 중복 제외 {local.duplicates}행 / 형식 또는 필수값 제외 {local.excluded}행 · 브라우저 임시자료</p>
   <div className="budget-controls"><label><input type="checkbox" checked={lightingOnly} onChange={e=>setLightingOnly(e.target.checked)}/> LED·조명·등주 관련만</label>{kind==='contract'&&<label>기관 구분<select value={institution} onChange={e=>setInstitution(e.target.value)}><option value="">전체</option><option value="LH">LH</option><option value="SH">SH</option></select></label>}<button type="button" onClick={download}>현재 필터 CSV 내려받기</button></div>
   <p>현재 조건에 해당하는 검토자료 <strong>{localRows.length}건</strong> · 금액은 원본 표기 단위를 유지합니다. 계약금액과 예산 잔액의 단위가 일치하는지 확인 전에는 합산하지 않습니다.</p>
   {localRows.length>100&&<p>표에는 상위 100행만 표시합니다. CSV 내보내기는 검토 조건에 맞는 전체 행을 포함합니다.</p>}
   <Records rows={localRows} kind={kind} local={true}/>
   </>}
  </section>}
  <section className="original-card"><h3>{canManage?3:2}. 계약·예산 추가 검토</h3><p>저장된 예산사업은 <a href={href('/budget?mode=live')}>예산·영업후보</a>에서 지역, 기관, 부서, 사업명 및 잔액 기준으로 조회할 수 있습니다. <a href={href('/matching?mode=live')}>예산·납품 매칭</a>은 같은 원천의 검증된 저장자료에 한하여 근거를 제공합니다. {canManage&&'신규 파일 미리보기는 매칭 결과로 자동 확정하지 않습니다.'}</p><p>나라장터 공고·개찰·낙찰 전체는 이 납품요구 자료와 구별되는 데이터입니다. 현재 화면은 해당 전체 자료를 직접 수집했다고 주장하지 않습니다.</p></section>
 </main>;
}
