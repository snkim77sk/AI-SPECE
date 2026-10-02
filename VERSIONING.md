# G2B vNext 버전 관리

## 4.1 현재 기준

SINSUNG G2B vNext 4.1은 **단일 PostgreSQL + 예산 중심 + 쇼핑몰 조명·등주 영업** 운영판입니다.

1. Cafe24 기본 runtime role은 `UNIFIED`입니다.
2. 운영 저장소는 PostgreSQL 하나이며 `g2b_app`, `g2b_budget`, `g2b_meta` schema로 역할만 분리합니다.
3. 운영 SQLite 의존성은 제거했고 SQLite는 `G2B_TEST_MODE=1` 회귀테스트에서만 허용합니다.
4. 쇼핑몰은 2026-09-01 이후 전국 납품요구를 확인하되 조명·등주 대상만 정규화 저장합니다.
5. 예산 QWGJK는 최신 current state를 유지하면서 2026-01-01부터 D-1까지 과거 snapshot을 revision history로 순차 보강합니다. 과거분은 current state를 덮어쓰지 않으며 AIDFA를 포함한 예산 원천은 원문 JSON을 영구 저장하지 않습니다.
6. 물품입찰·용역공고·개찰·낙찰·계약 collection은 G2B에서 제거하고 NO1로 분리합니다.
7. bulk historical과 `APPROVED_HISTORICAL`, 교육예산 live transport는 HOLD입니다.
8. 운영 source cycle은 프로세스 singleton + PostgreSQL advisory lease로 중복 실행을 차단합니다.
9. 현재 운영정책은 `G2B_AUTO_SYNC=0` 유지이며 관리자 수동 1회 수집만 허용합니다. 자동수집 전환은 별도 승인 후 진행합니다.
10. UNIFIED `/ready`는 단일 PostgreSQL의 app/budget 저장계약과 backend가 모두 정상일 때만 200입니다.
11. 최초 4.1 전환은 `fresh_start_4_1_0=NORMALIZED_NO_RAW_V1` marker로 1회 초기화를 고정합니다.
12. 모든 배포 전 실제 PostgreSQL contract, shopping normalized collection, 전체 pytest/compile, runtime HTTP smoke를 통과해야 합니다.

## 3.0 기준
SINSUNG G2B 3.0은 기존 2.x 런타임과 호환성을 유지하지 않는 clean vNext 기준판입니다.

1. 운영 진입점은 `main.py -> vnext_clean_app.py` 하나만 사용합니다.
2. 2.x 대시보드, scheduler, collector_v200, sinsung patch chain, serving tables는 사용하지 않습니다.
3. 기본 DB는 `g2b-vnext.sqlite3`입니다.
4. API 비밀키는 배포 환경변수 또는 로그인한 관리자 설정 화면에서 등록할 수 있습니다. 관리자 저장키는 일반 `app_settings`와 분리된 vNext 전용 credential table에 저장하며 화면/API에 원문을 다시 노출하지 않습니다. 환경변수가 있으면 환경변수가 우선합니다.
5. 수집 단계에서는 LED/조명/등주 키워드로 RAW를 버리지 않습니다.
6. RAW 원본과 revision을 먼저 보존한 뒤 정규화·후분류·분석합니다.
7. 전체 원천 완전수집은 실제 원천 검증 없이 선언하지 않습니다.
8. bounded canary와 small-validation을 통과하기 전 bulk historical을 열지 않습니다.
9. `APPROVED_HISTORICAL`은 별도 승인 전 비활성 상태를 유지합니다.
10. 교육예산 vNext live transport는 별도 검증 전 HOLD입니다.
11. 모든 배포 전 전체 pytest, compile, vNext runtime HTTP smoke를 통과해야 합니다.

## 버전 변경
- 3.1.9: 운영판 보안·가시성 보강. 최초 관리자 생성에 브라우저 CSRF nonce 추가, 로그인 제한을 IP+계정 기준으로 강화, 운영버전/영구저장 상태를 화면에 표시, 비영구 DB 운영 fail-closed, 공개 진단 오류 상세 최소화, Uvicorn Server 헤더 비노출.
- 3.1.8: 물품 입찰공고 기능을 G2B vNext에서 제거하고 NO1로 역할 분리. UI/API/collector/historical/canary/monitor/budget-link 대상에서 제외하며 기존 RAW는 보존.
- 3.1.7: pre-live 전수 감사. main 기준 manual validation workflow 활성화, canary metadata 정합화, LOFIN quota 파싱 보강, regression source credential 격리 강화, credential DB 파일 권한 보정, 운영 문서 동기화.
- 3.1.4: 관리자 설정 화면에서 나라장터/지방재정365 API 키를 전용 credential table에 등록·삭제 가능하게 변경. 환경변수 우선순위와 키 원문 비노출 유지.
- 3.1.3: 최초 관리자 생성에서 setup token 의존성을 제거하고 최초 접속자가 직접 관리자 계정을 생성하도록 단순화.
- 3.1.6: 실제 collection checkpoint와 RAW 건수를 이용한 수집 상태 모니터를 추가합니다. 단계별 실행중/완료/오류/갱신중단, 페이지·저장건수·최근시각을 5초 자동 새로고침으로 표시하며 외부 API 호출이나 HOLD 해제는 하지 않습니다.
- 3.1.5: 관리자 설정에서 지방교육재정알리미 API 키를 별도 등록·삭제할 수 있으며, 교육 예산 live transport는 bounded validation 전까지 HOLD를 유지합니다.
- 3.0.x: clean vNext 런타임 내부 수정 및 운영 안정화
- 3.1.x: 검증된 신규 source/분석 기능 추가
- 4.0.0: 데이터 계약 또는 운영 아키텍처의 호환 불가 변경

## 폐기된 계열
2.x 코드는 운영 저장소에서 제거되며 Git 이력만 과거 감사용으로 남습니다.
