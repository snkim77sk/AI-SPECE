'use client';

import { useEffect, useState } from 'react';
import {
  dashboardMode, dashboardMetrics, dashboardAwaitingInitialCollection, dashboardErrorMessage, fetchDashboard,
  formatDashboardCount, originalNavHref,
} from '../../lib/dashboard-display.mjs';

type DashboardData = Record<string, any>;

export default function Dashboard() {
  const [mode, setMode] = useState('live');
  const [ready, setReady] = useState(false);
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const restore = () => {
      setData(null);
      setError('');
      setLoading(true);
      setMode(dashboardMode(window.location.search));
      setRefresh(value => value + 1);
      setReady(true);
    };
    restore();
    window.addEventListener('popstate', restore);
    return () => window.removeEventListener('popstate', restore);
  }, []);

  useEffect(() => {
    if (!ready) return;
    const abort = new AbortController();
    setData(null);
    setError('');
    setLoading(true);
    fetchDashboard(fetch, mode, abort.signal)
      .then(result => { if (!abort.signal.aborted) setData(result as DashboardData); })
      .catch(reason => { if (!abort.signal.aborted) setError(dashboardErrorMessage(reason)); })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [mode, refresh, ready]);

  function changeMode(value: string) {
    const nextMode = 'live';
    if (nextMode === mode) return;
    const url = new URL(window.location.href);
    url.searchParams.set('mode', nextMode);
    window.history.pushState(window.history.state, '', url);
    window.dispatchEvent(new Event('g2b:mode-change'));
    setData(null);
    setError('');
    setLoading(true);
    setMode(nextMode);
  }

  const count = (value: unknown) => formatDashboardCount(value, loading);
  const href = (path: string) => originalNavHref(path, mode);
  const budgets = Array.isArray(data?.budgets) ? data.budgets : [];
  const revisions = Array.isArray(data?.revisions) ? data.revisions : [];
  const configured = data?.readiness?.g2b?.liveReady === true && data?.readiness?.lofin?.liveReady === true;
  const summary = loading
    ? '저장자료 집계를 불러오는 중입니다. 메뉴는 바로 사용할 수 있습니다.'
    : error || (data ? '저장자료 집계 완료 · 실제 원천의 수집 완전성은 미확인입니다.' : '저장자료 집계를 확인하지 못했습니다.');

  return <main className="original-wrap original-dashboard">
    <section className="original-card">
      <h2>G2B vNext 대시보드</h2>
      <div className="original-notice"><b>운영 원칙:</b> Windows 서버가 수집·보관한 예산과 조명·등주 자료를 GPT 사이트에서 조회합니다. 카페24 G2B는 사용하지 않습니다.</div>
      <p className={'original-summary-state original-muted' + (error ? ' original-error' : '')} role="status" aria-live="polite">{summary}</p>
      {!loading && !error && dashboardAwaitingInitialCollection(data) && <p className="original-muted">기존 자료를 가져오지 않았고 실제 수집 전입니다.</p>}
      {null}
    </section>

    <div className="original-grid" aria-label="저장자료 주요 현황" aria-busy={loading}>
      <div className="original-kpi"><b>5.2.18</b><span>소스 후보 버전</span><small>실제 운영 배포 버전 검증 별도</small></div>
      <div className="original-kpi"><b>미확인</b><span>배포 HEAD</span><small>실행 배포의 커밋 정보 미제공</small></div>
      <div className="original-kpi"><b>{loading ? '…' : data ? '연결됨' : '미확인'}</b><span>저장소 조회</span><small>{data ? '집계 응답 확인 · 영속성 검증 별도' : '영구 저장소 검증 미확인'}</small></div>
      {dashboardMetrics(data).map(metric => <div className="original-kpi" key={metric.id}>
        <b>{count(metric.value)}</b><span>{metric.label}</span><small>{metric.note}</small>
      </div>)}
    </div>

    <section className="original-card">
      <h3>기존 Sites 직접수집 준비상태</h3>
      <p><span className={'original-pill' + (data && !configured ? ' warning' : '')}>{loading ? '확인 중' : !data ? '미확인' : configured ? '호출 설정됨' : '설정 확인 필요'}</span> <span className="original-muted">· 실제 원천 검증·제공기관 잔량 확인은 별도입니다.</span></p>
      <p className="original-muted">집계는 저장자료만 조회합니다. 이 화면에서 원천 수집을 실행하지 않으며, 현재 자료와 보존 이력을 구분해서 표시합니다.</p>
      <p><a className="original-button" href="/contract-review">신규 계약·예산 즉시 검토</a>　<a className="original-button" href={href('/collection-monitor')}>각 자료 수집 상태 확인</a></p>
      <div className="original-controls">
        <label>자료 모드<span className="chip mint">실제 저장자료</span></label>
        <button type="button" onClick={() => setRefresh(value => value + 1)} disabled={loading}>{loading ? '집계 확인 중…' : '전체 현황 새로고침'}</button>
        <span className="original-muted">{data?.asOf ? '조회 시각 ' + data.asOf : loading ? '최대 15초 동안 확인합니다.' : '조회 시각 미확인'}</span>
      </div>
    </section>

    <details className="original-card original-detail">
      <summary>집계 기준·연도별 예산·연결 상세</summary>
      <section className="original-detail-section">
        <h3>저장자료 집계 기준</h3>
        <p>현재 활성 저장행은 납품 활성 원천행과 연도·원천별 예산 활성행의 합입니다. 이전 변경차수가 포함될 수 있으며, 원본 서비스의 ‘현재 유효 저장자료’와 동일한 집계가 아닙니다.</p>
        <p>현재 대상 납품품목은 조회기간 내 최신 조명·등주 품목 수입니다. 납품요구 식별값 수와 다르며 스마트제어는 별도입니다. 보존 납품 원천행에는 비활성 행과 변경 이력, 스마트제어가 포함됩니다.</p>
        <p>예산 후보는 QWGJK의 활성 조명·등주 중 유효한 양수 잔액이 있는 행입니다. 원본의 ‘대상 예산사업’과 범위가 다르며 발주 예정·확정 사업 수가 아닙니다. AIDFA 편성액을 미집행 잔액으로 합산하지 않습니다.</p>
        <p className="original-muted">조회기간 {data?.window?.from || '미확인'} ~ {data?.window?.to || '미확인'} · 최신 납품요구 식별값 {count(data?.delivery?.requests)}개 · 스마트제어 최신 품목 {count(data?.delivery?.smart_control_rows)}건 · 기관 식별값 {count(data?.delivery?.agency_identities)}개. 기관 식별값은 전국 공식기관 수가 아닙니다.</p>
      </section>
      <section className="original-detail-section">
        <h3>연도·원천별 예산</h3>
        <div className="data-table-wrap"><table><thead><tr><th>원천 / 연도</th><th>저장행</th><th>현재 활성</th><th>보존된 비활성</th><th>양수 잔액 후보</th><th>최종 저장</th></tr></thead><tbody>
          {budgets.map((row: any) => <tr key={row.service + ':' + row.fiscal_year}><td>{row.service} · {row.fiscal_year}</td><td>{count(row.stored_rows)}</td><td>{count(row.active_rows)}</td><td>{count(row.inactive_rows)}</td><td>{row.service === 'AIDFA' ? '해당 없음' : count(row.target_rows)}</td><td>{row.updated_at || '미확인'}</td></tr>)}
          {!budgets.length && <tr><td colSpan={6}>{loading ? '집계를 확인하는 중입니다.' : data ? '저장된 예산 행이 없습니다.' : '집계 미확인'}</td></tr>}
        </tbody></table></div>
      </section>
      <section className="original-detail-section">
        <h3>이력 보존</h3>
        {revisions.map((row: any) => <p key={row.service}>{row.service} 고유 상태 {count(row.unique_states)}건 · 최초 저장 기준일 {row.first_stored_snapshot || '미확인'} ~ {row.last_stored_snapshot || '미확인'}</p>)}
        {!revisions.length && <p>{loading ? '이력을 확인하는 중입니다.' : data ? '저장된 고유 상태 이력이 없습니다.' : '이력 집계 미확인'}</p>}
        <p>날짜별 QWGJK 관측 {count(data?.observations?.observed_states)}건 · 관측 날짜 {count(data?.observations?.observed_dates)}일 · {data?.observations?.first_date || '날짜 없음 / 미확인'} ~ {data?.observations?.last_date || '날짜 없음 / 미확인'}</p>
        <p className="original-muted">관측일 수는 완전히 수집한 날짜 수가 아닙니다. 기존 고유 상태를 날짜별 관측으로 소급 생성하지 않습니다.</p>
        <div className="original-inline-links"><a href={href('/budget/history')}>기존 고유 상태 이력</a><a href={href('/budget/observations')}>날짜별 관측 기록</a></div>
      </section>
      <section className="original-detail-section">
        <h3>연결·저장·메모리 상태</h3>
        {['g2b', 'lofin'].map(source => {
          const status = data?.readiness?.[source];
          return <p key={source}>{source === 'g2b' ? 'G2B' : '지방재정'} 키 {status ? status.hasKey ? '설정됨' : '미설정' : '미확인'} · 실제 호출 준비 {status ? status.liveReady ? '설정됨, 원천 검증 별도' : '미완료' : '미확인'} · 앱 일일 상한 {status ? status.localDailyCap ? count(status.localDailyCap) + '회' : '미배정' : '미확인'} · 공유 범위 검토 {status ? status.quotaScopeReviewed ? '설정됨, 실제 잔량 별도' : '미완료' : '미확인'}</p>;
        })}
        <p className="original-muted">전국 원천의 완전성, 다른 프로그램의 호출량과 실제 제공기관 잔량은 미확인입니다. 물리 저장용량과 최고 메모리는 계측하지 못했습니다. 플랫폼 실행 한도는 128MiB이며, 조회기간과 자료 삭제 정책은 서로 다릅니다.</p>
        <p className="original-muted">집계 API의 일정 설치 상태: {data?.readiness?.scheduleInstalled === false ? '미설치' : data?.readiness?.scheduleInstalled === true ? '설정됨, 실행 검증 별도' : '미확인'} · 물리 삭제 정책: {data?.storage?.physicalRetentionEnabled === false ? '자동 삭제 꺼짐' : '미확인'}. 최신 수집 실행 상태는 수집 상태 메뉴에서 확인하세요.</p>
        <div className="original-inline-links"><a href={href('/storage-inspection')}>삭제 없는 저장량 진단</a><a href={href('/settings')}>설정 확인</a></div>
      </section>
    </details>
  </main>;
}
