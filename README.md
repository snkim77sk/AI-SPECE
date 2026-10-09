# SINSUNG G2B vNext 4.1.223

## 4.1.223 QWGJK 담당부서 원천제약 표시

지방재정365 QWGJK 공식 출력 스키마는 `dept_cd`(부서코드)는 제공하지만 부서명 필드는 제공하지 않습니다. 따라서 부서명이 비어 있는 QWGJK 행을 단순한 "미수집"으로 표시하지 않고 `QWGJK 원천 부서명 미제공 · 부서코드 ...`로 명확히 표시합니다. 기존 same-record revision 및 동일 기관+부서코드의 단일 확정명 source-free 복구는 유지하되, 저장된 실제 이름 근거가 없으면 임의로 추정하지 않습니다. 화면 상세·변경이력·Excel 표시를 같은 규칙으로 통일했습니다. 외부 API 추가호출, PostgreSQL 자료/체크포인트/schema, LOFIN 500회 한도, 256MB 격리 작업자 정책은 변경하지 않습니다.

## 4.1.222 업체·수주 분석 납품금액 순위 추가

업체·수주 분석 화면의 가장 왼쪽에 순위 열을 추가했습니다. 지역/업체 검색 결과 내 납품금액 내림차순으로 1위부터 표시하며, 운영 PostgreSQL 조회와 호환 결과서버 스냅샷에 동일하게 적용합니다. 기존 업체/납품 집계, 검색필터, 저장자료, 수집/API/256MB 격리 정책은 변경하지 않았습니다. 빈 결과 표 colspan 및 렌더링 회귀테스트를 추가했습니다.


## 4.1.221 비정상 수집 작업자 종료 후 안전한 자동 재개

격리 수집 작업자 실행상태가 FAILED가 되면 일반 2시간 주기를 기다리던 경로를 개선했습니다. 실패 후 자동 재시도는 2분·4분·8분·16분·최대 30분으로 늘어나는 bounded backoff를 적용하고, 정상 완료 시 연속 실패 횟수를 초기화합니다. 예기치 못한 스케줄러 예외도 같은 제한을 적용합니다. 호출한도 소진 시에는 기존 KST 자정 대기, lease 충돌 및 메모리 대기 정책은 유지합니다. PostgreSQL 예산 페이지에서 작업자가 갑자기 종료돼도 원본 checkpoint/generation을 보존하며 마지막 정상 저장 페이지 이후부터 재개하는 회귀테스트를 추가했습니다. DB 삭제·초기화, 수집기·API 한도, 256MB isolated worker는 그대로 유지합니다.


## 4.1.220 격리 수집 작업자 오류 이력 재사용 방지

나라장터/지방재정365 격리 작업자 실패시 저장한 오류가 다음 실행 후에도 남아, 새 작업자가 오류를 기록하지 못하면 지난 `WORKER:source_run:TimeoutError`를 현재 실패로 잘못 표시할 수 있던 문제를 방지합니다. 부모가 각 작업 실행마다 새 식별자를 발급하고, 자식이 기존 `shopping_recent_last_error`/`budget_recent_last_error`에 해당 실행 식별자와 함께 기록합니다. 부모는 본인 자식 식별자와 정확히 일치하는 오류만 받아들입니다. 오류 기록이 없으면 실제 프로세스 종료코드를 표시하고, 후속 성공시 화면 오류 상태를 지웁니다. API, PostgreSQL 사업자료 및 수집 체크포인트, 256MB 격리/자동 수집 정책은 그대로 유지합니다.


## 4.1.219 수집 단계·최근 실행 내역 상태 일치

예산 지역분할과 일반 체크포인트가 5분 이상 갱신되지 않아 수집 단계는 "갱신중단"인데 최근 실행 내역은 원본 RUNNING 값을 읽어 "실행중"으로 표시되던 오류를 수정했습니다. 두 화면은 동일한 `_state_for`의 5분 기준으로 표시하고, 원본 체크포인트 상태는 `checkpoint_status` 필드로 유지합니다. 나라장터·지방재정365 상태, 진행 건수, 체크포인트 불변 테스트를 추가했습니다. DB 삭제/초기화·수집기·API 호출한도·256MB 격리 작업자는 변경하지 않았습니다.


## 4.1.218 배포 전 전체 코드 감사 · 지역분할 갱신중단 표시

운영 소스 81개와 주요 보안·메모리·PostgreSQL·수집·배포 경로를 감사했습니다. 지역별 예산 분할수집 체크포인트가 5분 이상 갱신되지 않아도 실행중으로 보일 수 있는 현상을 수정했습니다. 재개용 체크포인트와 원본 데이터는 수정하지 않으며, 모니터 화면의 표시 판정에만 5분 기준을 적용합니다. 기존 수집·DB·API 호출한도·256MB worker 정책은 불변입니다.


## 4.1.217 지방재정365 PostgreSQL 연결 풀 타임아웃 보완

격리된 예산 작업자가 advisory lease 연결을 보유하면서 예산/앱 분류를 서로 다른 연결로 중첩 저장해 공유 1+1 PostgreSQL 풀을 초과할 수 있던 경로를 수정했습니다. 두 분류 결과는 동일한 연결과 트랜잭션에서 기록하며 실 PostgreSQL CI에서 잠금 보유 중 분류를 검증합니다. 기존 DB/checkpoint, 나라장터·지방재정365 수집기, API 500회 호출한도, 256MB worker 정책은 변경하지 않았습니다.


## 4.1.216 모바일 지역·기관 동기화

예산 화면의 지역/기관 연동을 production CSP에 맞게 수정했습니다. 기존 inline onchange는 CSP에 의해 차단될 수 있어 모바일에서 지역 select만 경기도로 보이면서 서버 화면·기관목록은 인천 상태로 남는 회귀가 있었습니다. inline handler를 제거하고 same-origin /budget-filter.js에서 지역·기관 변경을 처리하며, pageshow 때 서버가 렌더한 region/institution/department 값을 form에 다시 동기화해 모바일 뒤로가기·탭복원·form state restoration에서도 교차지역 기관이 섞이지 않게 했습니다. 서울도봉구/경기수원시처럼 공백·광역명 표기가 축약된 QWGJK 기관명은 서울특별시 도봉구/경기도 수원시와 동일 기관으로 검색·표시합니다. 수집/API/checkpoint/schema에는 변화가 없습니다.

## 4.1.215 첫 진입 대시보드 즉시 표시

로그인 직후 /dashboard가 raw_counts, target_dataset_counts, readiness 집계를 한 HTTP 요청에서 모두 기다리던 구조를 분리했습니다. 대시보드 HTML은 버전·HEAD·저장소 상태와 빈 KPI shell만 즉시 렌더하고, same-origin 외부 JS가 /api/dashboard-summary를 비동기로 호출해 저장자료 숫자와 준비상태를 나중에 채웁니다. 집계가 지연되거나 잠시 실패해도 메뉴와 화면은 먼저 사용할 수 있습니다. production startup의 기존 lazy runtime loader/background backend init은 유지하고, 외부 API·수집·checkpoint·DB schema에는 변화가 없습니다.

## 4.1.214 지역별 기관선택 복구

예산 화면에서 지역을 바꿔도 이전 지역의 기관값이 URL/폼에 남아 `경기도 + 강화군`처럼 교차지역 조합이 유지될 수 있던 회귀를 서버에서 차단합니다. 선택 기관이 현재 지역의 허용목록에 없으면 institution/department 필터를 즉시 초기화하며, 화면 JavaScript가 비활성화되어도 동일하게 동작합니다. 서울특별시는 서울 본청+25개 자치구, 경기도는 경기도 본청+31개 시·군을 기본 기관목록으로 항상 제공하고 저장 PostgreSQL에서 확인된 추가 기관을 병합합니다. 따라서 해당 지역 수집량이 아직 적어도 기관 선택창이 비지 않습니다. 짧은 기관명(강남구/수원시)과 전체 기관명(서울특별시 강남구/경기도 수원시)은 동일 기관으로 처리합니다. 다른 지역은 기존 저장기관 동적 목록을 유지합니다. 수집/API/checkpoint/schema는 변경하지 않습니다.

## 4.1.213 QWGJK 부서코드 기반 담당부서 해석

QWGJK 현재자료에서 담당부서명이 비어 있어도 부서코드가 존재하는 경우가 있어, 동일 기관(org_code 우선, 없으면 org_name) + 동일 dept_code에서 이미 실제 부서명이 저장된 다른 current/revision evidence가 있고 이름이 정확히 하나로 일치할 때만 담당부서명을 source-free로 보강합니다. 같은 코드에 둘 이상의 부서명이 존재하면 ambiguous로 남겨 자동 적용하지 않습니다. 이름 evidence가 전혀 없을 때는 사업 상세에 `부서명 미제공 · 부서코드 ...`를 표시해 원천에서 확보된 코드를 숨기지 않으며, Excel에도 담당부서코드를 별도 열로 포함합니다. 기존 same-record revision 복구를 먼저 수행한 뒤 code evidence 복구를 실행합니다. 외부 API 추가호출, DB 초기화, schema 변경은 없습니다.

## 4.1.212 담당부서 source-free 복구·중복표시 제거

예산 목록의 지역/기관 아래에 담당부서를 중복 표시하지 않고 사업명을 펼쳤을 때 상세에서 한 번만 표시하도록 정리했습니다. 담당부서가 빈 현재 QWGJK 사업은 외부 API를 추가 호출하지 않고 동일 dataset+record_key의 기존 normalized revision에서 가장 최근의 실제 비어있지 않은 부서명을 bounded 방식으로 복구합니다. 다른 사업명·사업코드·기관의 부서명을 추정하거나 복사하지 않으며, revision에도 실제 부서값이 없으면 미수집을 유지합니다. isolated budget worker가 자동수집 사이클마다 최대 250건×4 batch를 source-free로 점검하므로 메모리와 API quota에 부담을 주지 않습니다. 향후 정상 QWGJK 재수집에서 부서값이 들어오면 기존 normalized current row는 자연스럽게 갱신됩니다. source API·checkpoint·PostgreSQL schema·256MB worker 구조는 변경하지 않습니다.

## 4.1.211 예산사업 단순검색·기관/담당부서 복구

4.1.210 배포 후 실제 사용 피드백을 반영해 예산 화면을 다시 단순화했습니다. 상단은 연도 → 지역 → 기관 → 담당부서 → 빠른검색 → 직접검색 → 집행상태 → 조회 → Excel 순서만 유지합니다. 기관과 담당부서는 분리된 select이며 각각 전체 옵션을 제공합니다. 인천은 기존 curated scope를 복구해 인천 본청·종합건설본부·경제자유구역청·상수도사업본부·도시철도건설본부·군·구를 즉시 선택할 수 있고, 서울·경기 등 다른 지역은 저장된 현재 QWGJK 예산자료에서 기관을 동적으로 구성한 뒤 선택 기관의 실제 담당부서를 두 번째 select에 표시합니다. 사업명은 별도 페이지 이동 대신 행 안에서 펼쳐 담당부서·사업코드·분야·부문·회계·기준일을 확인합니다. 담당부서가 저장자료에 없으면 빈칸 대신 미수집으로 표시하고, 향후 수집자료는 QWGJK 부서 필드 별칭을 넓게 수용합니다. 빠른검색은 조명군과 신축·건립·도로개설 등 연관사업군을 한 select에 두고 직접검색과 함께 사용할 수 있습니다. Excel은 화면과 같은 기관·담당부서·빠른검색·직접검색 조건을 적용합니다. source API·checkpoint·PostgreSQL schema·256MB isolated worker는 변경하지 않습니다.

## 4.1.210 전국 예산사업 빠른검색·상세·Excel

예산 화면을 저장자료 중심 영업검색 화면으로 단순화했습니다. 인천 전용 기관 preset 대신 선택 지역의 현재 PostgreSQL 자료에서 기관·담당부서명을 동적으로 불러와 전국 모든 지역·기관·부서를 동일 방식으로 검색·선택할 수 있습니다. 조명/LED/가로등/보안등/실내조명/투광등/등주/경관조명과 신축/건립/증축/리모델링/도로개설/도로정비/공원/주차장/터널/교량/도시재생 등의 빠른검색을 추가했으며, 키워드 검색은 기존 분류를 강제로 바꾸지 않아 OTHER 사업도 찾을 수 있습니다. 사업명 클릭 시 담당부서·사업코드·분야·부문·회계·예산·집행·잔액·집행률을 read-only 상세화면에서 확인합니다. 현재 검색조건은 최대 10,000건까지 dependency-free XLSX로 내려받을 수 있습니다. 기존 영업후보·미래예산/과거 예산↔조달/기관별 구매패턴 버튼은 기본 UI에서 제거했습니다. 외부 API·수집 checkpoint·PostgreSQL schema·256MB isolated worker 정책은 변경하지 않습니다.

