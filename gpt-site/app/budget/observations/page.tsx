'use client';
import {recentQueryDates} from '../../../lib/query-dates.mjs';
import {useCallback, useEffect, useRef, useState} from 'react';
import BudgetNavigator from '../components/navigator';
import {INCHEON_SCOPES} from '../../../lib/read-filters.mjs';

type Filters = {
  mode: string; year: string; from: string; to: string; q: string; category: string;
  region: string; org: string; dept: string; orgKey: string; deptKey: string;
  incheonScope: string; execution: string; view: string; key: string;
};
type ObservationRow = {
  source_key: string; payload_hash: string; snapshot_date: string; observation_first_seen: string; observation_last_seen: string;
  project: string; org: string; dept: string; region: string; category: string;
  effective_budget_amount: string | null; executed_amount: string | null; remaining_amount: string | null;
  budget_change: string | null; executed_change: string | null; remaining_change: string | null;
  delta_status: string; previous_snapshot_date: string | null; baseline_before_start: boolean;
};
type ObservationResult = {rows: ObservationRow[]; mode: string; year: number; from: string; to: string; next: string | null; sourceRevision: string};
const initial: Filters = {mode: 'live', year: '2026', from: '2026-01-01', to: '2026-12-31', q: '', category: '', region: '', org: '', dept: '', orgKey: '', deptKey: '', incheonScope: '', execution: '', view: 'all', key: ''};
const money = (value: string | null, delta = false) => value === null ? '미제공' : (delta && !value.startsWith('-') && value !== '0' ? '+' : '') + value.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '원';
const statusText: Record<string, string> = {
  AVAILABLE: '앞선 관측일과 비교', MISSING_AMOUNT: '일부 금액 미제공', INVALID_AMOUNT: '금액 해석 오류로 차이 계산 제외',
  NO_PREVIOUS_OBSERVATION: '앞선 관측일 없음', AMBIGUOUS_CURRENT_DATE: '같은 기준일 상태 순서 불명', AMBIGUOUS_PREVIOUS_DATE: '앞선 기준일에 여러 상태가 있어 비교 불가',
};
const errorText: Record<string, string> = {
  BUDGET_OBSERVATION_SOURCE_CHANGED_RETRY: '조회 중 관측자료가 바뀌었습니다. 첫 페이지부터 다시 검색해 주세요.',
  INVALID_OBSERVATION_CURSOR: '검색조건과 페이지 정보가 맞지 않습니다. 첫 페이지부터 다시 검색해 주세요.',
  INVALID_OBSERVATION_DATE_RANGE: '2026년 이후의 올바른 날짜 범위를 선택해 주세요.',
  BUDGET_OBSERVATION_RECORD_TOO_LARGE: '한 저장 상태가 조회 크기 제한을 넘습니다. 다른 사업이나 기간으로 좁혀 주세요.',
  BUDGET_OBSERVATION_BASELINE_TOO_LARGE: '비교 기준 자료가 크기 제한을 넘습니다. 이 사업은 차이를 조회할 수 없습니다.',
  DATABASE_SCHEMA: '관측 이력 조회에 필요한 데이터베이스 준비가 완료되지 않았습니다.',
};
function parameters(filters: Filters) {
  const p = new URLSearchParams({service: 'QWGJK', limit: '50'});
  for (const [key, value] of Object.entries(filters)) if (value) p.set(key, value);
  return p;
}
function locationFilters() {
  const p = new URLSearchParams(window.location.search), year = p.get('year') || new Date().toLocaleDateString('en-CA', {timeZone: 'Asia/Seoul'}).slice(0, 4);
  const filters = {...initial, year, ...recentQueryDates()};
  for (const key of Object.keys(initial) as (keyof Filters)[]) if (p.has(key)) filters[key] = p.get(key) || '';
  filters.mode = 'live';
  return {filters, cursor: p.get('cursor')};
}

