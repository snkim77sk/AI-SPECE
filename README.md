# SINSUNG G2B vNext 4.1.0

## 운영 구조

운영 진입점은 `main.py -> vnext_clean_app.py` 하나입니다. 구형 2.x 대시보드,
scheduler, serving table 체계는 clean vNext 운영 경로에서 사용하지 않습니다.

핵심 데이터 흐름:

`전체수집 → RAW 원본·revision 보존 → 정규화 → 후분류 → 조달/영업 분석`

수집 단계에서는 LED/조명/등주 키워드로 원천 자료를 먼저 버리지 않습니다.

## 현재 운영 화면

- `/dashboard` — 전체 현황과 수집 준비상태
- `/collection-monitor` — 실제 RAW/checkpoint 기반 수집 진행상태
- `/shopping` — 쇼핑몰 납품요구 후분류 조회
- `/vendors` — 저장된 쇼핑몰 납품요구 기반 업체 분석
- `/budget` — QWGJK/AIDFA/교육 RAW 기반 예산·영업후보
- `/raw` — RAW 저장소
- `/settings` — API 키, 안전상태, 저장 RAW 재정리
- `/api/collection-status` — 인증된 수집상태 JSON
- `/health`, `/__ai_space_health`, `/live`, `/ready` — 배포 진단

수집 상태 화면은 5초마다 다시 읽으며 외부 API를 호출하지 않습니다. checkpoint가
`RUNNING`인데 5분 이상 갱신되지 않으면 실제 실행중으로 표시하지 않고
`갱신중단`으로 표시합니다.

## 저장소

4.1 운영은 **PostgreSQL 하나를 단일 source of truth**로 사용합니다. 운영 SQLite
의존성은 제거했습니다. 같은 PostgreSQL 안에서 workload별 schema만 분리합니다.

- `g2b_app` — 관리자/세션/설정/API 키, 쇼핑몰 RAW·revision, checkpoint/page receipt,
  분류와 경량 read model
- `g2b_budget` — QWGJK/AIDFA 예산 RAW/current/revision/checkpoint와 예산 projection
- `g2b_meta` — 4.1 fresh-start 같은 release bootstrap marker만 보관

운영 연결은 `G2B_DATABASE_URL` 하나가 기준입니다. control과 budget이 같은 SQLAlchemy
connection pool을 공유하므로 4.0의 SQLite + PostgreSQL 이중 저장소와 독립 pool 관리가
사라집니다.

첫 4.1 전환에서는 사용자가 승인한 대로 기존 G2B 4.0 데이터는 마이그레이션하지 않고
초기화한 뒤 공식 원천에서 다시 수집합니다. 삭제 범위는 G2B 소유 schema
(`g2b_app`, `g2b_budget`)와 구형 G2B SQLite 파일로 제한하며, PostgreSQL advisory lock과
durable marker로 재배포 중 중복 초기화를 막습니다.

- 운영에서 SQLite는 사용하지 않습니다.
- `G2B_TEST_MODE=1`인 회귀테스트에서만 SQLite fixture를 허용합니다.
- `/live`와 `/health`는 DB 장애가 있어도 플랫폼 502로 무너지지 않게 유지합니다.
- DB/schema/권한 계약이 정상일 때만 `/ready=200`입니다.
- 예산 RAW 기본 retention은 365일, 대량 page/item receipt 기본 retention은 3일입니다.

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

- shopping: 2026-10-01 이후 전국 원천을 날짜순으로 확인하되 조명·등주 범위만 저장
- budget QWGJK: 현재 회계연도 전체 RAW를 PostgreSQL에 저장 후 후분류
- 용역공고·개찰·낙찰·계약: G2B에서 제거, NO1 담당
- 물품 입찰공고: G2B에서 제거, NO1 담당
- bulk historical: HOLD
- `APPROVED_HISTORICAL` execution context: 비활성
- 교육 vNext live transport: HOLD
- bounded canary / small-validation: 수동 검증용이며 production PostgreSQL을 사용하지 않음
- 로컬 RAW/checkpoint가 존재해도 전체 원천 완전수집으로 간주하지 않음

일반 운영 수집과 별개로 배포 전 검증은
`bounded canary → one-day small-validation → 결과 감사` 순서로 수행합니다.

## 데이터 원천별 현재 상태