## 4.1.209 지방재정 quota 경계 정상대기

지방재정365 QWGJK/AIDFA 수집이 당일 500회 한도 또는 source-context 요청 slice의 마지막 요청까지 정상 저장한 뒤 다음 페이지에서 경계에 도달하는 경우를 일반 worker 오류로 표시하지 않도록 보완했습니다. `LOCAL_DAILY_QUOTA_REACHED`와 `VNEXT_SOURCE_REQUEST_CONTEXT_BUDGET_EXHAUSTED`는 checkpoint를 FAILED가 아닌 INCOMPLETE로 보존하고, 실제 LOFIN 잔여호출이 0이면 runtime을 WAITING_QUOTA로, 일일 quota가 남아 있으면 PARTIAL로 종료합니다. 저장된 page/checkpoint는 그대로 resume하며 다른 RuntimeError는 숨기지 않습니다. heavy worker의 실제 오류는 안전한 120자 코드까지 남겨 다음 운영 진단이 가능하도록 했습니다.

## 4.1.208 분류대기 source-I/O 완전분리

256MB isolated budget worker가 기존 PostgreSQL exact-current 분류대기를 처리하는 사이클에서는 LOFIN 원천수집 코드를 아예 실행하지 않도록 순서를 고정했습니다. budget / budget_appropriation / education_budget를 500건×최대32 batch로 bounded 분류하고, 한 건이라도 처리했거나 batch limit에 도달하면 해당 child는 PARTIAL로 종료해 메모리를 해제합니다. 다음 자동 사이클이 다시 분류를 이어가며, 저장 분류대기가 비워진 뒤에만 지방재정365 수집을 재개합니다. 따라서 분류대기 감소 구간의 LOFIN source API 호출 증가는 코드상 0이며 기존 PostgreSQL·checkpoint·quota 분리·메모리 격리는 유지합니다.

## 4.1.207 분류대기 자동 보강

예산 전체조건 요약에서 표시되는 exact-current 분류대기 건이 원천수집 종료 뒤 그대로 남지 않도록 low-memory isolated budget worker의 분류 보강을 source-free 유지보수로 분리했습니다. 이제 LOFIN 키가 잠시 없거나 일일 quota가 소진됐거나 새 예산 페이지가 없는 사이클에도 PostgreSQL이 준비되어 있으면 저장된 budget/budget_appropriation/education_budget의 누락·변경 분류를 최대 500건×32 batch씩 bounded 처리합니다. 외부 API를 추가 호출하지 않으며 기존 예산자료·checkpoint·수집상태 의미와 256MB 메모리 격리는 유지합니다.


## 4.1.206 예산 후보 분류완료 범위 표시

예산 전체조건 요약의 총 사업수·예산·집행·잔액은 그대로 전체 필터조건 기준으로 집계하되, 조명·등주 잔액 후보 수는 exact-current 분류가 완료된 행만 근거로 계산된다는 점을 화면에 명확히 표시합니다. 전체 조건 중 분류완료/분류대기 건수를 함께 집계하고, 분류대기가 있으면 후보 수와 후보 잔액에 '분류완료 건 기준' 안내를 표시합니다. 전체 행을 웹 메모리에 올리지 않으며 source I/O도 발생하지 않습니다.


## 4.1.205 자동수집 상태 표시·운영정책 정합화

production UNIFIED의 자동수집은 4.1.192 이후 backend readiness가 끝나면 별도 클릭이나 positive enable flag 없이 자동으로 시작합니다. 이번 버전은 collection monitor에 자동수집 ON/OFF와 자동 worker 상태를 명확히 표시하고, 수동 수집 버튼은 자동수집을 켜는 버튼이 아니라 즉시 실행·점검용임을 표시합니다. 오래된 .env/runbook의 manual-only 설명도 실제 runtime 정책과 맞췄습니다. 수집 순서·API 한도·checkpoint·DB 자료·메모리 격리 방식은 변경하지 않습니다.


## 4.1.204 예산 전체 페이지 위치 표시

4.1.203의 전체조건 PostgreSQL 집계를 목록 페이지에도 연결했습니다. 예산 목록은 이제 `세부사업 페이지 2 / 34 · 전체 6,719건`처럼 현재 위치와 전체 페이지/건수를 함께 표시하고, 주소창에 실제 범위를 넘는 page 번호가 들어오면 전체조건 COUNT 기준 마지막 유효 페이지로 자동 보정합니다. 페이지당 200건 bounded 조회와 256MB 메모리 안전정책, source I/O 0 원칙은 그대로 유지합니다.


## 4.1.203 예산 전체조건 요약 정확도 개선

예산 화면의 한눈에 보기 숫자를 현재 200건 페이지 합계가 아니라 PostgreSQL 전체 필터 조건 집계로 변경했습니다. 현재 연도·지역·기관·분류·검색어·집행상태·영업우선 조건을 SQL에 그대로 적용한 뒤 COUNT/SUM만 반환하여 총 사업수·총 예산·총 집행·총 잔액·미집행·부분집행·조명/등주 잔액후보 수와 후보 잔액을 정확히 표시합니다. 전체 행을 웹 메모리에 올리지 않으므로 256MB 안전정책과 source I/O 0 원칙을 유지합니다.


## 4.1.202 지역분할 수집상태 가독성 개선

QWGJK가 전국 pagination 중첩 후 지역분할 fallback으로 전환된 경우 수집상태 화면에서 전국 checkpoint 오류만 보이던 혼란을 줄였습니다. 지역분할 모드에서는 대상 기준일, 완료 지역/전체 지역, 현재 처리 지역명, 지역 진행률, 현재 지역 페이지를 별도 패널로 표시합니다. 최근 실행 내역도 raw scope key 대신 전국 현재·지역분할·과거이력으로 구분해 읽기 쉽게 표시합니다. 광역지역 이름은 이미 저장된 QWGJK/AIDFA normalized rows에서 읽으며 외부 API 호출은 없습니다.


## 4.1.201 QWGJK 지역분할 자동 폴백

전국 QWGJK가 1회 fresh replay 후에도 REPEATED_OR_OVERLAPPING_PAGE를 반복하면 같은 전국 cursor를 더 호출하지 않고, 이미 저장된 같은 회계연도의 정규화 자료에서 광역 region_code 계획을 fail-closed 방식으로 구성해 지역분할 수집으로 전환합니다. 최소 17개 first-tier region code가 확인될 때만 자동 전환하며, 한 isolated worker는 미완료 지역 1개만 최대 16페이지 진행하고 checkpoint에서 다음 worker가 이어받습니다. 모든 계획 지역이 완료되면 원 전국 checkpoint는 PARTITION_COMPLETE로 표시하되 source-wide archive 완전성은 주장하지 않습니다. 기존 예산자료·observation·중첩 전국 receipt는 보존합니다.


## 4.1.200 QWGJK 중첩페이지 자동 재생

운영에서 확인된 REPEATED_OR_OVERLAPPING_PAGE가 같은 next-page cursor를 반복 호출해 멈추는 경로를 수정했습니다. 첫 중첩 감지 후 다음 수집은 기존 정규화 자료를 보존한 채 page 1부터 새 receipt generation으로 한 번 자동 재생합니다. 재생 도중 같은 중첩이 다시 발생하면 REPLAY_EXHAUSTED로 고정해 추가 API 호출을 소비하지 않고 운영 오류로 승격합니다. abandoned receipt는 즉시 대량삭제하지 않고 기존 retention이 정리하도록 하여 256MB 환경의 삭제 잠금/메모리 압력을 피합니다. 원천 예산자료와 기존 normalized observation은 삭제하지 않습니다.


## 4.1.199 지방재정365 메모리 안전 재개

운영 화면에서 확인된 QWGJK 과거이력 MemoryPressureError와 isolated worker SIGKILL(-9)을 메모리 안전대기로 처리하도록 보완했습니다. 256MB isolated budget child는 한 scope에서 최대 16페이지만 처리하고 checkpoint를 남긴 뒤 다음 child가 짧은 간격으로 자동 재개합니다. 현재 QWGJK가 부분완료인 동안에는 같은 child에서 과거이력을 추가 실행하지 않습니다. MemoryPressure는 FAILED가 아니라 INCOMPLETE/checkpoint 보존으로 기록하고, SIGKILL(-9)은 WAITING_MEMORY로 표시합니다. 또한 low-memory child에서는 매 cycle 전체 예산 read-model을 다시 materialize하지 않고 누락·변경 classification만 bounded batch로 갱신합니다. 원천 API 호출한도·수집범위·저장자료는 변경하지 않습니다.


## 4.1.198 영업우선 예산 원클릭 보기

예산 화면에 영업우선 보기를 추가했습니다. 한 번 클릭하면 조명·등주 세부사업만 대상으로 잡고, 잔액이 남은 미집행·부분집행 사업만 PostgreSQL에서 먼저 필터링한 뒤 잔액 큰 순으로 표시합니다. 전액집행 사업과 AIDFA 구조예산은 이 보기에서 제외하고, 페이지 이동에서도 영업우선 상태를 유지합니다. 일반 품목·집행상태 필터로 이동하면 일반 예산 보기로 돌아갑니다. 원천 API 호출·수집범위·저장자료는 변경하지 않습니다.


## 4.1.197 예산사업 보기순서 개선

예산 기본 목록은 영업 판단에 바로 쓰기 쉽도록 잔액 큰 순을 기본값으로 변경했습니다. 잔액 큰 순·예산 큰 순·최근 갱신순·기관명순을 빠른선택으로 제공하며, PostgreSQL에서 정렬 후 LIMIT/OFFSET을 적용해 전체 자료를 메모리에 올리지 않습니다. 기존 품목·집행상태·기관 필터와 페이지 이동에서도 선택한 정렬을 유지합니다. 원천 API 호출·수집범위·저장자료는 변경하지 않습니다.


## 4.1.196 예산 화면 한눈에 보기 개편

기본 예산 화면을 실제 사업명·예산·집행·잔액 중심으로 재구성했습니다. 품목/집행상태 빠른선택, 현재 페이지 요약, 조명·등주 잔액 후보 표시, 태블릿·모바일 카드형 사업보기, 접힌 수집기술정보/AIDFA 편성근거, 분석 실행 전 빈 표 제거를 적용했습니다. 새 원천 호출이나 대량 선적재 없이 기존 bounded PostgreSQL 조회 결과만 사용합니다.


## 4.1.195 지방재정365 retention 잠금·오류 분리

예산 retention을 짧은 checkpoint별 transaction과 최대 500건 단위 receipt 삭제로 변경했습니다. retention 유지보수 실패는 원천수집 실패로 승격하지 않고 budget_maintenance_warning으로 분리해 호출한도 대기·부분수집 상태를 보존합니다. 예산자료·checkpoint 의미·365일 보관정책·API 호출한도는 변경하지 않습니다.


## 4.1.194 0건 납품요구 PostgreSQL 저장 오류 및 수집내역 화면 개선

나라장터의 정상 0건 응답을 4.1.193에서 허용한 뒤 빈 page receipt batch가 PostgreSQL 호환 계층의 `executemany()`까지 전달되면서 SQLSTATE 42P02가 발생하던 후속 오류를 수정했습니다. 빈 batch는 SQLite와 동일하게 실제 SQL을 실행하지 않는 no-op으로 처리합니다. 수집 상태의 최근 실행 내역은 태블릿·모바일 폭에서는 표 대신 카드형으로 표시하고, 넓은 화면의 표도 열 너비를 고정해 한글이 한 글자씩 세로로 깨지지 않도록 개선했습니다. 데이터·checkpoint·API 호출한도·수집범위는 변경하지 않습니다.


## 4.1.193 나라장터 0건 응답의 items 생략 호환

