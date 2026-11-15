'use client';

import { useEffect, useState } from 'react';
import ServerSyncStatus from './server-sync-status';
import { usePathname, useSearchParams } from 'next/navigation';
import { ORIGINAL_NAV_ITEMS, dashboardMode, originalNavActive, originalNavHref } from '../lib/dashboard-display.mjs';

export default function OriginalShell({ children }: Readonly<{ children: React.ReactNode }>) {
  const pathname = usePathname();
  const params = useSearchParams();
  const search = params?.toString() || '';
  const [location, setLocation] = useState({ pathname: pathname || '/', search });

  useEffect(() => {
    const restore = () => setLocation({ pathname: window.location.pathname, search: window.location.search });
    restore();
    window.addEventListener('popstate', restore);
    window.addEventListener('g2b:mode-change', restore);
    return () => {
      window.removeEventListener('popstate', restore);
      window.removeEventListener('g2b:mode-change', restore);
    };
  }, [pathname, search]);

  const mode = dashboardMode(location.search);
  const activeLabel = ORIGINAL_NAV_ITEMS.find(item => originalNavActive(item.href, location.pathname, location.search))?.label || '상세 화면';
  return <div className="original-frame">
    <a className="original-skip-link" href="#original-content">본문으로 바로가기</a>
    <header className="original-top">
      <div className="original-brand-row">
        <a href={originalNavHref('/dashboard', mode)} className="original-brand">SINSUNG · 신성라이텍 G2B vNext</a>
        <span className="original-runtime">v5.2.12 · 비공개</span>
      </div>
      <div className="original-sub">미래예산 수집 → 기관·사업 정리 → 조명·등주 후보 · 사업자료 2026-01-01 이후</div>
      <ServerSyncStatus/>
    </header>
    <nav className="original-nav" aria-label="주요 메뉴">
      {ORIGINAL_NAV_ITEMS.map(item => {
        const active = originalNavActive(item.href, location.pathname, location.search);
        return <a key={item.label} href={originalNavHref(item.href, mode)} className={active ? 'on' : undefined} aria-current={active ? 'page' : undefined}>{item.label}</a>;
      })}
    </nav>
    <details className="original-mobile-menu"><summary>전체 메뉴 <span>{activeLabel}</span></summary><nav aria-label="모바일 주요 메뉴">{ORIGINAL_NAV_ITEMS.map(item => {const active=originalNavActive(item.href,location.pathname,location.search);return <a key={item.label} href={originalNavHref(item.href,mode)} aria-current={active?'page':undefined}>{item.label}</a>;})}</nav></details>
    <div id="original-content" tabIndex={-1}>{children}</div>
  </div>;
}
