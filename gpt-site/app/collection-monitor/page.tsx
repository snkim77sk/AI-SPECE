'use client';
import {useState} from 'react';
import ServerCollectorStatus from '../../components/server-collector-status';
import LegacyCollectionMonitor from '../../components/legacy-collection-monitor';
export default function Monitor(){
 const [legacy,setLegacy]=useState(false);
 return <div className="budget-shell">
  <header><a href="/dashboard?mode=live">대시보드</a><a href="/budget?mode=live">지방재정 예산</a><span>비공개 · 서버 운영상태</span></header>
  <section className="original-card"><h2>자료 수집 상태</h2><p>Windows 서버의 오늘 호출 잔여량과 최근 실행 결과를 표시합니다. 이 화면을 조회해도 원천 API를 호출하지 않습니다.</p><p>자료 저장 후 GPT 사이트에 반영하는 작업은 별도입니다. 화면 위 최근 반영시각으로 조회 자료의 업데이트를 확인할 수 있습니다.</p></section>
  <ServerCollectorStatus/>
  <details className="panel" onToggle={e=>setLegacy(e.currentTarget.open)}><summary>이전 Sites 직접 수집 기록 · 현재 Windows 서버 상태와 별개</summary><p>과거 사이트 안에서 직접 실행했던 계획과 오류 기록입니다. 현재 서버의 호출 잔여량·중단 사유는 위의 서버 운영상태를 확인하세요.</p>{legacy&&<LegacyCollectionMonitor/>}</details>
 </div>;
}