자동수집이 최신 D-1 날짜까지 도달했을 때 나라장터가 `resultCode=00`과 `totalCount=0`을 반환하면서 `items` 컨테이너를 생략하는 정상 0건 응답을 `SCHEMA:missing items container` 오류로 잘못 판정하던 경로를 수정했습니다. 이제 **성공 응답 + 명시적 totalCount=0**인 경우에만 items 생략을 0건으로 인정합니다. totalCount가 없거나 1 이상인데 items가 없으면 기존처럼 fail-closed로 오류 처리하여 실제 스키마 변화를 숨기지 않습니다. JSON/XML 모두 같은 규칙을 적용하며 DB/checkpoint/revision/receipt/자동수집 정책은 변경하지 않습니다.

## 4.1.192 배포 후 자동 수집 기본 ON

Cafe24 UNIFIED 운영은 backend 준비 완료 후 나라장터 조명·등주와 지방재정365 예산 수집을 자동 시작합니다. `G2B_AUTO_SYNC` 값이 없거나 과거 값 `0`이 남아 있어도 UNIFIED 자동수집을 막지 않습니다. 256MB 환경에서는 웹 프로세스가 직접 무거운 수집을 실행하지 않고 기존 isolated child queue에 `shopping → budget` 순서로 넣어 한 번에 한 child만 실행합니다. 메모리 대기는 60초 뒤 자동 재시도하고, 일일 API 한도 대기는 KST 다음 날짜까지 기다린 뒤 checkpoint에서 재개합니다. `G2B_AUTO_SYNC_DISABLE=1`은 비상 중지용 kill-switch로 계속 유지합니다.

## 4.1.191 Cafe24 외부 PORT 선바인딩형 lazy bootstrap

첫 접속 때 `run.py -> uvicorn -> main.py -> vnext_clean_app` 전체 import가 끝나기 전까지 Cafe24 외부 PORT가 응답하지 않는 구간을 줄였습니다. production의 `main.py`는 이제 무거운 `vnext_clean_app`을 동기 import하지 않고 작은 ASGI bootstrap만 즉시 생성합니다. Uvicorn이 먼저 외부 PORT를 열고 `/live` 및 브라우저 warmup 화면에 응답한 뒤, daemon loader가 full runtime을 불러와 준비 완료 즉시 동일 요청 경로를 실제 FastAPI 앱으로 넘깁니다. `/ready`는 full runtime 준비 전까지 계속 503으로 fail-closed이며 DB/수집/checkpoint/revision/receipt 정책은 변경하지 않습니다.

## 4.1.190 첫 접속 저장소 준비 화면 즉시 응답

배포·재기동 직후 `/collection-monitor` 같은 일반 화면으로 바로 들어오면 backend 초기화가 끝날 때까지 503을 반환하던 경로를 수정했습니다. 브라우저 GET 요청은 웹 프로세스가 살아 있는 동안 즉시 200 시작화면을 반환하고 2초마다 자동 재확인합니다. API 요청, POST 요청, 실제 backend 초기화 실패는 기존처럼 503으로 fail-closed를 유지합니다. 데이터 수집·DB/checkpoint/revision/receipt·메모리 안전정책은 변경하지 않습니다.

## 4.1.189 수집중 MemoryPressureError를 오류가 아닌 안전대기로 분류

나라장터 isolated child가 페이지 사이 메모리 가드에서 `MemoryPressureError`를 만나면 checkpoint는 이미 보존된 상태이므로 이를 일반 실패로 처리하지 않고 `WAITING_MEMORY`로 종료하도록 수정했습니다. 메모리 압박 직후 retention 작업도 추가 실행하지 않고 child를 종료해 메모리를 반환하며, `PROCESS_RSS_HOLD`/`CGROUP_WAIT_TIMEOUT` 같은 내부 가드 사유를 안전한 진단값으로 보존합니다. 메모리 한도 자체는 완화하지 않았고, DB/checkpoint/revision/receipt 및 API 정책은 변경하지 않습니다.

## 4.1.188 수집상태 자동새로고침 부하 완화

수집상태 화면이 작업이 없거나 완료·오류 상태에서도 5초마다 전체 페이지를 계속 새로고침하던 동작을 조정했습니다. 나라장터·지방재정365·과거매칭 중 하나라도 실제 실행 중이면 5초 주기를 유지하고, 대기·완료·오류 상태에서는 30초 주기로 낮춰 모바일 브라우저와 PostgreSQL read 부하를 줄입니다. 수집 로직, API 호출, checkpoint/revision/receipt, 자동수집 OFF 정책은 변경하지 않습니다.

## 4.1.187 수집상태 화면 read-path 경량화

`/collection-monitor`가 5초 자동새로고침 때마다 budget COMPLETE checkpoint의 page/item receipt를 다시 검증하던 고비용 read path를 제거했습니다. 모니터 전용 fast status는 budget dataset count를 일괄 집계하고, checkpoint 상태/최근 범위만 읽으며, per-checkpoint receipt 재검증은 수행하지 않습니다. 운영에서는 이 read-only snapshot을 기본 30초 캐시하고 화면의 RUNNING/오류 상태와 API quota는 별도 runtime 상태로 계속 갱신합니다. 나라장터 checkpoint도 최근 표시분만 읽고 상태별 총계는 SQL GROUP BY로 계산합니다. 외부 API 호출, 수집 범위, 저장자료, full readiness 검증 의미는 변경하지 않습니다.

## 4.1.186 manual source advisory lease pool 고갈 수정

4.1.185 실운행에서 `SHOPPING:WORKER:source_run:TimeoutError`가 확인되었습니다. 원인은 수동 shopping/budget cycle이 global shared advisory lock과 source-exclusive advisory lock을 각각 별도 PostgreSQL connection으로 잡아, 256MB 안전설정의 작은 DB pool(기본 1 + overflow 1)을 두 lease connection이 모두 점유한 상태에서 실제 checkpoint/data 작업이 세 번째 connection을 요구했던 구조였습니다. 4.1.186은 두 advisory lock을 **같은 PostgreSQL session 한 개**에서 보유하여 실제 수집용 connection을 남깁니다. DB 삭제·초기화, 기존 checkpoint/revision/receipt, API 호출한도, 자동수집 OFF, source별 상호배제 정책은 변경하지 않습니다.

## 4.1.185 isolated shopping worker 실제 오류 전달

4.1.184 실운행에서 child가 exit 1로 끝날 때 부모 화면에 generic `ISOLATED_WORKER_EXIT_1`만 남을 수 있던 경로를 보완했습니다. child의 실제 source error 또는 `WORKER:<stage>:<Exception>` 진단값을 PostgreSQL 설정에 저장하고 parent가 이를 그대로 표시합니다. 데이터 삭제·초기화, checkpoint/revision/receipt 정책, 자동수집 OFF, 256MB 순차 heavy-child 정책은 변경하지 않습니다.

## 4.1.184 production runtime setting DDL 제거 및 쇼핑 준비오류 진단

나라장터 수집 진행상태를 `app_settings`에 기록할 때 `set_setting()`이 매번 `init_db()`를 실행하던 구조를 제거했습니다. production에서는 startup이 schema를 소유하고 runtime get/set/source-credential I/O는 순수 SELECT/UPSERT만 수행하며, SQLite test fixture만 lazy schema bootstrap을 유지합니다. 또한 `shopping_recent`의 collection storage 준비단계를 명시적 failure boundary 안으로 옮겨 준비 실패도 `shopping_recent_last_error=PREPARE:<Exception>`으로 저장하고, isolated worker exit 1 시 부모 UI에 해당 원인을 표시합니다.

## 4.1.183 수집 충돌 방지와 모니터 경량화

256MB UNIFIED 환경에서 나라장터 shopping과 지방재정365 budget 수동수집이 동일 isolated-heavy-worker 슬롯을 공유하되 두 번째 요청이 탈락하지 않도록 순차 queue를 추가했습니다. 한 번에 child process는 하나만 유지하며 첫 작업 종료 후 다음 source를 자동 실행합니다. child 종료코드가 COMPLETE/키대기/호출한도/저장소/메모리/lease/부분완료 상태로 parent에 반영되어 RUNNING 고착을 방지합니다. 또한 `/collection-monitor`의 5초 read path에서 production `ensure_foundation()`/`shopping_store_v41.ensure_schema()` 반복 DDL·인덱스 확인을 제거해 collection write와의 PostgreSQL lock 경쟁을 줄였습니다. API 키·원천 호출 로직·DB 자료는 변경하지 않습니다.

## 4.1.182 RESULT_SERVER 지역별 업체 snapshot

RESULT_SERVER `/vendors`와 `/api/vendors`가 지역 선택 시 production PostgreSQL `vendor_rows()`로 빠지던 fallback을 제거했습니다. 로컬 수집기 snapshot 생성 시 전국 업체 합계와 함께 지역별 업체 합계를 `vendors:<지역명>` section으로 저장하며, 지역 section은 화면/API 최대 표시량과 같은 상위 1,000건으로 제한합니다. RESULT_SERVER는 지역 선택 여부와 관계없이 serving snapshot만 읽고 외부 DB read path로 이탈하지 않습니다. `/api/vendors`에는 `region` 검증도 추가했습니다.

## 4.1.181 production 업체집계 streaming

UNIFIED `/vendors`와 `/api/vendors`의 production `vendor_rows()`가 전국/지역 조달내역을 `shopping_rows(limit=None)`으로 한꺼번에 메모리에 올리던 구조를 제거했습니다. PostgreSQL에서는 SQLAlchemy server-side `stream_results`와 `max_row_buffer=250`을 사용해 active 조명·등주 row를 250행 cursor batch로 읽고, 납품요구 품목별 최신 변경차수만 즉시 선별해 업체 집계에 반영합니다. 업체·요청별 request-total fallback, 사업자번호 없는 동일상호 병합, 정렬·검색·최대 1,000건 반환 의미는 유지합니다. 테스트/SQLite 호환 경로는 기존 방식 그대로입니다.

## 4.1.180 RESULT_SERVER 예산 API bounded paging

`/api/budget` RESULT_SERVER 경로가 `budget_targets`와 `budget_prebid`를 각각 최대 500건 먼저 읽은 뒤 지역별 새 Python 리스트를 만들던 구조를 제거했습니다. 4.1.179의 bounded budget reader를 재사용해 지역 선택 시 최대 100건 단위로 읽고 최대 500건 결과만 누적합니다. 지역 미선택 시 기존 최대 500건 단일 조회 의미를 유지합니다.

## 4.1.179 RESULT_SERVER 예산 지역필터 bounded paging

RESULT_SERVER `/budget` 분석 화면에서 `budget_targets`와 `budget_prebid`를 각각 300건 선적재한 뒤 지역별 새 리스트를 다시 만들던 구조를 제거했습니다. 지역이 선택되면 serving snapshot을 최대 100건 단위로 읽고 `region_matches`로 걸러 최대 300건 결과만 누적합니다. 지역이 없으면 기존처럼 최대 300건 한 번 조회를 유지합니다. 기존 정렬·분류 의미는 유지하면서 256MB 웹 프로세스의 중복 materialization을 줄였습니다.

## 4.1.178 미래예산 evidence 조회 bounded 처리

`/budget` 미래예산 분석에서 인천 기관 범위를 Python 후필터가 아니라 `screen_budget_rows()` DB 조회 단계에 전달합니다. 후보 정확도를 위해 최대 500건은 유지하되, historical evidence가 붙은 dict는 `heapq.nlargest` 기반 top-K로 최종 200건만 유지해 500건 전체 enriched list와 후필터 복제 리스트가 동시에 존재하지 않게 했습니다. 기존 evidence 점수·정렬 기준은 유지합니다.

## 4.1.177 예산↔조달 매칭 후보 메모리 bounded 처리

`/budget`의 수동 예산↔조달 매칭은 기존처럼 최대 300개 예산과 3,000개 조달요청을 비교하지만, 모든 예산×조달 후보쌍을 `candidate_rows`에 누적하지 않습니다. 각 조달요청마다 현재 최적 예산 후보 1건만 즉시 유지해 임시 후보 상태를 조달요청 수에 비례하도록 제한했습니다. 기존 `ONE_DELIVERY_REQUEST_TO_ONE_BUDGET_PROJECT` 선택 규칙과 점수·정렬 의미는 그대로 유지합니다.

## 4.1.176 RESULT_SERVER 쇼핑 화면 bounded paging

RESULT_SERVER의 `/shopping` 화면에 남아 있던 5,000건 선적재를 제거했습니다. 날짜·품목·검색어는 serving SQLite에서 먼저 제한하고, 지역 필터가 필요한 경우 최대 250건 단위로 페이지를 읽어 정확한 지역 결과가 요청 limit에 찰 때까지만 누적합니다. 웹 프로세스가 다른 지역 수천 건을 한 번에 list/dict로 만들지 않도록 하면서 기존 화면 정렬·지역 의미는 유지합니다.

