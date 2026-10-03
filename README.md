# SINSUNG G2B vNext 4.1.44

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
- `/budget` — QWGJK/AIDFA/교육 정규화 예산 기반 영업후보
- `/raw` — 4.1에서 폐기된 호환 URL이며 `/collection-monitor`로 이동
- `/settings` — API 키, 안전상태, 4.1 저장정책 확인
- `/api/collection-status` — 인증된 수집상태 JSON
- `/health`, `/__ai_space_health`, `/live`, `/ready` — 배포 진단

수집 상태 화면은 5초마다 다시 읽으며 외부 API를 호출하지 않습니다. 수동 수집은 원천별로 분리해 `나라장터 조명·등주 수집`과 `지방재정365 예산 수집`을 각각 실행하며, 서로의 API 키·호출한도·checkpoint를 공유하지 않습니다. AIDFA 상태는 현재연도 기초편성과 다음연도 미래예산을 분리해서 표시합니다. 수동 source가 하나라도 실행 중이면 `/api/collection-status`의 aggregate 상태도 `RUNNING`으로 유지하며 `manual_sources_running`에 실행중 source 수를 표시합니다. QWGJK 카드에는 실제 rolling 시작일(2026년에는 2026-01-01)부터 D-1까지 예산이력 완료일수·전체일수·진행률·다음 수집일도 표시합니다. checkpoint가
`RUNNING`인데 5분 이상 갱신되지 않으면 실제 실행중으로 표시하지 않고
`갱신중단`으로 표시합니다.

## 저장소

4.1 운영은 **PostgreSQL 하나를 단일 source of truth**로 사용합니다. 운영 SQLite
의존성은 제거했습니다. 같은 PostgreSQL 안에서 workload별 schema만 분리합니다.

- `g2b_app` — CONTROL(관리자/세션/설정/API 키/checkpoint) + 2026-09-01 이후 정규화 사업자료 + READ 지원
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
- `/live`와 `/health`는 DB 장애가 있어도 플랫폼 502로 무너지지 않게 유지합니다.
- DB/schema/권한 계약이 정상일 때만 `/ready=200`입니다.
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

- shopping: 2026-09-01 이후 전국 원천을 날짜순으로 확인하되 조명·등주 범위만 저장
- AIDFA: 다음 회계연도 세출예산을 우선 갱신하고, 현재 회계연도(2026) 기초편성예산도 최대 16페이지/회차로 수집하고 같은 KST 날짜의 COMPLETE는 재사용하되 날짜가 바뀌면 다시 확인해 1월 1일 예산범위를 보완
- budget QWGJK: 현재 회계연도 최신 snapshot은 current state로 유지하고, 2026-01-01부터 시작해 이후 rolling 365일 범위의 과거 snapshot은 남는 LOFIN 호출량으로 순차 보강합니다. 과거분은 `history:연도:날짜` 전용 checkpoint와 revision history로만 저장해 현재 예산값을 과거값으로 되돌리지 않습니다
- 용역공고·개찰·낙찰·계약: G2B에서 제거, NO1 담당
- 물품 입찰공고: G2B에서 제거, NO1 담당
- bulk historical: HOLD
- `APPROVED_HISTORICAL` execution context: 비활성
- 교육 vNext live transport: HOLD
- bounded canary / small-validation: 수동 검증용이며 production PostgreSQL을 사용하지 않음
- checkpoint/저장건수가 있어도 전체 원천 완전수집으로 자동 간주하지 않음

일반 운영 수집과 별개로 배포 전 검증은
`bounded canary → one-day small-validation → 결과 감사` 순서로 수행합니다.

## 데이터 원천별 현재 상태

### 나라장터
4.1 운영 수집 범위는 쇼핑몰 납품요구입니다. 2026-09-01 이후 전국 원천을 확인하고
조명·가로등주 대상만 저장합니다. 용역공고·개찰·낙찰·계약과 물품 입찰공고는
NO1 담당으로 분리되어 G2B source allowlist에서도 차단됩니다.

### 지방재정365
- QWGJK: 2026-01-01부터 세부사업/집행 snapshot 이력 보강 + 최신 snapshot current state 유지
- AIDFA: 세출예산 appropriation 수집 구조

QWGJK bounded canary가 존재하지만 AIDFA whole-source completeness는 아직 검증 완료로
선언하지 않습니다.

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
- `G2B_AUTO_SYNC=0` — 최초 기동/검증 단계
- `G2B_RUNTIME_ROLE=UNIFIED`
- PostgreSQL 연결원천 하나: Cafe24 `DB_*` 자동변수 또는 `G2B_DATABASE_URL`
- `G2B_APP_SCHEMA=g2b_app`
- `G2B_BUDGET_SCHEMA=g2b_budget`
- 첫 4.1 전환에서만 `G2B_V41_FRESH_START=1`

첫 fresh-start가 성공해 `g2b_meta.release_bootstrap`에 완료 marker가 기록되면
`G2B_V41_FRESH_START`는 삭제합니다. 이후 일반 재배포는 fresh-start를 다시 실행하지
않습니다.

원천 키는 환경변수 또는 관리자 `/settings`에서 설정합니다.

- `G2B_SERVICE_KEY` — 쇼핑몰 납품요구
- `LOFIN_API_KEY` — QWGJK 현재예산·과거이력 + AIDFA 현재연도 기초편성예산 + 다음연도 미래 편성예산
- `EDUINFO_API_KEY` — 저장 가능하지만 live transport는 HOLD

공유 PostgreSQL 권장값:

