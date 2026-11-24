'use client';

import { useEffect, useState } from 'react';
import ServerSyncStatus from './server-sync-status';
import { usePathname, useSearchParams } from 'next/navigation';
import { ORIGINAL_NAV_ITEMS, OWNER_NAV_ITEM, dashboardMode, originalNavActive, originalNavHref } from '../lib/dashboard-display.mjs';
import { ViewerAccessProvider, useViewerAccess } from './viewer-access';
import packageInfo from '../package.json' with {type:'json'};

export default function OriginalShell({ children, publicMembership=false }: Readonly<{ children: React.ReactNode; publicMembership?:boolean }>) {
  return <ViewerAccessProvider publicMembership={publicMembership}><OriginalFrame>{children}</OriginalFrame></ViewerAccessProvider>;
}

export function OriginalFrame({ children }: Readonly<{ children: React.ReactNode }>) {
  const {canRead,canManage,isAdministrator,loading,preview,href,publicMembership,error}=useViewerAccess();
  const pathname = usePathname();
  const params = useSearchParams();
  const search = params?.toString() || '';
  const [location, setLocation] = useState({ pathname: pathname || '/', search });
  const navItems=canRead?[
    ...ORIGINAL_NAV_ITEMS.filter(item=>canManage||!['/settings','/collection-monitor'].includes(item.href)),
    ...(canManage?[OWNER_NAV_ITEM]:[]),
    ...(canManage&&publicMembership?[{label:'회원 관리',href:'/admin/members'}]:[]),
  ]:[];

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

  // Fixture-only lab must never mount the live server-sync status poller.
  if (pathname === '/agent-lab') return <>{children}</>;
  const mode = dashboardMode(location.search);
  const activeLabel = navItems.find(item => originalNavActive(item.href, location.pathname, location.search))?.label || '상세 화면';
  const accountPage=pathname==='/login'||pathname==='/account/pending';
  return <div className="original-frame">
    <a className="original-skip-link" href="#original-content">본문으로 바로가기</a>
    <header className="original-top">
      <div className="original-brand-row">
        <a href={href(canRead?originalNavHref('/dashboard', mode):'/login')} className="original-brand">SINSUNG · 신성라이텍 G2B vNext</a>
        <span className="original-runtime">{canManage?'v'+packageInfo.version+' · 관리자':canRead?'자료 조회 전용':'회원 서비스'}</span>
        {canRead&&<a href={href('/contract-review')} className="original-review-link">계약·예산 조회</a>}
        {isAdministrator&&<a href={preview?'/dashboard':'/dashboard?viewer=member'} className="original-review-link">{preview?'관리자 화면으로 돌아가기':'일반 사용자 화면 미리보기'}</a>}
      </div>
      <div className="original-sub">예산·기관·사업과 조명·등주 자료 조회 · 사업자료 2026-01-01 이후</div>
      {canRead&&!canManage&&<p className="original-muted" style={{color:'#d5e2f0',margin:'12px 0 0',fontSize:'0.95rem'}} role="status">{preview?'일반 사용자 화면 미리보기입니다. ':''}자료 검색·조회·다운로드만 사용할 수 있습니다.</p>}
      {canManage&&<ServerSyncStatus/>}
    </header>
    {canRead&&<><nav className="original-nav" aria-label="주요 메뉴">
      {navItems.map(item => {
        const active = originalNavActive(item.href, location.pathname, location.search);
        return <a key={item.label} href={href(originalNavHref(item.href, mode))} className={active ? 'on' : undefined} aria-current={active ? 'page' : undefined}>{item.label}</a>;
      })}
    </nav>
    <details className="original-mobile-menu"><summary>전체 메뉴 <span>{activeLabel}</span></summary><nav aria-label="모바일 주요 메뉴">{navItems.map(item => {const active=originalNavActive(item.href,location.pathname,location.search);return <a key={item.label} href={href(originalNavHref(item.href,mode))} aria-current={active?'page':undefined}>{item.label}</a>;})}</nav></details></>}
    <div id="original-content" tabIndex={-1}>{canRead||accountPage?children:<section className="original-card" role="status"><h1>{loading?'회원 권한을 확인하고 있습니다.':'관리자 승인 후 자료를 조회할 수 있습니다.'}</h1>{error&&<p>{error}</p>}{!loading&&<a href="/login">로그인·회원가입</a>}</section>}</div>
  </div>;
}