## 4.1.175 RESULT_SERVER 쇼핑 조회 bounded 처리

RESULT_SERVER의 /api/shopping이 요청 limit과 무관하게 최대 5,000건을 먼저 SQLite에서 읽고 Python에서 날짜 필터·슬라이스하던 메모리 낭비를 제거했습니다. shopping source_date 범위를 serving_rows SQL WHERE에 직접 적용하고 요청 limit(최대 1,000)을 DB 단계에서 적용합니다. shopping 날짜 index도 추가해 조회 범위를 줄였습니다. 기존 UNIFIED PostgreSQL shopping 조회 로직은 변경하지 않았습니다.

## 4.1.174 production env 문법 안전화

배포 템플릿의 `.env.example`에 남아 있던 두 문법 오류를 수정했습니다. `G2B_DESTRUCTIVE_RESET_CONFIRM` 값 뒤 설명문을 주석으로 분리하고, `G2B_MEMORY_SOFT_LIMIT_MB=160`과 `G2B_ISOLATED_WORKER_SOFT_LIMIT_MB=112` 사이의 literal `\\n` 문자열을 실제 줄바꿈으로 복구했습니다. 회귀테스트는 template의 non-comment assignment를 직접 파싱해 destructive-reset=0, web soft-limit=160, isolated-worker=112, fresh-start=0을 고정합니다.

## 4.1.173 PostgreSQL 결과서버 경량화 no-op

RESULT_SERVER의 구형 경량화 기능이 production PostgreSQL에서도 `sqlite_master`, SQLite `DROP TABLE`, `VACUUM`, `sqlite3.connect(current_db_path())`를 실행할 수 있던 잘못된 경로를 차단했습니다. production PostgreSQL에서는 이 기능이 즉시 `SKIPPED_POSTGRESQL`로 끝나며 DB 파일경로 조회, SQLite 접속, 테이블 삭제, VACUUM을 전혀 수행하지 않습니다. SQLite 테스트/호환 모드에서만 기존 경량화 기능이 유지됩니다.

## 4.1.172 serving SQLite 경로 안전화

RESULT_SERVER compact snapshot 저장경로가 production PostgreSQL 논리 locator를 filesystem 경로로 오인해 `postgresql:/configured/g2b-serving.sqlite3`처럼 잘못 만들어질 수 있던 경로를 제거했습니다. `G2B_SERVING_DB_PATH`가 명시되면 그 값을 우선하고, 로컬/테스트 SQLite에서는 기존 DB 옆에 `g2b-serving.sqlite3`를 유지하며, production PostgreSQL에서는 기본값을 `/app/user_data/g2b-serving.sqlite3`로 고정합니다.

## 4.1.171 result-sync 웹 프로세스 격리

4.1.170에서 업로드 크기를 제한했지만 RESULT_SERVER 웹 프로세스가 gzip 해제·JSON 파싱·snapshot import를 직접 수행하던 마지막 메모리 피크 경로를 제거했습니다. 웹 프로세스는 인증 후 최대 4MiB 요청을 임시파일로 스트리밍하고, 실제 해제·파싱·SQLite import는 별도 disposable worker에서 실행합니다. worker는 기존 heavy-worker와 같은 단일 lock, parent watchdog, oom_score_adj=900, 기본 112MiB soft limit을 사용합니다. cgroup memory.oom.group=1이면 worker를 시작하지 않고 503 MEMORY_PRESSURE로 fail-closed하며, worker가 OOM signal로 종료돼도 웹 서버는 계속 살아 있습니다.

## 4.1.170 result-sync 메모리 상한

256MiB RESULT_SERVER에서 대형 snapshot 업로드가 웹 프로세스를 OOM으로 종료시키는 경로를 차단했습니다. /api/result-sync는 더 이상 request.body()로 무제한 요청을 먼저 적재하지 않고 request.stream()을 사용해 압축 본문 4MiB에서 즉시 중단합니다. gzip 해제 JSON은 12MiB로 제한하고, 수신 직전 cgroup/process memory guard가 안전하지 않으면 503 MEMORY_PRESSURE로 fail-closed 합니다. import_snapshot은 parsed payload 전체를 _json_safe로 복제하지 않아 snapshot 메모리 피크를 줄였습니다. 로컬 collector도 이미 생성한 gzip 파일을 재사용하며 4MiB/12MiB 상한 초과 시 네트워크 전송 전에 중단합니다.

## 4.1.169 70초 장기 생존 gate

과거 Cafe24에서 약 45초 이후 프로세스가 종료되던 실제 장애를 회귀검증 대상으로 고정했습니다. Python 3.12 + 실제 PostgreSQL 환경에서 정상 UNIFIED runtime을 기동한 뒤 5초 간격으로 70초까지 /live를 확인하고, 45초·70초 /health에서 backend/operational readiness, memory guard, OOM kill=0, RSS soft limit 미만을 검증합니다. /proc의 VmHWM도 160MiB 미만인지 확인하고 idle 상태에서 g2b_heavy_worker가 예기치 않게 뜨지 않는지 검사합니다. 자동수집·post-boot maintenance·match auto refresh는 gate에서 명시적으로 OFF입니다.

## 4.1.168 launcher 자동 복구

정상 운영은 기존의 단순 `python run.py → Uvicorn → main:app` 구조를 그대로 사용합니다. Uvicorn dependency/import 또는 main:app 초기 기동이 예외나 비정상 SystemExit로 실패할 때만 Python 표준라이브러리의 최소 복구 HTTP 서버로 자동 전환합니다. 복구 서버는 /live·/health를 200으로 유지해 Cafe24 전체 502를 피하되 /ready는 503으로 유지해 정상 운영 준비상태로 오인하지 않습니다. 복구 경로는 PostgreSQL/schema/reset/maintenance/source API를 전혀 실행하지 않습니다. 과거 4.1.164의 유용한 failover만 복원하며 256MiB에서 위험했던 progressive runtime loader는 복원하지 않습니다.

## 4.1.167 4.1.165 운영안전 기능 복원

과거 4.1.156~4.1.160에서 검증된 운영 식별과 비파괴 안전장치를 현재 메모리 안전판에 선별 복원했습니다. /live·/health는 환경변수만 믿지 않고 GITHUB_SHA → 실제 .git checkout → 수동 fallback 순으로 배포 HEAD를 판정하고, 핵심소스 SHA-256 fingerprint·process instance ID·기동시각·uptime·deployment verdict를 제공합니다. 결과 snapshot 존재확인은 파일/폴더를 만들지 않는 read-only 경로로 바꿨습니다. 기존 PostgreSQL이 있는 상태에서 schema reset은 G2B_V41_FRESH_START=1과 G2B_DESTRUCTIVE_RESET_CONFIRM=1이 모두 있어야만 가능하며, 완료 marker가 있으면 정상 기동 중 legacy 파일도 임의 삭제하지 않습니다.

## 4.1.166 4.1.165 저메모리 기능 복원

과거 안정판 4.1.165에서 검증됐던 4.1.150~4.1.152 저메모리 개선을 현재 SINSUNG 방식 메모리 하드닝 위에 선별 복원했습니다. /api/budget는 전체 회계연도 분석을 메모리에 만들지 않고 PostgreSQL bounded slice만 사용하며, shopping partial-resume receipt 검증은 전체 item fetchall 대신 page 순서 스트리밍으로 처리합니다. COMPLETE 예산 snapshot reconciliation도 stale current key를 400건씩 삭제합니다. 현재 256MiB heavy-worker 격리/cgroup batch guard는 그대로 유지합니다.

## 4.1.141.5 SINSUNG 방식 메모리 하드닝

SINSUNG V7.1.24의 검증된 메모리 보호 원리를 G2B 데이터 수집 구조에 맞게 확장합니다. 256 MiB UNIFIED 웹 프로세스는 heavy 작업을 직접 수행하지 않고 별도 disposable worker로 분리합니다. worker는 단일 file-lock, parent watchdog, 높은 OOM 희생 우선순위(oom_score_adj=900), 기본 112 MiB process soft limit을 사용합니다. shopping/budget 수집 페이지, 분류 batch, 과거 예산-조달 match scan 사이마다 cgroup/process 메모리를 다시 검사해 실행 중 압박이 커지면 checkpoint를 보존한 채 중단합니다. cgroup v2 memory.oom.group=1인 환경에서는 worker 격리가 웹 보호를 보장하지 못하므로 아예 heavy worker를 시작하지 않습니다.

## 4.1.141.4 256MB 응급 생존 모드

실제 Cafe24 256 MiB에서 4.1.141.3 cgroup guard 적용 후에도 OOM 재종료가 확인되어, 4.1.141.4는 웹 프로세스 생존을 최우선으로 fail-closed 합니다. cgroup limit이 320 MiB 이하인 UNIFIED/RESULT_SERVER에서는 자동수집, 관리자 수동 수집 thread, match rollover, 웹 요청 안에서의 대량 예산↔조달 매칭을 시작하지 않습니다. 현재 압력 자체는 /health의 memory_guard_ok로, heavy 작업 허용 여부는 memory_heavy_work_ok / memory_low_memory_web_hold로 분리해 확인합니다. DB/revision/checkpoint/receipt는 변경하거나 초기화하지 않습니다.

## 4.1.141.3 cgroup 이중 메모리 안전 패치

RSK V7.0.90의 cgroup-aware 메모리 방어 원리를 G2B 구조에 맞게 적용합니다. 기존 process RSS 160 MiB soft limit은 그대로 유지하고, Linux cgroup v1/v2의 실제 컨테이너 current/limit/stat/events를 추가로 감시합니다. 256 MiB 한도에서는 effective pressure 약 208 MiB부터 새 heavy 작업을 HOLD하고 약 224 MiB부터 BLOCK 상태로 판단합니다. file/page cache는 전부 위험 메모리로 계산하지 않고 최대 32 MiB 바닥만 pressure에 반영합니다. /health에서 cgroup limit/current/peak/effective/wait/block/OOM 값을 확인할 수 있습니다.

## 4.1.141.2 분류 메모리 안전 패치

예산 분류 시 전체 current hash dict·전체 pending set·전체 classification list를 동시에 만들지 않습니다. PostgreSQL LEFT JOIN이 미분류/변경 key만 골라내고 `record_key` keyset pagination으로 bounded batch 처리합니다. 각 batch 조회 연결은 yield 전에 닫아 기존 DB pool 1+1 안전경계와 함께 동작합니다.

## 메모리 안전 패치

4.1.141.1은 4.1.141 기능을 유지하면서 Cafe24 웹 프로세스 생존을 우선하도록 운영 안전경계를 강화합니다. 자동수집·기동 후 분류복구·파생 match refresh는 기본 OFF이며, 메모리-heavy 작업은 프로세스당 하나만 실행되고 RSS가 `G2B_MEMORY_SOFT_LIMIT_MB` 이상이면 새 작업을 시작하지 않습니다. 기본 PostgreSQL pool은 1 + overflow 1입니다.


## 운영 구조

운영 진입점은 `main.py -> vnext_clean_app.py` 하나입니다. 구형 2.x 대시보드,
scheduler, serving table 체계는 clean vNext 운영 경로에서 사용하지 않습니다.

핵심 데이터 흐름:

`전국 예산·사업 수집 → 즉시 정규화 → 기관/사업 정리 → 조명·등주 분류 → 영업후보 조회`

원천 응답은 수집 중 메모리에서 검증·정규화하고 운영 DB에는 필요한 필드와 source hash만 저장합니다.

## 현재 운영 화면

- `/dashboard` — 전체 현황과 수집 준비상태
- `/collection-monitor` — 정규화 저장건수/checkpoint 기반 수집 진행상태
- `/shopping` — 쇼핑몰 납품요구 후분류 조회
- `/vendors` — 저장된 쇼핑몰 납품요구 기반 업체 분석
- `/budget` — QWGJK/AIDFA/교육 정규화 예산 기반 영업후보 + 수집된 current-state 원천자료 분리 표시(QWGJK/AIDFA/교육청); 수집된 current-state 표는 app projection이 아니라 canonical `g2b_budget` normalized current를 직접 읽어 projection 지연에도 저장자료를 숨기지 않음
- `/raw` — 4.1에서 폐기된 호환 URL이며 `/collection-monitor`로 이동
- `/settings` — API 키, 안전상태, 4.1 저장정책 확인
- `/api/collection-status` — 인증된 수집상태 JSON
- `/__ai_space_health` — 플랫폼 프로세스 생존 확인 전용(저장소/예산 readiness 미접촉)
- `/live` — 애플리케이션 프로세스 생존 확인
- `/health` — 저장소 상태를 포함한 진단(장애 시에도 HTTP 200)
- `/ready` — app + budget 전체 운영 준비상태 gate