- `G2B_DB_POOL_SIZE=5`
- `G2B_DB_MAX_OVERFLOW=2`
- `G2B_DB_POOL_TIMEOUT_SECONDS=5`
- `G2B_DB_POOL_RECYCLE_SECONDS=900`
- `G2B_DB_CONNECT_TIMEOUT_SECONDS=3`
- `G2B_DB_LOCK_TIMEOUT_MS=5000`
- `G2B_DB_STATEMENT_TIMEOUT_MS=120000`
- `G2B_BUDGET_RETENTION_BATCH_SIZE=5000`
- `G2B_BUDGET_RETENTION_DAYS=365` — QWGJK 과거 snapshot/revision은 실제 source snapshot 날짜 기준 365일 유지
- `G2B_BUDGET_RECEIPT_RETENTION_DAYS=3` — 최신/current resume receipt의 상한. COMPLETE된 과거 QWGJK history scope의 page/item receipt는 즉시 compact

자동수집 기본값:

- `G2B_SHOPPING_SYNC_INTERVAL_SECONDS=7200`
- `G2B_SHOPPING_SYNC_DAYS_PER_RUN=31`
- `G2B_BUDGET_SYNC_MAX_PAGES=256`
- `G2B_BUDGET_SYNC_MAX_REQUESTS=320`
- `G2B_BUDGET_HISTORY_DAYS_PER_RUN=31` — 한 운영 cycle에서 시도할 과거 QWGJK 날짜 상한
- `G2B_BUDGET_HISTORY_RESERVE_REQUESTS=20` — 과거예산이 남아 있으면 미래예산 처리 후 남은 LOFIN 허용량의 최대 25%, 상한 20회를 history에 확보
- `G2B_FUTURE_BUDGET_SYNC_MAX_PAGES=24` — 다음년도 AIDFA 우선 수집의 1회 page 상한
- `G2B_CURRENT_APPROPRIATION_SYNC_MAX_PAGES=16` — 현재 회계연도 AIDFA 기초편성예산의 1회 page 상한
- `G2B_OPERATIONAL_LEASE_RETRY_SECONDS=15`
- `G2B_VNEXT_API_DAILY_LIMIT=900` — 나라장터 조명·등주 API 전용 로컬 일일 안전한도. 지방재정365 quota와 완전히 독립
- `LOFIN_VNEXT_API_DAILY_LIMIT=100` — 지방재정365 예산 API 전용 로컬 일일 안전한도. 실제 cycle은 다음연도 AIDFA → 현재연도 AIDFA → 최신 QWGJK → 2026-01-01+ history 순으로 배정하며 history가 남아 있으면 최신 QWGJK가 일일 허용량을 전부 소진하지 않도록 일부를 예약

배포 직후 source API를 호출하지 않는 인프라 검증:

```bash
python scripts/g2b_deployment_preflight.py
```

원천 키까지 준비됐는지 확인하는 최종 preflight:

preflight 결과는 `shopping_collection_ready`와 `budget_collection_ready`를 별도로 표시하며, 둘 다 true일 때만 전체 `collection_ready=true`입니다. 웹 `/api/status`도 `shopping_operational_ready`와 `budget_operational_ready`를 별도로 제공합니다. PostgreSQL 연결이 없으면 `CONFIGURE_POSTGRES_CONNECTION`을 요구하며, 이는 `G2B_DATABASE_URL` 또는 Cafe24 `DB_*`/`PG*`/플랫폼 URL 중 하나를 준비하라는 뜻입니다.

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

1. `G2B_AUTO_SYNC=0`, 최초 전환이면 `G2B_V41_FRESH_START=1`로 기동
2. `/live → /health → /ready` 확인
3. fresh-start marker 확인 후 `G2B_V41_FRESH_START` 삭제
4. source-free preflight
5. `--require-keys` preflight
6. bounded source canary — 쇼핑 + QWGJK + 현재년도 AIDFA + 다음년도 AIDFA
7. production PostgreSQL QWGJK 1페이지 canary
8. checkpoint/resume 확인
9. 이상 없어도 현재 운영정책은 `G2B_AUTO_SYNC=0` 유지 · 자동수집 전환은 별도 승인 후 진행

수동 수집은 관리자 화면에서 나라장터와 지방재정365를 각각 1회 실행할 수 있으며, 수동 실행상태도 API별로 독립 기록합니다. 자동 all-cycle은 global exclusive lease를 사용하고 수동 API cycle은 global shared + source exclusive lease를 사용해 서로의 원천호출이 겹치지 않게 합니다. 자동수집을 별도 승인해 활성화하는 경우에만 프로세스 안에서 worker thread 하나를 사용하고, Cafe24 rolling deploy에서
구/신 프로세스가 겹치더라도 PostgreSQL advisory lease
`g2b_v41_operational_cycle`로 실제 source I/O를 하나의 프로세스만 수행하게 합니다.

정상 기동 기준:

- `/live` → HTTP 200, `process_alive=true`
- `/health` → HTTP 200
- UNIFIED `/ready` → 단일 PostgreSQL의 app + budget storage contract가 모두 정상일 때 200
- PostgreSQL 장애/권한오류 → `/live`와 `/health` 유지, `/ready`만 503
- 운영 `storage_backend=POSTGRESQL_UNIFIED`

최초 fresh-start에는 G2B schema를 drop/create할 수 있는 bootstrap/owner 권한이
필요합니다. schema와 table/index를 모두 준비한 이후 장기 운영 역할은 필요한
CONNECT/USAGE/DML 권한으로 축소할 수 있습니다.

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