export default function BudgetObservations() {
  const [filters, setFilters] = useState<Filters>(initial), [result, setResult] = useState<ObservationResult | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [searched, setSearched] = useState(false), [canBack, setCanBack] = useState(false);
  const generation = useRef(0), active = useRef<AbortController | null>(null);
  const load = useCallback(async (selection: Filters, cursor: string | null = null, updateUrl = false) => {
    const version = ++generation.current;
    active.current?.abort();
    const controller = new AbortController(); active.current = controller;
    setBusy(true); setError(''); setResult(null); setSearched(true); setFilters(selection);
    const p = parameters(selection); if (cursor) p.set('cursor', cursor);
    if (updateUrl) {
      const previous = window.history.state?.budgetObservationDepth;
      const depth = cursor ? (Number.isSafeInteger(previous) && previous >= 0 ? previous : 0) + 1 : 0;
      window.history.pushState({budgetObservationDepth: depth}, '', '/budget/observations?' + p);
    }
    setCanBack(Number.isSafeInteger(window.history.state?.budgetObservationDepth) && window.history.state.budgetObservationDepth > 0);
    try {
      const response = await fetch('/api/budget/observations?' + p, {signal: controller.signal, cache: 'no-store'});
      const data = await response.json() as ObservationResult & {error?: string};
      if (!response.ok) throw Error(data.error || 'OBSERVATION_READ_FAILED');
      if (version === generation.current && !controller.signal.aborted) setResult(data);
    } catch (failure) {
      if (version === generation.current && !controller.signal.aborted) {
        const code = failure instanceof Error ? failure.message : 'OBSERVATION_READ_FAILED';
        setError(errorText[code] || '관측 이력을 불러오지 못했습니다: ' + code);
      }
    } finally {
      if (version === generation.current && !controller.signal.aborted) setBusy(false);
    }
  }, []);
  useEffect(() => {
    const restore = () => { const {filters, cursor} = locationFilters(); void load(filters, cursor); };
    restore();
    window.addEventListener('popstate', restore);
    return () => {window.removeEventListener('popstate', restore); active.current?.abort();};
  }, [load]);
  function edit(patch: Partial<Filters>) {
    if(patch.mode){const url=new URL(window.location.href);url.searchParams.set('mode',patch.mode);window.history.replaceState(window.history.state,'',url);window.dispatchEvent(new Event('g2b:mode-change'));}
    generation.current++; active.current?.abort(); setBusy(false); setResult(null); setError(''); setSearched(false);
    setFilters(current => ({...current, ...patch}));
  }
  function reset() {
    const {mode, year} = filters;
    edit({...initial, mode, year, ...recentQueryDates()});
  }
  const preservedCurrent = parameters(filters); preservedCurrent.delete('from'); preservedCurrent.delete('to'); preservedCurrent.delete('key'); preservedCurrent.delete('limit');
  return <div className="budget-shell">
    <header style={{gap: 16, flexWrap: 'wrap'}}><a href={'/budget?' + preservedCurrent}>← 현재 예산사업</a><a href={'/budget/history?' + parameters(filters)}>이전 고유 상태 이력</a><a href={'/collection-monitor?mode='+filters.mode}>수집 운영상태</a><span>비공개 · 저장자료 조회</span></header>
    <section className="intro"><div><div className="eyebrow">BUDGET · RECORDED OBSERVATIONS</div><h1>날짜별 예산 관측 이력</h1><p>사업키 없이 기간·기관·검색어로 QWGJK 사업의 날짜별 관측 기록을 찾습니다. 외부 API 호출이나 키 설정 없이 읽기만 합니다.</p></div></section>
    <div className="notice"><div><strong>{'수집해 둔 실제 자료만 조회합니다.'}</strong><p>날짜가 다르면 같은 상태도 따로 보존해 A→B→A와 날짜만 바뀐 관측을 확인할 수 있습니다. 같은 날의 같은 상태는 하나로 합쳐지므로 모든 당일 변화 순서나 완전한 일별 존재 여부를 뜻하지 않습니다. 이 기능 도입 전 고유 상태 기록을 날짜별 관측으로 추정해 채우지 않습니다.</p></div></div>
    <section className="panel" aria-label="관측 이력 검색조건">
      <div className="budget-controls">
        <label>자료<span className="chip mint">실제 저장자료</span></label>
        <label>회계연도<input aria-label="이력 회계연도" type="number" min="2026" max="2100" value={filters.year} onChange={e => edit({year: e.target.value, from: e.target.value + '-01-01', to: e.target.value + '-12-31', orgKey: '', deptKey: '', key: ''})}/></label>
        <label>시작 기준일<input aria-label="이력 시작 기준일" type="date" min="2026-01-01" max="2100-12-31" value={filters.from} onChange={e => edit({from: e.target.value})}/></label>
        <label>종료 기준일<input aria-label="이력 종료 기준일" type="date" min="2026-01-01" max="2100-12-31" value={filters.to} onChange={e => edit({to: e.target.value})}/></label>
        <label>분류<select aria-label="이력 분류" value={filters.category} onChange={e => edit({category: e.target.value})}><option value="">전체 분류</option><option value="LIGHTING">조명</option><option value="POLE">등주</option><option value="SOLAR">태양광</option><option value="ELECTRICAL">전기</option><option value="OTHER">기타</option></select></label>
      </div>
      <BudgetNavigator mode={filters.mode} service="QWGJK" year={filters.year} region={filters.region} orgKey={filters.orgKey} deptKey={filters.deptKey} onChange={(region, orgKey, deptKey) => edit({region, orgKey, deptKey, org: '', dept: '', incheonScope: ''})}/>
      <form onSubmit={event => {event.preventDefault(); if (!busy) void load(filters, null, true);}}>
        <div className="budget-controls">
          <label>통합 검색<input aria-label="이력 통합 검색" value={filters.q} maxLength={100} placeholder="사업·기관·부서·분야·부문·회계" onChange={e => edit({q: e.target.value})}/></label>
          <label>기관명 직접 입력 (완전 일치)<input aria-label="이력 기관명 직접 입력" value={filters.org} maxLength={1024} placeholder="현재 선택지에 없는 과거 기관" onChange={e => edit({org: e.target.value, orgKey: '', deptKey: ''})}/></label>
          <label>부서명 직접 입력 (완전 일치)<input aria-label="이력 부서명 직접 입력" value={filters.dept} maxLength={1024} onChange={e => edit({dept: e.target.value, deptKey: ''})}/></label>
          <label>인천 기관 범위<select aria-label="이력 인천 기관 범위" value={filters.incheonScope} onChange={e => edit({incheonScope: e.target.value, region: e.target.value ? '인천광역시' : filters.region, orgKey: '', deptKey: '', org: '', dept: ''})}><option value="">별도 범위 없음</option>{INCHEON_SCOPES.map(scope => <option key={String(scope[0])} value={String(scope[0])}>{String(scope[1])}</option>)}</select></label>
          <label>집행 상태<select aria-label="이력 집행 상태" value={filters.execution} onChange={e => edit({execution: e.target.value})}><option value="">전체</option><option value="UNEXECUTED">미집행</option><option value="PARTIAL">일부 집행</option><option value="FULL">전액 이상 집행</option></select></label>
          <label>후보 범위<select aria-label="이력 후보 범위" value={filters.view} onChange={e => edit({view: e.target.value})}><option value="all">전체 사업</option><option value="targets">조명·등주 양수 잔액</option></select></label>
          <button className="primary" disabled={busy}>{busy ? '조회 중…' : '관측 이력 검색'}</button><button type="button" onClick={reset}>조건 초기화</button>
        </div>
      </form>
      {filters.key && <p className="analysis-note">특정 사업으로 좁혀져 있습니다. <button onClick={() => edit({key: ''})}>사업 제한 해제</button></p>}
      <p className="analysis-note">조건은 각 관측 상태에 적용됩니다. 선택지는 현재 저장자료 기준이므로 과거 기관·부서는 직접 이름으로도 찾을 수 있습니다. 결과는 기준일 내림차순이며 같은 날짜 안의 표시 순서는 실제 변화 순서를 뜻하지 않습니다.</p>
    </section>
    {error && <div role="alert" className="feedback">{error} <button disabled={busy} onClick={() => void load(filters, null, true)}>첫 페이지 다시 검색</button></div>}
    {busy && <p role="status" className="feedback">관측 이력과 앞선 관측 상태를 확인하고 있습니다.</p>}
    {result && <section className="panel" aria-label="관측 이력 결과">
      <div className="budget-controls"><h2>이번 페이지 {result.rows.length}개 관측 상태</h2><span>{result.from} ~ {result.to} · {result.year} 회계연도</span></div>
      <p className="analysis-note">증감은 같은 사업의 더 이른 날짜의 직전 관측 상태와 비교합니다. 검색 시작일 이전의 관측도 비교에만 사용하며, 같은 상태를 다음 관측일에 다시 확인했으면 유효한 금액의 증감은 0입니다. 같은 기준일의 상태 순서가 불명확하거나 금액이 없거나 잘못된 경우 임의로 0을 넣지 않습니다. 관측 사이에 수집하지 못한 날이 있을 수 있어 매일의 증감을 보장하지 않습니다.</p>
      {!result.rows.length ? <p className="analysis-note">조건에 맞는 날짜별 관측이 없습니다. 자료가 없다는 것은 해당 날짜에 사업이 없었다는 뜻이 아닙니다.</p> : <div className="data-table-wrap"><table><thead><tr><th>관측 기준일 / 최초·최근 저장</th><th>사업 / 기관 / 부서</th><th>예산 기준액 / 증감</th><th>집행액 / 증감</th><th>잔액 / 증감</th><th>비교 기준</th></tr></thead><tbody>{result.rows.map(row => <tr key={row.source_key + ':' + row.snapshot_date + ':' + row.payload_hash}>
        <td>{row.snapshot_date}<br/><small>{row.observation_first_seen}<br/>{row.observation_last_seen}</small></td><td><strong>{row.project || '사업명 미제공'}</strong><br/>{row.region} · {row.org || '기관명 미제공'}<br/><small>{row.dept || '부서명 미제공'} · {row.category}</small></td>
        <td>{money(row.effective_budget_amount)}<br/><small>증감 {money(row.budget_change, true)}</small></td><td>{money(row.executed_amount)}<br/><small>증감 {money(row.executed_change, true)}</small></td><td>{money(row.remaining_amount)}<br/><small>증감 {money(row.remaining_change, true)}</small></td>
        <td>{statusText[row.delta_status] || row.delta_status}<br/><small>{row.previous_snapshot_date || '앞선 기준일 없음'}{row.baseline_before_start ? ' · 검색 시작일 이전' : ''}</small></td>
      </tr>)}</tbody></table></div>}
      <div className="budget-controls"><button disabled={busy} onClick={() => void load(filters, null, true)}>첫 페이지</button><button disabled={busy || !canBack} onClick={() => window.history.back()}>이전 페이지</button><button disabled={busy || !result.next} onClick={() => {if (result.next) void load(filters, result.next, true);}}>다음 페이지</button><span>{result.next ? '더 오래된 관측이 있습니다.' : '선택 범위의 관측을 모두 조회했습니다.'}</span></div>
    </section>}
    {!busy && !result && !error && !searched && <p className="analysis-note">조건을 바꿨습니다. 검색을 눌러 새 조건의 관측 이력을 확인해 주세요.</p>}
  </div>;
}