수집 상태 화면은 5초마다 다시 읽으며 외부 API를 호출하지 않습니다. 수동 수집은 원천별로 분리해 `나라장터 조명·등주 수집`과 `지방재정365 예산 수집`을 각각 실행하며, 서로의 API 키·호출한도·checkpoint를 공유하지 않습니다. AIDFA 상태는 현재연도 기초편성과 다음연도 미래예산을 분리해서 표시합니다. 수동 source가 하나라도 실행 중이면 `/api/collection-status`의 aggregate 상태도 `RUNNING`으로 유지하며 `manual_sources_running`에 실행중 source 수를 표시합니다. 두 source가 모두 종료된 뒤 aggregate 상태는 마지막에 끝난 worker 값이 아니라 `shopping_run_state`와 `budget_run_state`를 함께 합산해 결정하므로 한쪽의 오류·저장소대기·키대기·호출한도대기가 다른 쪽 완료로 가려지지 않습니다. 같은 API 응답의 `source_quota.shopping`과 `source_quota.budget`은 나라장터/지방재정365 로컬 호출량을 서로 독립적으로 제공합니다. QWGJK 카드에는 실제 rolling 시작일(2026년에는 2026-01-01)부터 D-1까지 예산이력 완료일수·전체일수·진행률·다음 수집일도 표시합니다. checkpoint가
`RUNNING`인데 5분 이상 갱신되지 않으면 실제 실행중으로 표시하지 않고
`갱신중단`으로 표시합니다. 다만 source runtime이 `WAITING_QUOTA`/`WAITING_KEYS`/`WAITING_STORAGE`처럼 명시적인 대기 상태이면 해당 상태를 우선 표시해 정상 quota 대기를 오류·갱신중단으로 오판하지 않습니다.

## 저장소

4.1 운영은 **PostgreSQL 하나를 단일 source of truth**로 사용합니다. 운영 SQLite
의존성은 제거했습니다. 같은 PostgreSQL 안에서 workload별 schema만 분리합니다.

- `g2b_app` — CONTROL(관리자/세션/설정/API 키/checkpoint) + 2026-01-01 이후 정규화 조명·등주 사업자료 + READ 지원
- `g2b_budget` — 정규화 예산 current state + 최대 1년 변경이력 + 예산 분류/projection
- `g2b_meta` — 4.1 fresh-start 같은 release bootstrap marker만 보관

운영 연결은 PostgreSQL 연결원천 하나를 사용합니다. Cafe24가 `DB_*` 시스템 변수를
자동 제공하면 그대로 사용하며, 자동변수가 없을 때만 `G2B_DATABASE_URL`을 직접
등록합니다. 둘이 동시에 존재하면 `G2B_DATABASE_URL`이 우선하므로 중복 등록은
피합니다. control과 budget은 같은 SQLAlchemy connection pool을 공유합니다.

첫 4.1 전환에서는 사용자가 승인한 대로 기존 G2B 4.0 데이터는 마이그레이션하지 않고
초기화한 뒤 공식 원천에서 다시 수집합니다. 삭제 범위는 G2B 소유 schema
(`g2b_app`, `g2b_budget`)와 구형 G2B SQLite 파일로 제한하며, PostgreSQL advisory lock과
durable marker로 재배포 중 중복 초기화를 막습니다.

- 운영에서 SQLite는 사용하지 않습니다.
- `G2B_TEST_MODE=1`인 회귀테스트에서만 SQLite fixture를 허용합니다.
- `/__ai_space_health`는 저장소·예산 상태를 전혀 조회하지 않는 순수 프로세스 생존 endpoint로 유지합니다.
- `/live`와 `/health`는 DB 장애가 있어도 플랫폼 502로 무너지지 않게 유지합니다.
- DB/schema/권한 계약이 정상일 때만 `/ready=200`입니다. 예산 영역만 준비되지 않은 경우 `/ready=503`이어도 공통 backend와 나라장터 shopping 경로는 별도 상태로 유지됩니다.
- 과거 예산 변경이력은 365일 보관하고 미래 회계연도 current state는 기간만으로 삭제하지 않습니다.
- 전국 QWGJK/AIDFA snapshot이 COMPLETE이고 1건 이상 수신되면 이번 generation에 없는 같은 회계연도 항목은 current에서 제외합니다.
- 0건 COMPLETE 응답은 원천 일시 이상 가능성을 고려해 기존 current를 즉시 전부 삭제하지 않습니다.
- 완료/종료된 page/item receipt 기본 retention은 3일입니다.
- RUNNING/FAILED/INCOMPLETE checkpoint의 현재 generation receipt는 resume를 위해 최대 1년 보호합니다.
- QWGJK가 일일 quota 안에 끝나지 않으면 다음 cycle/다음 KST 날짜에도 해당 미완료 snapshot을 먼저 resume하고, 더 최신 같은 회계연도 COMPLETE snapshot이 생기면 오래된 미완료는 SUPERSEDED 처리합니다.

## 최초 관리자

최초 접속 시 관리자 계정이 0명인 경우에만 `/setup`이 열립니다.

- 아이디: 4~50자
- 비밀번호: 10자 이상
- 최초 관리자 생성은 DB transaction으로 단일 생성 보장
- 이후 `/setup`을 통한 두 번째 관리자 생성 차단
- `G2B_SETUP_TOKEN`은 사용하지 않음

## 원천 API 키

지원 자격증명:

- 나라장터: `G2B_SERVICE_KEY`
- 지방재정365: `LOFIN_API_KEY`
- 지방교육재정알리미: `EDUINFO_API_KEY`

키는 두 방법으로 설정할 수 있습니다.

1. 배포 환경변수
2. 로그인 후 `/settings`의 관리자 API 키 입력

관리자 입력 키는 일반 `app_settings`와 분리된
`vnext_source_credentials` table에 저장하며 웹 화면/API에서 원문을 다시 반환하지
않습니다. 같은 종류의 환경변수가 있으면 환경변수가 우선합니다.

GitHub Actions의 bounded canary/small-validation은 별도 실행 환경이므로 웹에서 저장한
Cafe24 DB 키가 GitHub runner로 자동 전달되지 않습니다. GitHub에서 수동 검증을
실행하려면 repository secret `G2B_SERVICE_KEY`, `LOFIN_API_KEY`가 별도로 필요합니다.
교육 API live transport는 아직 HOLD이므로 EDUINFO를 source traffic에 사용하지 않습니다.

## 수집 안전경계

4.1 Cafe24 기본 역할은 `UNIFIED`입니다.

- shopping: 2026-01-01 이후 전국 원천을 날짜순으로 확인하되 조명·등주 범위만 저장
- AIDFA: 다음 회계연도 세출예산을 우선 갱신하고, 현재 회계연도(2026) 기초편성예산도 최대 16페이지/회차로 수집하고 같은 KST 날짜의 COMPLETE는 재사용하되 날짜가 바뀌면 다시 확인해 1월 1일 예산범위를 보완
- budget QWGJK: 현재 회계연도 최신 snapshot은 current state로 유지하고, 2026-01-01부터 시작해 이후 rolling 365일 범위의 과거 snapshot은 남는 LOFIN 호출량으로 순차 보강합니다. 과거분은 `history:연도:날짜` 전용 checkpoint와 revision history로만 저장해 현재 예산값을 과거값으로 되돌리지 않습니다; current snapshot은 지방재정 source-safe 경계인 D-1을 사용하며, 과거에 당일 조회로 COMPLETE·0건이 된 scope는 다음 KST 날짜에 한 번 재검증해 영구 0건 checkpoint로 굳지 않게 합니다
- 용역공고·개찰·낙찰·계약의 원천수집·입찰예측: G2B에서 제거, NO1 담당
- 예산사업 집행검증용 낙찰 evidence: 원천수집을 다시 열지 않고 이미 확보된 공식 결과행/독립 export에서 최소 필드만 source-free compact 저장
- 물품 입찰공고: G2B에서 제거, NO1 담당
- bulk historical: HOLD
- `APPROVED_HISTORICAL` execution context: 비활성
- 교육 vNext live transport: HOLD
- bounded canary / small-validation: 수동 검증용이며 production PostgreSQL을 사용하지 않음
- checkpoint/저장건수가 있어도 전체 원천 완전수집으로 자동 간주하지 않음

일반 운영 수집과 별개로 배포 전 검증은
`bounded canary → one-day small-validation → 결과 감사` 순서로 수행합니다. 로컬 호환 수집기와 Windows 전체수집 런처도 기본 시작일을 2026-01-01로 사용하고 현재 4.1.x 패치버전을 허용하므로, 재시작 시 동일 checkpoint에서 이어서 진행할 수 있습니다.

## 데이터 원천별 현재 상태

### 나라장터
4.1 운영 수집 범위는 쇼핑몰 납품요구입니다. 2026-01-01부터 전국 원천을 날짜순으로 확인하고
조명·가로등주 대상만 저장합니다. 운영 PostgreSQL shopping은 대상 행을 저장하는 순간 transient 원천행에서 deterministic 분류를 함께 기록하므로 날짜별 post-classification DB scan이 필요하지 않습니다. catch-up 시작 시 shopping/foundation/receipt schema를 1회 준비하고, 이후 날짜별 수집은 준비된 schema를 재사용해 checkpoint 조회와 page transaction만 수행합니다. catch-up 수집은 post-classification을 defer하여 반복 호출을 제거하고, 운영 classifier의 batch-end 확인도 DB를 읽지 않는 `NORMALIZED_AT_INGEST` no-op입니다. 하위 storage scope와 source guard도 같은 2026-01-01 경계를 사용하며, 기존 10월 이후 checkpoint의 resume 계약 ID는 호환성을 위해 유지합니다. 회당 날짜창은 최대 62일이지만 한 날짜의 source context는 최대 64요청, 수집 페이지는 최대 40페이지로 제한합니다. 로컬 900회 안전한도에 도달하면 오류로 끝내지 않고 `WAITING_QUOTA`로 남겨 다음 KST 날짜에 같은 checkpoint부터 이어갑니다. 한 날짜가 여러 페이지인 경우 각 페이지의 정규화 저장·receipt·다음 page checkpoint를 같은 transaction으로 확정하며, quota/네트워크 중단 뒤에는 마지막 미완료 page부터 resume합니다. RUNNING/FAILED/INCOMPLETE는 page/item receipt를 그대로 보존합니다. 나라장터 `shopping_delivery` 실시간 source의 `totalCount`가 page 사이에서 증가하면 같은 generation을 계속 확장하되 기존 receipt와 source-key 중복/겹침 검사를 유지합니다. 이 완화 규칙은 shopping 전용이며 예산/기타 collector의 total 변화는 기존처럼 이상으로 중단합니다. baseline 완전수집 후에는 최근 최대 7일만 bounded replay로 하루 1회 재확인하며, 동일 source identity는 upsert하고 새 변경차수는 새 source identity로 추가합니다. 백로그가 남아 있으면 이 재확인은 실행하지 않아 900회 quota를 과거 catch-up보다 먼저 사용하지 않습니다. 최근 7일 밖의 COMPLETE 날짜는 별도 cursor로 하루 최대 2일씩 순환 재확인하며, 7일 재확인과 합쳐도 날짜별 64요청 상한 기준 이론상 최대 576회(7×64 + 2×64)라 로컬 900회 안전한도에 여유를 남깁니다. COMPLETE 재확인에서는 해당 source 날짜의 receipt 전체를 기준으로 이전 target row를 reconcile합니다. 이번 완전 응답에 없으면 `MISSING_FROM_COMPLETE_SOURCE`, 같은 identity가 비대상 코드로 바뀌면 `OUTSIDE_TARGET_SCOPE`로 inactive 처리하되 행 자체는 삭제하지 않아 변경차수 이력을 보존합니다. 원천이 0건인 COMPLETE 응답은 일시적 no-data 가능성을 고려해 전량 inactive 처리를 하지 않습니다. 일반 shopping/vendor 영업화면은 active row만 사용하고, 내부 history 조회는 inactive 이력까지 포함할 수 있습니다. 상태/대시보드 집계도 같은 기준을 사용해 현재 유효(active) 납품요구와 보존된 전체 이력, 그중 inactive 이력을 분리 표시합니다. `shopping_store_v41.count().records`는 호환성을 위해 전체 이력 건수를 유지하고, 신규 `active_records / inactive_records / history_records`를 별도로 제공합니다. 반대로 total 감소, source-total underrun, 조기 빈 페이지, 페이지 겹침은 현재 page를 normalized DB에 저장하지 않고 INCOMPLETE로 멈춘 뒤 다음 cycle에서 그 날짜만 새 generation의 page 1부터 재검증합니다. COMPLETE가 확정되면 page response hash들의 digest와 generation·page/fetched/saved/source-total·완료사유·query contract를 checkpoint 안의 compact completion marker로 함께 확정하고 page/item receipt는 삭제합니다. 이후 COMPLETE 날짜는 marker 한 건만 검증해 source API와 대용량 receipt scan 없이 즉시 건너뜁니다. 4.1.61 이전 COMPLETE scope는 첫 4.1.62 실행에서 기존 receipt를 한 번 검증한 뒤 같은 marker로 승격합니다. 실제 verified partial checkpoint에서 이어진 결과는 `resumed=true`로 보고하며, 손상되거나 계약이 맞지 않아 새 generation으로 replay하는 경우에는 resume로 표시하지 않습니다. 용역공고·개찰·낙찰·계약과 물품 입찰공고는
NO1 담당으로 분리되어 G2B source allowlist에서도 차단됩니다.

