/** Original navigation order, shared by the shell and its regression tests. */
export const ORIGINAL_NAV_ITEMS = Object.freeze([
  { label: '대시보드', href: '/dashboard' },
  { label: '예산·영업후보', href: '/budget' },
  { label: 'LED 조명', href: '/shopping?category=LIGHTING' },
  { label: '등주', href: '/shopping?category=POLE' },
  { label: '스마트LED', href: '/smartled' },
  { label: '업체·단가 분석', href: '/vendors' },
  { label: '수집 상태', href: '/collection-monitor' },
  { label: '설정', href: '/settings' },
]);

export function dashboardMode(search = '') {
  return 'live';
}

export function originalNavHref(href, mode = 'live') {
  const [path, query = ''] = href.split('?');
  const params = new URLSearchParams(query);
  params.set('mode', 'live');
  return path + '?' + params.toString();
}

export function originalNavActive(href, pathname = '/', search = '') {
  const query = new URLSearchParams(search);
  const [target, targetQuery = ''] = href.split('?');
  const tab = query.get('tab');
  let path = pathname.replace(/\/$/, '') || '/';
  if (path === '/') {
    path = ({ data: '/shopping', vendors: '/vendors', agencies: '/vendors', smart: '/smartled', collect: '/settings', scope: '/settings' })[tab] || '/dashboard';
  }
  if (target === '/shopping') {
    if (query.has('category') && !['LIGHTING', 'POLE'].includes(query.get('category'))) return false;
    const category = query.get('category') === 'POLE' ? 'POLE' : 'LIGHTING';
    return path === '/shopping' && category === new URLSearchParams(targetQuery).get('category');
  }
  if (target === '/budget') return path === '/budget' || path.startsWith('/budget/');
  if (target === '/vendors' && ['/institution-patterns', '/matching'].includes(path)) return true;
  return path === target;
}

/** Counts remain decimal strings; never round a source count through Number. */
export function exactCount(value) {
  if (typeof value === 'string' && /^(0|[1-9]\d{0,127})$/.test(value)) return value;
  if (typeof value === 'number' && Number.isSafeInteger(value) && value >= 0) return String(value);
  return null;
}

export function formatDashboardCount(value, loading = false) {
  const count = exactCount(value);
  return count === null ? (loading ? '…' : '미확인') : count.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

function sumCounts(values) {
  const counts = values.map(exactCount);
  return counts.some(value => value === null) ? null : counts.reduce((sum, value) => sum + BigInt(value), 0n).toString();
}

/** Each metric names its Sites source. These are not the original server's aggregates. */
export function dashboardMetrics(data) {
  const delivery = data?.delivery;
  const budgets = Array.isArray(data?.budgets) ? data.budgets : null;
  const activeTotal = delivery && budgets ? sumCounts([delivery.active_source_rows, ...budgets.map(row => row?.active_rows)]) : null;
  const budgetCandidates = budgets ? sumCounts(budgets.filter(row => row?.service === 'QWGJK').map(row => row?.target_rows)) : null;
  return [
    { id: 'active-total', label: '현재 활성 저장행', value: activeTotal, note: '납품 활성 원천행 + 연도·원천별 예산 활성행' },
    { id: 'target-delivery', label: '현재 대상 납품품목', value: exactCount(delivery?.latest_rows), note: '조회기간 내 최신 조명·등주 품목' },
    { id: 'stored-delivery', label: '보존 납품 원천행', value: exactCount(delivery?.stored_source_rows), note: '변경차수·비활성·스마트제어 포함' },
    { id: 'inactive-delivery', label: '비활성 납품 원천행', value: exactCount(delivery?.inactive_source_rows), note: '보존 원천행 중 비활성으로 표시된 행' },
    { id: 'budget-candidates', label: '양수 잔액 예산 후보', value: budgetCandidates, note: 'QWGJK 활성 조명·등주 · AIDFA 제외' },
  ];
}

export function dashboardErrorMessage(error) {
  switch (error?.message) {
    case 'DASHBOARD_TIMEOUT': return '15초 안에 집계를 받지 못했습니다. 새로고침으로 다시 확인해 주세요. 메뉴는 바로 사용할 수 있습니다.';
    case 'DASHBOARD_SOURCE_CHANGED_RETRY': return '조회 중 저장자료가 바뀌었습니다. 새로고침해 주세요.';
    case 'DASHBOARD_SOURCE_NOT_FOUND': return '저장자료 집계 기준을 확인하지 못했습니다. 수집 상태를 확인하거나 새로고침해 주세요.';
    case 'DASHBOARD_INVALID_RESPONSE': return '집계 응답을 확인하지 못했습니다. 건수를 0으로 표시하지 않습니다. 다시 조회해 주세요.';
    default: return '저장자료 집계를 불러오지 못했습니다. 새로고침해 주세요. 메뉴는 바로 사용할 수 있습니다.';
  }
}

/** Bound the complete read, including JSON decoding; cancellation never becomes a zero result. */
export async function fetchDashboard(fetcher, mode, signal, timeoutMs = 15000) {
  const controller = new AbortController();
  let timer;
  let cancel;
  const interrupted = new Promise((_, reject) => {
    cancel = () => {
      controller.abort();
      const error = new Error('DASHBOARD_CANCELLED');
      error.name = 'AbortError';
      reject(error);
    };
    if (signal?.aborted) cancel();
    else signal?.addEventListener('abort', cancel, { once: true });
    timer = setTimeout(() => {
      controller.abort();
      reject(new Error('DASHBOARD_TIMEOUT'));
    }, timeoutMs);
  });
  const read = async () => {
    if (signal?.aborted) {
      const error = new Error('DASHBOARD_CANCELLED');
      error.name = 'AbortError';
      throw error;
    }
    const response = await fetcher('/api/dashboard?mode=' + ('live'), {
      signal: controller.signal, cache: 'no-store', credentials: 'same-origin', headers: { Accept: 'application/json' },
    });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data?.error === 'string' ? data.error : 'DASHBOARD_FETCH_FAILED');
    if (!data || typeof data !== 'object' || data.mode !== mode || !data.delivery || !Array.isArray(data.budgets)) {
      throw new Error('DASHBOARD_INVALID_RESPONSE');
    }
    return data;
  };
  try {
    return await Promise.race([interrupted, read()]);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', cancel);
  }
}

/** Only a positively empty live store with both source keys absent gets this note. */
export function dashboardAwaitingInitialCollection(data) {
  return data?.mode === 'live'
    && data?.readiness?.g2b?.hasKey === false
    && data?.readiness?.lofin?.hasKey === false
    && Array.isArray(data?.budgets)
    && data.budgets.every(row => exactCount(row?.stored_rows) === '0')
    && dashboardMetrics(data).every(metric => metric.value === '0');
}