### 나라장터
4.1 운영 수집 범위는 쇼핑몰 납품요구입니다. 2026-10-01 이후 전국 원천을 확인하고
조명·가로등주 대상만 저장합니다. 용역공고·개찰·낙찰·계약과 물품 입찰공고는
NO1 담당으로 분리되어 G2B source allowlist에서도 차단됩니다.

### 지방재정365
- QWGJK: 세부사업/집행 snapshot 수집 구조
- AIDFA: 세출예산 appropriation 수집 구조

QWGJK bounded canary가 존재하지만 AIDFA whole-source completeness는 아직 검증 완료로
선언하지 않습니다.

### 지방교육재정알리미
RAW identity/정규화/분석 구조와 API 키 저장 구조는 준비되어 있으나 live transport는
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
- `G2B_DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/DBNAME`
- `G2B_APP_SCHEMA=g2b_app`
- `G2B_BUDGET_SCHEMA=g2b_budget`
- 첫 4.1 전환에서만 `G2B_V41_FRESH_START=1`

첫 fresh-start가 성공해 `g2b_meta.release_bootstrap`에 완료 marker가 기록되면
`G2B_V41_FRESH_START`는 삭제합니다. 이후 일반 재배포는 fresh-start를 다시 실행하지
않습니다.

원천 키는 환경변수 또는 관리자 `/settings`에서 설정합니다.

- `G2B_SERVICE_KEY` — 쇼핑몰 납품요구
- `LOFIN_API_KEY` — QWGJK 예산
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
- `G2B_BUDGET_RETENTION_DAYS=365`
- `G2B_BUDGET_RECEIPT_RETENTION_DAYS=3`

자동수집 기본값:

- `G2B_SHOPPING_SYNC_INTERVAL_SECONDS=7200`
- `G2B_SHOPPING_SYNC_DAYS_PER_RUN=31`
- `G2B_BUDGET_SYNC_MAX_PAGES=256`
- `G2B_BUDGET_SYNC_MAX_REQUESTS=320`
- `G2B_OPERATIONAL_LEASE_RETRY_SECONDS=15`

배포 직후 source API를 호출하지 않는 인프라 검증:

```bash
python scripts/g2b_deployment_preflight.py
```

원천 키까지 준비됐는지 확인하는 최종 preflight:

```bash
python scripts/g2b_deployment_preflight.py --require-keys
```

최초 live 예산 검증은 자동수집보다 먼저 현재 KST 날짜 QWGJK 1페이지 canary로 수행합니다.

```bash
python scripts/g2b_budget_deployment_canary.py --allow-live
```

canary가 `RUNNING`이면 다음 정상 수집이 같은 generation의 `page_no=2`부터 resume해야
합니다. 1페이지 안에서 원천이 끝난 경우는 `COMPLETE`가 정상입니다.

권장 배포 순서:

1. `G2B_AUTO_SYNC=0`, 최초 전환이면 `G2B_V41_FRESH_START=1`로 기동
2. `/live → /health → /ready` 확인
3. fresh-start marker 확인 후 `G2B_V41_FRESH_START` 삭제
4. source-free preflight
5. `--require-keys` preflight
6. QWGJK 1페이지 canary
7. checkpoint/resume 확인
8. 이상 없으면 `G2B_AUTO_SYNC=1`

자동수집은 프로세스 안에서 worker thread 하나를 사용하고, Cafe24 rolling deploy에서
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
- 기존 DB의 과거 `bid_notice_goods` RAW가 있더라도 자동 삭제하지 않음


## 3.1.9 운영판 보강

- 상단/대시보드/설정 화면에서 현재 배포 버전을 바로 확인
- 대시보드/설정에서 Cafe24 영구 저장소 사용 여부 표시
- 운영 모드에서는 영구 저장소가 아니면 `/ready`를 503으로 유지하고 로그인/설정 화면도 차단하여 임시 DB 운영을 방지
- 최초 관리자 생성은 별도 설정코드 없이 유지하되 브라우저 CSRF nonce 검증 추가
- 로그인 실패 제한을 IP와 계정 양쪽에 적용해 전달 IP 변경으로 우회하기 어렵게 보강
- 운영 환경의 공개 health/bootstrap 오류는 상세 경로 대신 오류 종류만 노출
- Uvicorn Server 응답 헤더 비노출
- `FORWARDED_ALLOW_IPS` 환경변수로 프록시 신뢰 범위를 필요 시 제한 가능