### 예산사업 → 용역·전기/조명공사 집행 evidence
- `budget_execution_evidence_vnext.py`는 나라장터 source API를 호출하지 않는 DB/입력행 전용 계층입니다. 기존 source guard의 용역·개찰·낙찰·계약 차단은 그대로 유지합니다.
- 저장 대상은 `SERVICE_AWARD / ELECTRICAL_WORK_AWARD / LIGHTING_WORK_AWARD` 세 종류이며 공고번호·차수·입찰분류·재입찰번호, 낙찰일, 기관, 공고명, 낙찰업체·금액·낙찰률, 연결된 예산사업 identity와 매칭근거만 저장합니다. 원본 JSON, 참가업체 전체, 투찰률 분포, 예비가격은 저장하지 않습니다.
- 같은 회계연도 + 정확한 기관(또는 행정개편 lineage) + 범용 LED/조명/전기 단어가 아닌 고유 사업·시설·지역 identity가 있어야 연결합니다. 동일 낙찰 execution이 복수 예산사업과 같은 강도로 연결되면 fail-closed로 모두 저장하지 않습니다.
- NO1 재활용은 향후 파일/API 같은 독립 export 계약만 허용하고 NO1 DB 직접조회·공유스키마·코드 import는 금지합니다. G2B의 목적은 입찰예측이 아니라 예산사업이 실제 어떤 방식으로 집행됐는지 확인하는 evidence입니다.

### 실행 evidence 단방향 ingress
- `budget_execution_evidence_ingress_vnext.py`는 NO1 또는 별도 공식결과 추출기가 만든 `g2b-execution-evidence-import-v1` JSON 문서만 받습니다. NO1 DB·스키마·ORM을 직접 연결하지 않습니다.
- 문서는 최대 5,000건이며 공고번호/차수/입찰분류/재입찰번호, 공식 낙찰일, 기관, 사업명, 낙찰업체·금액·낙찰률만 허용합니다. 참가업체, 예정가격, 예비가격, 추천·예측·모델·raw payload 필드는 exact allowlist에서 거부합니다.
- 동일 execution의 정확한 중복은 제거하고 서로 다른 내용의 중복은 import 전체를 거부합니다. 저장 시 원문 대신 `ingress_source + ingress_row_digest(SHA-256)`만 evidence에 추가합니다.
- 비용 우선순위는 `NO1 저장결과 단방향 export → execution gap 확인 → 부족한 공고만 공식 API 후속조회 → 같은 compact import 계약`입니다. 자세한 필드 매핑은 `docs/G2B_EXECUTION_EVIDENCE_INGRESS.md`를 따릅니다.

### 지방재정365
- QWGJK: 2026-01-01부터 세부사업/집행 snapshot 이력 보강 + 최신 snapshot current state 유지
- AIDFA: 세출예산 appropriation 수집 구조
- 예산 read-model/UI/API는 저장된 광역코드·광역명으로 전국 또는 17개 시·도별 조회를 지원하며 지역 조회 때문에 원천 API를 추가 호출하지 않습니다.
- 교육청 예산도 같은 17개 시·도 canonical region 규칙을 사용하되 live transport HOLD는 그대로 유지합니다.

QWGJK bounded canary가 존재하지만 AIDFA whole-source completeness는 아직 검증 완료로
선언하지 않습니다.

### 인천광역시 기관별 예산 조회
- 예산 화면의 기본 지역은 `인천광역시`, 기본 기관범위는 `인천광역시 전체`입니다.
- 기관 메뉴에서 `인천광역시 / 인천광역시 종합건설본부 / 인천경제자유구역청 / 인천광역시 상수도사업본부 / 인천광역시 도시철도건설본부`와 현재 2군·9구를 선택할 수 있습니다.
- 군·구 메뉴는 강화군, 옹진군, 제물포구, 영종구, 미추홀구, 연수구, 남동구, 부평구, 계양구, 서해구, 검단구를 사용합니다.
- 기관범위는 PostgreSQL의 `org_name / institution_name / dept_name`에 먼저 적용한 뒤 분류와 pagination을 수행하므로, 다른 기관의 앞쪽 행 때문에 선택기관의 QWGJK 세부사업이 누락되지 않습니다.
- 선택기관의 QWGJK `세부사업·집행` 행에서 사업명, 담당부서, 예산액, 집행액, 잔액, 미집행/부분집행/전액집행과 집행률을 그대로 확인합니다.

### 모바일 수집상태 표시
- `수집 상태 → 최근 실행 내역`은 데스크톱에서는 기존 7열 표를 유지하고, 640px 이하 모바일에서는 카드형으로 표시합니다.
- 모바일 카드에는 자료명·상태, 수집범위, 갱신시각, 페이지수, 저장건수, 오류를 세로 흐름으로 배치하며 긴 오류문구는 카드 폭 안에서 줄바꿈합니다.
- 수집 checkpoint·API 호출·저장 로직에는 영향을 주지 않는 표시 전용 변경입니다.

### 예산 분류 PostgreSQL 동기화 복구
- 4.1.137~4.1.139에서 예산 화면의 분류 필터는 PostgreSQL `budget_classifications`를 조회했지만 일부 분류 작업은 앱 호환 `classifications`에만 기록될 수 있어, 저장된 QWGJK가 있어도 `조명` 선택 시 0건으로 보일 수 있었습니다.
- 4.1.140부터 budget 분류의 화면 기준은 PostgreSQL `budget_classifications`이며 신규/변경 QWGJK·AIDFA 분류를 PostgreSQL과 호환표에 동기화합니다.
- 재배포 시 backend 준비 후 외부 API 호출 없이 저장된 normalized budget state를 읽어 누락/오래된 PostgreSQL 분류만 자동 복구하고, 그 다음 자동 API 수집 worker를 시작합니다.
- 기존 예산·집행·revision·checkpoint를 삭제하거나 초기화하지 않습니다.

### QWGJK 세부사업·집행 우선 화면
- 예산 화면의 주목록은 QWGJK `DETAIL_EXECUTION` 사업입니다. 파란 `세부사업·집행` 배지 아래에 `미집행 / 부분집행 / 전액집행`, 집행률, 기준일을 함께 표시합니다.
- 지역·기관/사업 검색·분류·집행상태로 조회할 수 있고, 200건 단위로 다음/이전 페이지를 이동합니다.
- 조명·등주·전기·태양광 분류 필터는 PostgreSQL의 exact-current `budget_classifications`를 먼저 JOIN한 뒤 LIMIT/OFFSET을 적용합니다. 앞쪽 일부 기관 2,000건을 먼저 자른 뒤 Python에서 분류하던 방식은 사용하지 않습니다.
- 따라서 특정 군·구의 데이터가 앞쪽에 몰려 다른 기관의 조명사업이 화면에서 사라지는 현상을 방지합니다.
- 과거 QWGJK↔LED·등주 조달 검증과 기관별 구매패턴은 삭제하지 않지만 기본 화면의 핵심기능에서 내려 보조 참고 기능으로만 표시합니다.

