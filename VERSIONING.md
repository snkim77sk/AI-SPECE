- 4.1.228: 실운영 반복 502 대응을 위해 4.1.222 최종 전체 코드 트리(c6b754f14e6f3f57a8c909f64703f6c088de7481)로 완전 복구. 4.1.223~4.1.227 런타임 변경 제거. PostgreSQL 자료/revision/checkpoint/수집이력 보존, destructive reset 없음. VERSION만 롤백 추적을 위해 상향.
# G2B vNext 버전 관리

## 4.1 현재 기준

SINSUNG G2B vNext 4.1은 **단일 PostgreSQL + 예산 중심 + 쇼핑몰 조명·등주 영업** 운영판입니다.

1. Cafe24 기본 runtime role은 `UNIFIED`입니다.
2. 운영 저장소는 PostgreSQL 하나이며 `g2b_app`, `g2b_budget`, `g2b_meta` schema로 역할만 분리합니다.
3. 운영 SQLite 의존성은 제거했고 SQLite는 `G2B_TEST_MODE=1` 회귀테스트에서만 허용합니다.
4. 쇼핑몰은 2026-09-01 이후 전국 납품요구를 확인하되 조명·등주 대상만 정규화 저장합니다.
5. 예산 QWGJK는 최신 current state를 유지하면서 2026-01-01부터 D-1까지 과거 snapshot을 revision history로 순차 보강합니다. AIDFA는 다음연도 미래예산뿐 아니라 2026 현재연도 기초편성예산도 수집하며, 과거 QWGJK는 current state를 덮어쓰지 않고 모든 예산 원천은 원문 JSON을 영구 저장하지 않습니다.
6. 물품입찰·용역공고·개찰·낙찰·계약 collection은 G2B에서 제거하고 NO1로 분리합니다.
7. bulk historical과 `APPROVED_HISTORICAL`, 교육예산 live transport는 HOLD입니다.
8. 운영 source cycle은 프로세스 singleton + PostgreSQL advisory lease로 중복 실행을 차단합니다.
9. 현재 운영정책은 production `UNIFIED` 자동수집 기본 ON입니다. `G2B_AUTO_SYNC=0`은 legacy 호환값으로 UNIFIED 자동수집을 끄지 않으며, `G2B_AUTO_SYNC_DISABLE=1`만 명시적 긴급중지로 사용합니다.
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
- 4.1.228: 업체·수주 분석 표 왼쪽에 납품금액 기준 순위(1위·2위…) 추가. 현재 지역·업체 검색결과에서 순위 매김. PG 및 결과서버 스냅샷 동일, 빈 표 6열 보정 및 회귀테스트 추가. 기존 저장·수집·API·메모리 정책 불변.
- 4.1.221: 격리 source worker FAILED 시 2시간 지연 대신 120/240/480/960/1800초로 제한된 자동 재시도; 복구 성공시 failure streak 초기화, 예상치 못한 scheduler 오류에도 재시도. 작업자 중단 이후 PostgreSQL 체크포인트/수집 generation 보존·다음 페이지 재개 회귀 테스트 추가. 원본 DB/API/256MB 격리 정책 불변.
- 4.1.220: isolated source worker 오류 기록을 작업별 식별자로 구분. 후속 작업이 실패 원인을 쓰지 못해도 이전 오류를 재사용하지 않고 종료코드로 진단. 복구 성공시 화면 오류 제거, 작업 대기 중 과거 오류 조회 방지. DB/체크포인트/수집기/API 한도/256MB worker 정책 불변.
- 4.1.219: 수집 단계 및 최근 실행 내역에 같은 RUNNING 5분 갱신중단 판정 사용. 저장 체크포인트 RUNNING은 유지하고 표시용 status만 STALE로 변환; checkpoint_status 원본 보존. 쇼핑·예산 회귀테스트 추가. DB/수집기/API/격리 메모리 정책 불변.
- 4.1.218: 배포 전 전체 소스 감사. 예산 지역분할 RUNNING 체크포인트가 5분 이상 갱신되지 않은 경우 지역분할 요약도 STALE(갱신중단)으로 표시, 최근 RUNNING은 유지. 저장/체크포인트/수집기/API/메모리 불변.
- 4.1.217: 예산 분류 중 PostgreSQL advisory lease + 이중 연결로 1+1 풀을 초과하던 경로 차단. 예산/앱 호환 분류를 단일 연결·트랜잭션에 저장, 실 PostgreSQL CI 회귀 검증 추가. DB/수집기/API/checkpoint/256MB worker 정책 불변.
- 4.1.216: 예산 지역/기관 inline onchange를 CSP-safe /budget-filter.js로 교체. pageshow 시 서버 region/institution/department 값으로 form 재동기화해 모바일 state restoration 교차지역 회귀 차단. 서울도봉구/경기수원시 등 축약 기관명을 canonical 기관과 동일하게 검색·표시. 수집/API/checkpoint/schema 불변.
- 4.1.215: 로그인 후 첫 dashboard HTML에서 raw/target/readiness PostgreSQL 집계를 제거하고 즉시 shell 렌더. /dashboard-loader.js가 /api/dashboard-summary를 비동기 호출해 KPI를 후로딩. 집계 지연 시에도 화면·메뉴 즉시 사용 가능. startup lazy loader/API/checkpoint/schema 불변.
- 4.1.214: 지역 변경 후 이전 기관 필터가 남는 cross-region 회귀를 서버에서 강제 초기화. 서울 본청+25개 자치구, 경기도 본청+31개 시·군을 기본기관으로 제공하고 저장기관을 병합. 짧은/전체 기관명 exact 변형을 동일 필터로 처리. 수집/API/checkpoint/schema 불변.
- 4.1.213: QWGJK dept_name 누락 시 동일 기관+dept_code의 저장 current/revision evidence가 단일 실제 이름으로 일치하는 경우에만 source-free 보강. 이름 충돌 시 자동복구 금지. 이름 evidence가 없으면 UI/Excel에 부서명 미제공 + 부서코드 표시, Excel 담당부서코드 열 추가. 외부 API/checkpoint/schema 불변.
- 4.1.212: 예산사업 담당부서 중복 표시 제거. 빈 current QWGJK dept_name을 동일 dataset+record_key의 과거 normalized revision 중 최근 비어있지 않은 실제 값으로만 source-free bounded 복구(250×4/cycle). 다른 사업에서 추정/복사 금지, revision에도 없으면 미수집 유지. 자동 isolated budget worker에 연결하며 API quota/checkpoint/schema 불변.
- 4.1.211: 4.1.210 예산 UI 회귀 보완. 지역→기관→담당부서→빠른검색→직접검색→집행상태의 단순 구조로 정리하고 기관/부서에 전체 옵션 추가. 인천 본청·종합건설본부·IFEZ·상수도·도시철도·군구 curated 선택 복구, 타 지역은 저장 QWGJK 기관→담당부서 동적 select. 사업 상세 inline expansion, 담당부서 미수집 명시, QWGJK 부서 필드 alias 확대, Excel 동일 필터 적용. 수집/API/checkpoint/schema 불변.
- 4.1.210: 예산 read/UI를 전국 기관·부서 선택 + 조명/사업유형 빠른검색 + 사업 상세보기 + 현재 검색조건 XLSX 다운로드로 확장. 기관목록은 저장된 current PostgreSQL facts에서 동적으로 조회하고 keyword search는 분류와 독립해 OTHER 도로·공원·신축사업도 검색. 기존 3개 보조 분석 버튼은 UI에서 제거. source API/checkpoint/schema/256MB worker 불변.
- 4.1.209: 지방재정365 500회 일일 quota 또는 source-context request slice 경계를 resumable 상태로 분류. LOCAL_DAILY_QUOTA_REACHED / VNEXT_SOURCE_REQUEST_CONTEXT_BUDGET_EXHAUSTED checkpoint는 INCOMPLETE로 보존하고 잔여 quota 0이면 WAITING_QUOTA, 잔여 quota가 있으면 PARTIAL로 종료. 실제 unrelated RuntimeError는 기존처럼 실패 유지하며 heavy-worker 오류 코드 진단을 보강.
- 4.1.208: low-memory isolated budget worker가 저장된 exact-current 분류대기를 줄이는 사이클에서는 LOFIN source collector를 실행하지 않도록 pre-source drain gate 추가. 500×32 bounded pass에서 1건 이상 처리하거나 batch limit 도달 시 PARTIAL로 child 종료·메모리 해제 후 자동 재시도하며, backlog가 비워진 다음 사이클부터 원천수집 재개. 분류 소진 중 LOFIN source I/O 0 보장.
- 4.1.207: low-memory isolated budget worker의 exact-current classification 보강을 새 source page 수집 여부와 분리. PostgreSQL ready이면 LOFIN key/quota/new-page와 무관하게 budget/budget_appropriation/education_budget 누락·변경 분류를 500×32 bounded batch로 source-free 진행. 256MB 격리·source I/O 0 유지.
- 4.1.206: 예산 전체조건 요약에 exact-current 분류완료/분류대기 건수를 추가하고, 조명·등주 잔액후보 수/잔액이 분류완료 건 기준임을 명시. 전체 사업·예산·집행·잔액 COUNT/SUM 의미와 256MB bounded web/source-I/O 0 정책 유지.
- 4.1.205: collection monitor에 UNIFIED 자동수집 ON/OFF와 scheduler 상태를 명시하고 수동 버튼을 즉시 실행·점검용으로 설명. .env/runbook/versioning의 오래된 manual-only 문구를 4.1.192+ owner policy에 맞게 정리. runtime collection semantics는 변경하지 않음.
- 4.1.204: 4.1.203 전체조건 COUNT를 예산 목록 paging에 연결해 현재 페이지/전체 페이지/전체 건수를 표시하고 범위를 넘는 page 번호는 마지막 유효 페이지로 자동 보정. 200-row bounded 조회·source I/O 0 유지.
- 4.1.203: 예산 한눈에 보기의 페이지 기준 합계를 PostgreSQL 전체 필터조건 COUNT/SUM으로 교체. 총 사업수·총 예산·총 집행·총 잔액·미집행·부분집행·조명/등주 잔액후보 수/잔액을 source I/O 없이 집계하며 256MB web materialization 없음.
- 4.1.202: 수집상태 화면에 QWGJK 지역분할 진행 패널 추가. 완료 지역/전체 지역, 현재 지역명, 지역 진행률, 현재 지역 페이지와 기준일을 표시하고 최근 실행 scope를 전국 현재·지역분할·과거이력으로 가독화. source I/O 없음.
- 4.1.201: QWGJK 전국 overlap replay exhausted 시 stored 2026 current region_code를 이용한 fail-closed 지역분할 fallback 추가. 최소 17개 지역이 있어야 전환하고 worker당 미완료 지역 1개만 bounded 수집. 계획 완료 시 PARTITION_COMPLETE로 스케줄링 종료하되 source completeness는 false 유지.
- 4.1.200: QWGJK REPEATED_OR_OVERLAPPING_PAGE 발생 시 한 번만 page 1 fresh receipt generation으로 자동 재생. normalized data는 보존하고 abandoned receipts는 retention에 맡김. 재생 후 동일 중첩은 REPLAY_EXHAUSTED로 고정하여 source quota 무한소모 방지.
- 4.1.199: 256MB isolated budget worker를 scope당 최대 16페이지로 제한하고 PARTIAL 자동재개 주기를 단축. MemoryPressure checkpoint는 INCOMPLETE로 보존, child SIGKILL(-9)은 WAITING_MEMORY로 분류. isolated child의 full budget reorganization을 bounded incremental classification으로 대체.
- 4.1.198: 예산 화면에 영업우선 원클릭 보기를 추가. LIGHTING+POLE, remaining_amount>0을 PostgreSQL에서 LIMIT 이전 필터링하고 잔액 큰 순으로 표시하며 AIDFA 구조예산/전액집행은 제외. 페이지 이동에서 sales_priority 상태 유지.
- 4.1.197: 예산사업 목록에 storage-side 정렬을 추가하고 UI 기본값을 잔액 큰 순으로 변경. 잔액/예산/최근갱신/기관명 정렬을 제공하며 필터·페이지 이동에서 정렬값을 보존. bounded LIMIT/OFFSET 유지.
- 4.1.196: 예산 화면을 현재 사업명·예산·집행·잔액 중심의 요약/빠른필터/모바일 카드 구조로 개편. 기술적 수집 숫자와 AIDFA 구조예산은 접어서 보조정보로 이동하고, 분석 미실행 시 빈 미래예산·후보 표를 제거. 새 API 호출·대량 선적재 없음.
- 4.1.195: budget retention을 checkpoint별 짧은 transaction으로 분리하고 page/item receipt 삭제를 최대 500건 batch로 제한. retention 유지보수 실패는 source FAILED가 아닌 budget_maintenance_warning으로 분리해 LOFIN 수집상태를 보존.
- 4.1.194: 나라장터 정상 0건 응답에서 빈 PostgreSQL `executemany()` batch가 SQLSTATE 42P02를 만들던 경로를 no-op으로 수정. 수집상태 최근 실행 내역은 1024px 이하에서 카드형으로 전환하고 데스크톱 표 열 너비/상태 배지를 정리해 세로 글자 깨짐을 방지. 데이터·checkpoint·수집범위·API 한도는 변경하지 않음.
- 4.1.184: production runtime `get_setting/set_setting/source credential/settings_dict`의 반복 `init_db()` DDL 제거. shopping collection storage 준비 실패를 상태에 영속화하고 isolated worker exit 1에 실제 `shopping_recent_last_error`를 표시해 6월 13일 COMPLETE 뒤 worker 실패 원인을 추적 가능하게 함.
- 4.1.183: 256MB 수동 shopping/budget isolated worker를 1-child 순차 queue로 변경해 두 번째 source 요청 유실 제거. worker exit state를 parent UI에 반영해 RUNNING 고착 방지. production collection-monitor 5초 read path의 반복 schema/index DDL 제거로 DB lock·로딩 지연 완화.
- 4.1.182: RESULT_SERVER `/vendors`·`/api/vendors`의 지역 선택 시 PostgreSQL fallback 제거. local snapshot에 `vendors:<지역명>` 상위 1,000건 section 추가, RESULT_SERVER vendor read를 snapshot-only로 고정하고 API region 검증 추가.
- 4.1.181: production `vendor_rows()`의 `shopping_rows(limit=None)` 전체 materialization 제거. PostgreSQL server-side `stream_results` + `max_row_buffer=250`으로 active target rows를 250-row cursor batch streaming하며 품목별 최신 변경차수만 집계, 기존 vendor amount/request fallback/상호 병합/정렬 의미 유지.
- 4.1.180: RESULT_SERVER `/api/budget`의 targets/prebid 500건 선적재 후 Python 지역 재리스트화를 제거하고 4.1.179 bounded reader 재사용. 지역 선택 시 100-row paging, 최대 500건 결과 유지.
- 4.1.179: RESULT_SERVER `/budget` 분석의 targets/prebid 300건 선적재 후 Python 지역 재리스트화를 제거. 지역 선택 시 최대 100-row bounded paging으로 필터해 최대 300건만 누적, 기존 정렬/분류 의미 유지.
- 4.1.178: `/budget` 미래예산 분석에서 institution_scope를 DB read 단계로 전달하고, 최대 500개 후보의 evidence 결과는 bounded top-K 200건만 materialize. Python 기관 후필터/500건 enriched 전체 리스트/후속 200건 slice 중복 제거, 기존 evidence 정렬 의미 유지.
- 4.1.177: `/budget` 수동 과거 예산↔조달 매칭의 전 후보쌍 `candidate_rows` 누적 제거. 조달요청별 현재 최적 후보만 즉시 유지해 임시 메모리를 O(예산×조달 후보쌍)에서 O(조달요청 수)로 제한하고 기존 1요청→1예산 최적배정 의미 유지.
- 4.1.176: RESULT_SERVER `/shopping` 화면의 5,000-row 선적재 제거. 날짜/품목/검색 조건은 serving SQLite에 전달하고, 지역 필터는 최대 250-row bounded paging으로 정확한 limit 결과만 누적해 256MB 웹 프로세스의 순간 materialization을 제한.
- 4.1.175: RESULT_SERVER /api/shopping 5,000-row 선적재 제거. source_date 범위를 serving SQLite SQL WHERE에 적용하고 요청 limit을 DB 단계에서 강제, shopping date index 추가.
- 4.1.174: production .env.example 문법 오류 수정. destructive-reset 값 뒤 설명문 제거, memory soft-limit/isolated-worker literal \\n 제거, parseable env contract regression 추가.
- 4.1.173: RESULT_SERVER 구형 SQLite 경량화의 production PostgreSQL 오실행 차단. PostgreSQL에서는 SKIPPED_POSTGRESQL no-op으로 종료하고 sqlite_master/DROP/VACUUM/sqlite3.connect를 실행하지 않음. SQLite test/compatibility 동작만 유지.
- 4.1.172: RESULT_SERVER serving SQLite 기본경로 안전화. PostgreSQL logical locator를 os.path.dirname에 넣지 않고 production 기본값을 /app/user_data/g2b-serving.sqlite3로 고정. G2B_SERVING_DB_PATH override와 local/test sibling-path 동작 유지.
- 4.1.171: RESULT_SERVER result-sync를 웹 프로세스에서 분리. 인증된 업로드는 4MiB 임시파일로 spool하고 gzip 해제·JSON 파싱·snapshot SQLite import는 oom_score_adj=900 disposable worker에서 실행. oom.group=1/메모리압박/worker signal은 503 fail-closed. 4.1.170 size cap과 70초 liveness 유지.
- 4.1.170: RESULT_SERVER 대형 snapshot OOM 방어. result-sync request.stream() 4MiB hard cap, gzip JSON 12MiB cap, memory-pressure 503 fail-closed, import_snapshot 전체 payload 복제 제거, local collector gzip 재사용/사전 크기검사 적용. 4.1.169 70초 liveness gate 유지.
- 4.1.169: 실제 과거 장애의 45초 경계를 넘겨 70초까지 정상 기동을 검증하는 장기 liveness gate 추가. 5초 간격 /live, 45/70초 /health, OOM kill=0, RSS/peak RSS <160MiB, idle heavy-worker 미기동을 Python 3.12 + PostgreSQL에서 확인. 4.1.168 launcher failover와 기존 memory hardening 유지.
- 4.1.168: 과거 4.1.164 launcher failover를 현재 단순기동 구조에 최소 이식. Uvicorn/main:app 초기 기동 실패 시 DB/API 무접촉 stdlib recovery HTTP로 전환해 502를 방지하며 /ready는 503으로 유지. progressive boot는 복원하지 않음. 4.1.167 운영안전 + 4.1.166 bounded + memory hardening 유지.
- 4.1.167: 과거 4.1.156~160의 배포 식별/비파괴 안전기능을 현재 메모리 안전판에 복원. actual checkout SHA 우선순위, source fingerprint, process instance/uptime, deployment verdict, read-only snapshot availability, destructive reset 2중 확인을 적용. 4.1.166 bounded read/resume 및 4.1.141.5 memory hardening 유지.
- 4.1.166: 과거 4.1.165 계열의 저메모리 기능 중 4.1.150~152 개선을 현재 안전판에 선별 복원. /api/budget bounded storage read, shopping resume receipt page streaming, stale budget current 400-key reconciliation batch를 복원하며 4.1.141.5 heavy-worker 격리/cgroup checkpoint는 유지.
- 4.1.141.5: SINSUNG V7.1.24 방식의 heavy-worker 격리와 cooperative memory checkpoint를 G2B에 적용. <=320MiB UNIFIED 웹에서는 수동 shopping/budget/match를 disposable LOCAL_COLLECTOR worker로 분리하고 oom_score_adj=900, parent watchdog, 단일 file-lock, worker RSS 112MiB 기본선을 사용. shopping/budget page·classification batch·historical match scan 중간마다 cgroup/process 압력을 재검사하며 memory.oom.group=1이면 fail-closed. DB/revision/checkpoint/receipt 보존.
- 4.1.141.4: 실제 Cafe24 256MiB OOM 재장애 대응 응급 생존판. <=320MiB UNIFIED/RESULT_SERVER 웹 cgroup에서 heavy 작업을 fail-closed하여 자동/수동 수집 thread, match rollover, 동기식 대량 예산↔조달 매칭을 HOLD하고 HTTP/DB 생존을 우선. /health에 heavy-work 허용/low-memory web hold 상태를 분리 노출. 기존 PostgreSQL 데이터·revision·checkpoint·receipt 보존.
- 4.1.141.3: RSK V7.0.90의 cgroup-aware 메모리 guard 원리를 적용. 기존 process RSS 160MiB guard에 cgroup v1/v2 current/limit/stat/events 기반 adaptive wait/block(256MiB 기준 약 208/224MiB), OOM 진단, allocator/native-thread 기본 튜닝을 추가.
- 4.1.141.2: 예산 PostgreSQL 분류의 전체 current-hash dict / pending set / classification list 동시 적재 제거. 미분류·변경 key를 SQL LEFT JOIN + keyset pagination bounded batch로 처리해 데이터 증가 시 메모리 피크를 제한.
- 4.1.141.1: 4.1.141 기반 메모리 안전 패치. 자동수집·기동 후 분류복구를 명시적 opt-in으로 전환, RSS soft limit(기본 160MiB), heavy-work 단일 실행 lock, PostgreSQL pool 기본 1+1 적용. `/health`에 메모리 상태 노출.
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
