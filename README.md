# SINSUNG G2B vNext 3.1.7

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
- `/goods` — 물품 입찰공고 후분류 조회
- `/service` — 용역 공고 → 개찰 → 최종낙찰 → 계약
- `/vendors` — 저장된 납품요구·계약 기반 업체 분석
- `/budget` — QWGJK/AIDFA/교육 RAW 기반 예산·영업후보
- `/raw` — RAW 저장소
- `/settings` — API 키, 안전상태, 저장 RAW 재정리
- `/api/collection-status` — 인증된 수집상태 JSON
- `/health`, `/__ai_space_health`, `/live`, `/ready` — 배포 진단

수집 상태 화면은 5초마다 다시 읽으며 외부 API를 호출하지 않습니다. checkpoint가
`RUNNING`인데 5분 이상 갱신되지 않으면 실제 실행중으로 표시하지 않고
`갱신중단`으로 표시합니다.

## 저장소

기본 Cafe24 운영 DB는 `/app/user_data/g2b-vnext.sqlite3`입니다.

- 웹 시작 중 구형 DB/테이블을 자동 삭제하지 않습니다.
- DB 경로는 프로세스에서 한 번 결정하고 실행 중 임의 전환하지 않습니다.
- SQLite 기본 lock timeout은 3초입니다.
- WAL은 명시적으로 켜지 않는 한 OFF입니다.
- API 자격증명이 들어갈 수 있으므로 POSIX 환경에서 DB 파일 권한을 가능한 경우
  owner-only(`0600`)로 보정합니다.

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

현재 운영모드는 `VALIDATION_ONLY`입니다.

- production scheduler: 비활성
- bulk historical: HOLD
- `APPROVED_HISTORICAL` execution context: 비활성
- 교육 vNext live transport: HOLD
- bounded canary: 수동 실행, main 기준
- small-validation: 수동 실행, main 기준
- 로컬 RAW/checkpoint가 존재해도 전체 원천 완전수집으로 간주하지 않음
- QWGJK canary가 성공해도 AIDFA/교육 원천의 전체 완전성을 증명하지 않음

즉, 실제 대량 운영을 시작하기 전에는
`bounded canary → one-day small-validation → 결과 감사` 순서를 통과해야 합니다.

## 데이터 원천별 현재 상태

### 나라장터
물품공고, 용역공고, 용역 개찰, 최종낙찰, 계약, 쇼핑몰 납품요구 수집기가 존재합니다.
실원천 호출은 source execution context와 quota gate를 통과해야 합니다.

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

## 배포 상태 확인

정상 기동 기준:

- `/live` → 200, `process_alive=true`
- `/health` → 200, `runtime=G2B_VNEXT_CLEAN`
- `/ready` → backend 준비 완료 시 200
- DB 초기화가 실패해도 uvicorn 프로세스는 유지되고 `/health`에서
  `backend_ok=false`로 진단 가능

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