### 과거 예산 → 실제 LED·등주 조달 검증
- 기본 검증은 2026 QWGJK 세부사업과 나라장터 LED·등주 납품요구를 저장자료끼리 비교합니다.
- 2026 표본이 `예산사업 30건 이상 + 높은 일치 사업 10건 이상`에 못 미치면 2025 검증자료 확장을 권고합니다.
- 2025 확장은 일반 운영수집 범위를 넓히는 방식이 아니라 `MATCH_BACKFILL_SHOPPING` / `MATCH_BACKFILL_BUDGET` 전용 source context로만 실행합니다.
- 허용 원천은 `2025-01-01~2025-12-31 쇼핑몰 납품요구 중 LED·등주`와 `2025 QWGJK 세부사업`뿐입니다. AIDFA·입찰·용역·낙찰·계약 일반수집은 이 모드에서 열지 않습니다.
- QWGJK 2025는 historical revision으로 저장하고 현재 2026 budget current-state를 덮어쓰지 않습니다. shopping은 별도 `match-backfill:2025:*` checkpoint namespace를 사용합니다. 2025 백필 진행률은 메모리 상태가 아니라 durable checkpoint와 저장된 match run에서 복구하므로 재배포·재기동 뒤에도 완료 날짜수·다음 resume 날짜·2025/2026 evidence 수가 유지됩니다.
- 1회 백필 cycle은 QWGJK 대표 snapshot을 resume하고, shopping은 기본 7일씩만 진행합니다. 기존 500/900 일일 API quota와 global operational lease를 그대로 적용합니다.
- 매칭결과는 원본자료를 복제하지 않고 `budget_shopping_match_runs` / `budget_shopping_match_evidence`에 기관·사업·실제 조달·점수·근거·금액·시차만 compact evidence로 저장합니다. 나라장터 상세품목은 `납품요구번호 + 상세순번`별 최신 변경차수만 남긴 뒤 같은 납품요구번호를 실제 구매 1건으로 묶고, LED·등주 대상 상세품목의 금액만 합산합니다. 반복되는 납품요구 전체금액(`dlvrReqAmt`)은 비대상품목 혼입 가능성이 있어 과거예산 매칭금액에 사용하지 않습니다. 같은 납품요구번호 안에서 비어 있지 않은 수요기관이 서로 다르거나, 업체 사업자번호가 충돌하거나(사업자번호가 전혀 없을 때는 상호명 충돌), 계약번호가 서로 다르면 해당 주문은 원천 일관성 이상으로 진단하고 과거예산 매칭에서 제외합니다. 빈값과 정상값의 혼재 또는 같은 사업자번호의 상호 표기 차이는 허용합니다. 실제 납품요구 1건은 점수·기관정확도·공유신호·금액근접도·시차 순으로 가장 근거가 강한 예산사업 1건에만 귀속하여 여러 예산사업의 구매근거로 중복 집계하지 않습니다. 다만 `LED·가로등·보안등·등기구·도로·공원·청사` 같은 범용 조명/시설 문구와 모델·W 규격은 사업 식별 근거로 중복 가산하지 않으며, 면·동·도로명·고유 시설명 등 예산사업과 실제 납품요구가 함께 공유하는 고유 토큰이 없으면 점수가 높아도 HIGH가 아니라 최대 79점 CANDIDATE로 제한합니다. 행정구역 표기는 2026-10-04 공식 변경현황을 기준으로 이력 정규화합니다. 일대일 변경(예: 안양8동→명학동, 구지면→구지읍)은 강한 동일성으로 보되, 분동·분구(예: 운서동→운서1·2동, 인천 중구/서구→영종구·제물포구·서해구·검단구, 화성시→4개 일반구)는 시행일을 넘는 자료에서 해당 하위지역명이 양쪽에 함께 있을 때만 기관 전환 근거로 인정합니다. 전남광주통합특별시 같은 광역 통합은 현재 지역명으로 조회할 때 과거 광주광역시·전라남도 자료를 역사조회 범위에 포함합니다. 다만 기관패턴은 원 기관명을 보존하면서 현재 기관 계보로만 재그룹합니다. 정확히 통합된 광주광역시·전라남도 본청은 현재 전남광주통합특별시 본청 패턴으로 이어지지만, 목포시 등 하위기관은 원 기관명을 유지합니다. 인천 구 서구 자료도 청라권은 현재 서해구, 검단·아라권은 현재 검단구로 나누고 위치단서 없는 과거 서구 자료는 어느 후속 구에도 강제 합산하지 않습니다. 샘플·화면 조회의 limit/offset도 상세행이 아니라 납품요구번호를 먼저 페이지 선택한 뒤 선택된 주문의 대상 상세품목 전체를 다시 읽어 집계하므로 페이지 경계에서 한 주문이 반쪽으로 잘리지 않습니다. 요청선택 경로는 운영 PostgreSQL에서 `DISTINCT ON (delivery_req_no)`으로 요청별 최신 대표행을 직접 선택하고 `delivery_req_no + source_date DESC + updated_at DESC + source_key DESC` 부분 인덱스를 사용합니다. SQLite 호환경로만 기존 portable GROUP BY를 유지합니다. 선택된 주문 상세 재조회는 `delivery_req_no + is_active + primary_category + source_date` 복합 인덱스를 사용하며 웹 요청 중 DDL은 실행하지 않습니다. 요청 페이지 OFFSET은 연간 전체매칭 상한과 동일한 50,000을 초과하면 거부해 병적인 대형 OFFSET 쿼리를 막습니다. 검색어가 한 상세품목에만 일치하더라도 선택된 주문의 다른 LED·등주 상세품목까지 포함해 금액을 완성합니다. 같은 연도·지역 분석을 다시 실행하면 같은 run key의 기존 evidence를 교체해 오래된 후보가 누적되지 않습니다.
- 기관별 패턴은 해당 연도 원천수집 완료와 DB 전체 스캔 완료가 모두 확인된 경우에만 LED·등주 QWGJK 예산사업 population을 compact하게 저장해 `전체 과거 예산사업 수 / 높은 일치 사업 수 / 높은 일치율`을 계산하고, 높은 일치 evidence에서 실제 조달건, 높은일치 예산규모, 실제 조달금액, 평균 비음수 예산→조달 시차, 반복 조명/등주 신호를 요약합니다. 높은 일치율과 조달/예산 금액비는 직접 재원전환율이나 수주확률이 아니라 저장자료 기반 evidence 지표입니다. 화면 조회는 startup에서 준비된 테이블을 SELECT만 하며 DDL을 실행하지 않습니다.
- 미래 AIDFA/QWGJK 예산은 목표 회계연도 직전의 저장된 최근 2개 과거연도를 자동 선택해 `과거구매근거 점수`를 계산합니다. 연도는 코드에 `2025·2026`으로 고정하지 않으며, 예를 들어 2027 목표는 2025·2026, 2028 목표는 저장자료가 있으면 2026·2027로 rollover합니다. 전체 예산사업 population이 저장된 연도와 evidence만 일부 있는 연도를 별도로 구분하고, 다년 신뢰도 +10점은 전체 population 검증연도가 2개 이상일 때만 부여합니다. 부분연도 evidence는 참고에는 사용하되 `MULTI_YEAR` 보너스를 올리지 않습니다. 사용할 과거연도가 하나도 없으면 전체기간 fallback 없이 NO_HISTORY로 종료합니다. 미래 원천이 행정개편 후에도 구기관명을 보내는 경우에는 관찰일보다 목표 회계연도를 기준으로 현재 기관 lineage를 먼저 해석합니다. 예를 들어 2027년 `인천 서구 아라1동`은 현재 검단구 패턴, `청라1동`은 서해구 패턴을 사용하고, 위치단서 없는 폐지 서구나 화성시처럼 후속기관을 특정할 수 없는 구조예산은 fail-closed로 과거점수를 적용하지 않습니다. 광주광역시·전라남도 본청의 구명칭은 미래예산에서 현재 전남광주통합특별시 본청 패턴으로 연결합니다. lineage 적용 여부와 원 기관명, 전체검증연도·부분 evidence 연도는 화면 근거에도 표시합니다. 원천수집과 전체 DB 스캔이 완료된 population에서 높은 일치율도 evidence 강도에 보수적으로 반영합니다. 이 값은 수주확률이 아니라 과거 동일기관의 실제 LED·등주 구매 evidence 강도입니다. AIDFA는 구조예산이므로 점수를 최대 75로 제한하고, 실제 QWGJK 세부사업만 더 높은 근거강도를 가질 수 있습니다.
### 지방교육재정알리미
정규화/분석 구조와 API 키 저장 구조는 준비되어 있으나 live transport는
명시적으로 HOLD입니다.

## 보안

- 서버 저장형 임의 session token
- HttpOnly / SameSite=Lax / 운영 HTTPS Secure cookie
- 로그인 실패 반복 제한
- 상태변경 POST CSRF 검증
- CSP / X-Frame-Options / nosniff / no-store
- 원천 API 오류에서 credential/요청 URL 원문 노출 금지
- source request context 없이 외부 source I/O 차단
- regression test에서 실제 source network 연결 차단
- regression 환경에서 G2B/LOFIN/EDUINFO credential 모두 강제 공백

## Cafe24 4.1 배포 환경계약

최종 배포 순서와 판정 기준은 `DEPLOYMENT_V41_RUNBOOK.md`를 기준으로 합니다.

필수 운영값:

- `G2B_TEST_MODE=0`
- `G2B_AUTO_SYNC=0` — 기본값. 자동수집은 명시적으로 `1`일 때만 시작
- `G2B_AUTO_SYNC_DISABLE=1` — 비상정지 kill-switch. `G2B_AUTO_SYNC=1`보다 항상 우선
- `G2B_MATCH_ROLLOVER_AUTO_ENABLE=0` — 파생 예산↔조달 match refresh 자동실행 HOLD. 관리자 수동 실행은 메모리 guard를 통과할 때만 실행
- `G2B_RUNTIME_ROLE=UNIFIED`
- PostgreSQL 연결원천 하나: Cafe24 `DB_*` 자동변수 또는 `G2B_DATABASE_URL`
- `G2B_APP_SCHEMA=g2b_app`
- `G2B_BUDGET_SCHEMA=g2b_budget`
- `G2B_V41_FRESH_START=0` — 현재 4.1.x 운영자료/checkpoint 보존
- `G2B_BUILD_COMMIT=<배포 Git SHA>` — 플랫폼이 `GITHUB_SHA`를 제공하지 않을 때만 사용하는 fallback. 둘 다 있으면 `GITHUB_SHA`가 항상 우선

기존 완료 marker `g2b_meta.release_bootstrap=NORMALIZED_NO_RAW_V1`은 유지하며,
일반 재배포에서는 fresh-start를 다시 실행하지 않습니다. `G2B_V41_FRESH_START=1`은
과거 4.0 자료를 의도적으로 폐기하는 1회성 전환에만 사용합니다. PostgreSQL shopping/budget checkpoint는 프로세스 재기동 뒤에도 유지되며, 미완료 page는 저장된 generation의 다음 page부터 resume합니다.

원천 키는 환경변수 또는 관리자 `/settings`에서 설정합니다.

- `G2B_SERVICE_KEY` — 쇼핑몰 납품요구
- `LOFIN_API_KEY` — QWGJK 현재예산·과거이력 + AIDFA 현재연도 기초편성예산 + 다음연도 미래 편성예산
- `EDUINFO_API_KEY` — 저장 가능하지만 live transport는 HOLD

공유 PostgreSQL 권장값:

- `G2B_DB_POOL_SIZE=1`
- `G2B_DB_MAX_OVERFLOW=1`
- `G2B_DB_POOL_TIMEOUT_SECONDS=5`
- `G2B_DB_POOL_RECYCLE_SECONDS=900`
- `G2B_DB_CONNECT_TIMEOUT_SECONDS=3`
- `G2B_DB_LOCK_TIMEOUT_MS=5000`
- `G2B_DB_STATEMENT_TIMEOUT_MS=120000`
- `G2B_BUDGET_RETENTION_BATCH_SIZE=5000`
- `G2B_BUDGET_RETENTION_DAYS=365` — QWGJK 과거 snapshot/revision은 실제 source snapshot 날짜 기준 365일 유지
- `G2B_BUDGET_RECEIPT_RETENTION_DAYS=3` — 최신/current resume receipt의 상한. COMPLETE된 과거 QWGJK history scope의 page/item receipt는 즉시 compact

자동수집 기본값:

