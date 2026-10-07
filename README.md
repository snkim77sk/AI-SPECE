# SINSUNG G2B vNext 4.1.173

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