- Cafe24 `UNIFIED`도 기본은 자동수집 OFF입니다. `/live`, `/health`, `/ready` 확인 후 `G2B_AUTO_SYNC=1`을 명시했을 때만 반복 worker가 시작됩니다.
- API 키가 없으면 `WAITING_KEYS`, 일일 호출한도에 도달하면 `WAITING_QUOTA`로 대기하며 추가 호출 없이 checkpoint를 보존합니다. quota 대기만 남으면 다음 KST 날짜 경계 직후 자동 재개합니다.
- `RESULT_SERVER`와 `G2B_TEST_MODE=1`은 항상 source-I/O 금지이며, `UNIFIED`와 `LOCAL_COLLECTOR` 모두 `G2B_AUTO_SYNC=1` 명시 시에만 반복수집합니다.
- LED 조명/등주 조달내역 화면은 저장된 `source_date` 인덱스로 시작일·종료일을 함께 검색하며, 기본 조회기간은 KST 기준 해당 연도 1월 1일~12월 31일입니다. 2026년 이전 원천범위는 조회하지 않습니다. 웹 조회 경로는 DDL/인덱스 생성을 수행하지 않고 앱 기동 시 준비된 스키마를 SELECT만 합니다.
- `G2B_SHOPPING_SYNC_INTERVAL_SECONDS=7200`
- `G2B_SHOPPING_SYNC_DAYS_PER_RUN=62` — 2026-01-01부터의 백로그를 한 cycle에 최대 62개 미완료 날짜씩 순차 처리. 완료일은 건너뛰며, 한 날짜는 내부적으로 최대 40페이지·재시도 포함 64요청까지만 허용해 한 날짜가 900회 전체를 독점하지 못하게 함
- `G2B_SHOPPING_RECHECK_DAYS=7` — baseline이 D-1까지 모두 COMPLETE된 뒤에만 최근 최대 7일을 하루 1회 재확인해 늦게 반영된 변경차수·추가 납품요구를 보강. 같은 KST 날짜에 이미 재확인한 source 날짜와 이번 run에서 새로 수집한 날짜는 다시 호출하지 않음
- `G2B_SHOPPING_LONGTAIL_RECHECK_DAYS_PER_RUN=2` — 최근 7일보다 오래된 COMPLETE 날짜를 KST 하루 최대 2일씩 오래된 순서로 순환 재검증. baseline catch-up이나 당일 신규수집이 있었던 run에서는 실행하지 않고, 같은 KST 날짜에는 한 번만 실행해 quota를 보호
- `G2B_SHOPPING_RETENTION_MONTHS=27` — 조명·등주 납품요구 사업자료는 KST 달력 기준 최근 27개월(2년 3개월) 유지. 월말은 대상월 말일로 보정하며, 기존 `G2B_SHOPPING_RETENTION_DAYS`는 호환 인자이고 운영에서는 27개월 값이 우선.
- `G2B_SHOPPING_RETENTION_BATCH_SIZE=2000` — 만료된 normalized shopping row를 한 transaction에서 전량 삭제하지 않고 기본 2,000건씩 짧은 transaction으로 정리. 환경값은 100~10,000 사이로 제한하며 오래된 receipt/checkpoint도 source-day scope별 transaction으로 분리 삭제합니다. `shopping_records`와 27개월 경계 이전 shopping checkpoint/잔여 receipt를 함께 정리하고, baseline·recent·long-tail 수집 시작점도 같은 달력 27개월 retention floor로 이동해 삭제한 과거 날짜를 다시 API로 수집하지 않습니다. checkpoint가 이미 사라진 orphan page/item receipt도 one-day scope 날짜 기준으로 직접 제거하며, retired local compatibility collector도 62일 run 상한과 27개월 source window를 동일하게 적용합니다. LOCAL_COLLECTOR는 SQLite를 사용하더라도 shopping read/snapshot은 legacy RAW가 아니라 normalized `shopping_records`를 사용하며, source 호출을 생략한 snapshot-only cycle에서도 27개월 shopping retention을 먼저 실행합니다. local cycle이 설정하는 `G2B_RUNTIME_ROLE/G2B_TEST_MODE/DB URL/서비스키` 등 프로세스 환경은 cycle 종료(성공·실패 모두) 시 원래 값으로 복원합니다. normalized shopping 저장은 `YYYY-MM-DD` ISO source date와 2026-01-01 이후 날짜를 필수로 검증하며, 과거 버전이 남긴 빈값·malformed·pre-bootstrap source_date 행은 retention 실행 때 방어적으로 제거
- shopping `page_size` 내부 상한은 999로 유지. 현재 공개 포털에서 이 서비스의 명시적 최대 `numOfRows` 값은 확인되지 않아 근거 없이 더 크게 요청하지 않음
- `G2B_BUDGET_SYNC_MAX_PAGES=256`
- `G2B_BUDGET_SYNC_MAX_REQUESTS=500`
- `G2B_BUDGET_HISTORY_DAYS_PER_RUN=31` — 한 운영 cycle에서 시도할 과거 QWGJK 날짜 상한
- `G2B_BUDGET_HISTORY_RESERVE_REQUESTS=20` — 과거예산이 남아 있으면 미래예산 처리 후 남은 LOFIN 허용량의 최대 25%, 상한 20회를 history에 확보
- `G2B_FUTURE_BUDGET_SYNC_MAX_PAGES=24` — 다음년도 AIDFA 우선 수집의 1회 page 상한
- `G2B_CURRENT_APPROPRIATION_SYNC_MAX_PAGES=16` — 현재 회계연도 AIDFA 기초편성예산의 1회 page 상한
- `G2B_OPERATIONAL_LEASE_RETRY_SECONDS=15`
- `G2B_VNEXT_API_DAILY_LIMIT=900` — 나라장터 조명·등주 API 전용 로컬 일일 안전한도. 코드 상한도 900회이며 환경변수는 이보다 낮출 수만 있습니다. 각 실제 재시도도 1회로 차감하고 900회 도달 뒤에는 추가 네트워크 호출 전에 차단합니다. 제거된 contract/bid kind는 이 quota를 소비할 수 없습니다. quota만 남은 blocker이면 자동 worker는 같은 날 반복호출하지 않고 다음 KST 날짜 경계 직후 기존 checkpoint에서 재개합니다.
- 예산 화면은 QWGJK 실제 세부사업·집행을 먼저 표시하고 AIDFA 기능별 구조예산은 별도 참고 표로 분리합니다. 단순 메뉴 진입은 전체 회계연도 분석을 실행하지 않고 연도·지역·자료유형 조건이 적용된 bounded current-state SELECT만 수행합니다. 영업후보·미래예산 분석과 QWGJK 변경이력은 각각 명시적 버튼을 눌렀을 때만 계산합니다. QWGJK 변경이력은 별도 날짜조회 표에서 시작일·종료일·지역·기관/사업명으로 검색하며, 저장된 normalized revision과 `dataset+source_date` 인덱스를 사용하므로 조회 자체는 외부 API를 호출하지 않습니다. AIDFA는 회계연도 기준 구조예산이므로 이 날짜이력 표에 섞지 않습니다. AIDFA는 세부사업이 아니므로 집행액·잔액을 0원으로 오해하지 않게 `해당 없음`으로 표시하고, 기관·분야·부문·회계가 정확히 일치하는 QWGJK 세부사업이 있으면 실제 사업명을 연결해 표시합니다.
- `LOFIN_VNEXT_API_DAILY_LIMIT=500` — 지방재정365 예산 API 전용 로컬 일일 안전한도. 코드 상한도 500회이며 G2B 900회 카운터와 별도 key/lock을 사용합니다. 자동 all-source cycle의 source 호출 순서는 나라장터 shopping backlog → 다음연도 AIDFA → 현재연도 AIDFA → 최신 QWGJK current → 2026-01-01+ QWGJK history로 고정합니다. shopping 계열 오류는 budget source 상태를 FAILED로 오염시키지 않으며, history가 남아 있으면 최신 QWGJK가 일일 허용량을 전부 소진하지 않도록 일부를 예약
- 배포 확인은 `/live`, `/health`, `/ready`의 `version`과 `build_commit`을 함께 확인합니다. 대시보드와 설정 화면도 동일한 배포 HEAD를 표시하며, SHA를 제공하지 않는 플랫폼에서는 `미확인`으로 표시해 잘못된 HEAD를 추정하지 않습니다.

배포 직후 source API를 호출하지 않는 인프라 검증:

```bash
python scripts/g2b_deployment_preflight.py
```

원천 키까지 준비됐는지 확인하는 최종 preflight:

preflight 결과는 `shopping_infrastructure_ready` / `budget_infrastructure_ready`와 `shopping_collection_ready` / `budget_collection_ready`를 각각 분리합니다. 예산 PostgreSQL readiness만 문제일 때 나라장터 shopping 준비상태를 false로 내리지 않으며, 둘 다 준비돼야 전체 `collection_ready=true`입니다. 웹 `/api/status`도 `shopping_storage_ready`, `shopping_operational_ready`, `budget_operational_ready`를 별도로 제공합니다. 예산 PostgreSQL readiness 문제만으로 나라장터 shopping readiness를 false로 내리지 않습니다. PostgreSQL 연결이 없으면 `CONFIGURE_POSTGRES_CONNECTION`을 요구하며, 이는 `G2B_DATABASE_URL` 또는 Cafe24 `DB_*`/`PG*`/플랫폼 URL 중 하나를 준비하라는 뜻입니다.

```bash
python scripts/g2b_deployment_preflight.py --require-keys
```

최초 live 원천 검증은 두 단계로 수행합니다.

1. bounded canary: production DB를 건드리지 않고 쇼핑 + QWGJK + 현재년도 AIDFA + 다음년도 AIDFA를 소량 검증. bounded canary 결과는 나라장터와 지방재정365를 독립 판정하여 한쪽 키/오류가 다른 쪽 진단을 중단시키지 않습니다.
2. deployment canary: 실제 PostgreSQL checkpoint에 현재 KST 날짜 QWGJK 1페이지를 기록해 resume 계약 검증

```bash
python scripts/g2b_bounded_canary.py --allow-live
python scripts/g2b_budget_deployment_canary.py --allow-live
```

bounded canary의 다음년도 AIDFA는 1페이지 read-only probe이며 source JSON/production DB에 저장하지 않습니다.
deployment canary가 `RUNNING`이면 다음 정상 수집이 같은 generation의 `page_no=2`부터 resume해야
합니다. 1페이지 안에서 원천이 끝난 경우는 `COMPLETE`가 정상입니다.

권장 배포 순서:

1. 일반 재배포는 기존 PostgreSQL/checkpoint를 그대로 유지하고 `G2B_AUTO_SYNC=0`, `G2B_POST_BOOT_MAINTENANCE_ENABLE=0`으로 웹/DB만 먼저 기동
2. `/__ai_space_health → /live → /health → /ready` 확인
3. source-free preflight와 `--require-keys` preflight 확인
4. 별도 production canary를 수동 실행해야 할 때만 잠시 `G2B_AUTO_SYNC_DISABLE=1`로 자동 worker를 정지
5. bounded source canary 및 production PostgreSQL QWGJK 1페이지 canary 실행
6. checkpoint/resume 확인 후에도 자동수집은 HOLD 유지
7. 운영 승인 후에만 `G2B_AUTO_SYNC=1`로 전환하며, RSS soft limit과 heavy-work 단일 실행 guard를 계속 적용

수동 수집은 관리자 화면에서 나라장터와 지방재정365를 각각 1회 실행할 수 있으며, 수동 실행상태도 API별로 독립 기록합니다. 자동 all-cycle은 global exclusive lease를 사용하고 수동 API cycle은 global shared + source exclusive lease를 사용해 서로의 원천호출이 겹치지 않게 합니다. UNIFIED 자동수집은 프로세스 안에서 worker thread 하나를 사용하고, Cafe24 rolling deploy에서
구/신 프로세스가 겹치더라도 PostgreSQL advisory lease
`g2b_v41_operational_cycle`로 실제 source I/O를 하나의 프로세스만 수행하게 합니다.

정상 기동 기준:

- `/__ai_space_health` → HTTP 200, 저장소/예산 probe 없음
- `/live` → HTTP 200, `process_alive=true`
- `/health` → HTTP 200; 진단 필드로 부분 장애 표시
- UNIFIED `/ready` → 단일 PostgreSQL의 app + budget storage contract가 모두 정상일 때 200
- 예산 영역만 장애 → `/__ai_space_health`·`/live`·`/health` 유지, `/ready`는 503이 될 수 있으나 나라장터 shopping 실행 경로는 공통 backend가 정상인 한 유지
- PostgreSQL 전체 장애/권한오류 → `/__ai_space_health`·`/live`·`/health` 유지, `/ready` 503
- 운영 `storage_backend=POSTGRESQL_UNIFIED`

최초 fresh-start에는 G2B schema를 drop/create할 수 있는 bootstrap/owner 권한이
필요합니다. 장기 운영 역할은 전체 데이터베이스 owner일 필요는 없지만, 현재 4.1
런타임은 app schema에서 idempotent table/index 설치 검사를 수행하므로
CONNECT와 workload schema USAGE, app schema CREATE, 필요한 table DML 권한이
있어야 합니다. 단순 CONNECT/USAGE/DML-only 역할은 현재 운영 권한 계약이 아닙니다.

## 검증

일반 push/PR 검증:

- Python 3.11
- 전체 Python compile
- 전체 pytest
- clean runtime HTTP smoke
- source network 차단 regression

실원천 검증 workflow:

- `g2b-vnext-canary`: manual dispatch on `main`
- `g2b-vnext-small-validation`: manual dispatch on `main`
- live bounded canary는 shopping/QWGJK/AIDFA acceptance가 불충분하거나 키가 없으면 exit nonzero로 실패 처리

일반 push만으로 source API를 호출하지 않습니다.

## 3.1.7 pre-live 전수 감사

3.1.6 main을 기준으로 운영 Python 52개 파일, workflow, runtime smoke, 주요 문서를
재검토했습니다. 이번 감사에서 다음을 보완했습니다.

- 과거 feature branch에 묶여 있던 manual canary/small-validation을 `main` 기준으로 수정
- bounded canary의 과거 `main_merge_hold=true` 메타데이터 제거
- 지방재정365 quota 환경값/저장 카운터 손상 시 fail-safe 기본값 적용
- regression에서 EDUINFO credential까지 명시적으로 제거
- credential 포함 SQLite 파일 owner-only 권한 보정
- 2.x 테스트판 설명과 현재 운영판 문서 불일치 정리

실제 source traffic과 전체수집은 이 코드 감사와 별개의 운영 검증 단계입니다.


## 3.1.8 물품 입찰공고 기능 분리

물품 입찰공고는 별도 NO1 프로그램에서 운영하므로 G2B vNext에서 중복 기능을 제거했습니다.

- 상단 `물품 입찰공고` 메뉴 제거
- `/goods`, `/api/goods` 제거
- goods bid collector 및 historical stage 제거
- bounded canary/small-validation의 goods endpoint 제거
- 수집 상태 모니터에서 물품공고 제거
- 예산 → 공고 후보 연결은 용역공고만 사용
- 4.1 fresh-start에서는 4.0 이전 G2B 데이터를 이관하지 않고 새 저장정책으로 시작


## 3.1.9 운영판 보강

- 상단/대시보드/설정 화면에서 현재 배포 버전을 바로 확인
- 대시보드/설정에서 Cafe24 영구 저장소 사용 여부 표시
- 운영 모드에서는 영구 저장소가 아니면 `/ready`를 503으로 유지하고 로그인/설정 화면도 차단하여 임시 DB 운영을 방지
- 최초 관리자 생성은 별도 설정코드 없이 유지하되 브라우저 CSRF nonce 검증 추가
- 로그인 실패 제한을 IP와 계정 양쪽에 적용해 전달 IP 변경으로 우회하기 어렵게 보강
- 운영 환경의 공개 health/bootstrap 오류는 상세 경로 대신 오류 종류만 노출
- Uvicorn Server 응답 헤더 비노출
- `FORWARDED_ALLOW_IPS` 환경변수로 프록시 신뢰 범위를 필요 시 제한 가능
