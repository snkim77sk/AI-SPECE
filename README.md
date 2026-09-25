# SINSUNG G2B vNext 3.1.3

## 운영 구조
G2B 2.x 런타임은 제거되었습니다. 운영 진입점은 `main.py -> vnext_clean_app.py` 하나입니다.

핵심 데이터 흐름:

`전체수집 → RAW 원본보존 → 정규화 → 후분류 → 조달/영업 분석`

수집 단계에서는 LED/조명/등주 키워드로 원천 자료를 버리지 않습니다.

## 운영 화면
- `/dashboard` — vNext 전체 현황
- `/shopping` — 쇼핑몰 납품요구 후분류 조회
- `/goods` — 물품 입찰공고 후분류 조회
- `/service` — 용역 공고 → 개찰 1순위 → 최종낙찰 → 계약
- `/vendors` — 납품요구·용역계약 기반 업체 분석
- `/budget` — 신규 vNext 예산·영업후보
- `/raw` — RAW 저장소
- `/settings` — 원천 연결·안전상태·저장 RAW 재정리
- `/health`, `/__ai_space_health`, `/live`, `/ready` — 배포 진단

## 저장소
기본 운영 DB는 `/app/user_data/g2b-vnext.sqlite3`입니다.

구형 `g2b.sqlite3`, 2.2 serving tables, scheduler state, legacy users/settings는 clean runtime에서 사용하지 않습니다. 웹 기동 중에는 구형 DB 파일/테이블을 삭제하지 않으며, 필요 시 별도 유지보수 단계에서 정리합니다.

## 최초 관리자
최초 접속 시 `/setup`에서 새 vNext 관리자 계정을 바로 만듭니다.

- 관리자 계정이 0명일 때만 `/setup`이 열립니다.
- 아이디와 비밀번호만 입력하면 최초 관리자가 생성됩니다.
- 최초 관리자 생성 후 `/setup`은 자동으로 닫히고 로그인 화면으로 이동합니다.
- `G2B_SETUP_TOKEN`은 사용하지 않습니다.

## 원천 비밀키
- 나라장터: `G2B_SERVICE_KEY`
- 지방재정365: `LOFIN_API_KEY`
- 교육재정: `EDUINFO_API_KEY` (현재 live transport HOLD)

비밀키는 SQLite에 저장하지 않고 환경변수에서만 읽습니다.
이 키들은 웹 프로세스 기동 필수값이 아닙니다.

## 수집 안전경계
현재 운영모드는 `VALIDATION_ONLY`입니다.

- bounded canary 후 실원천 검증
- small-validation 후 범위 확대
- production scheduler: 비활성
- bulk historical: HOLD
- APPROVED_HISTORICAL context: 비활성
- 교육 vNext live transport: HOLD
- 로컬 RAW/체크포인트가 존재해도 전체 원천 완전수집으로 간주하지 않음

## 보안
- 세션: 서버 저장형 임의 토큰
- 쿠키: HttpOnly / SameSite=Lax / 운영 HTTPS Secure
- 최초 관리자: 관리자 0명일 때만 `/setup` 허용
- 상태변경 POST: CSRF 검증
- 로그인 실패 반복 제한
- CSP / X-Frame-Options / nosniff / no-store 적용
- `DASHBOARD_SECRET`은 사용하지 않음

## 배포 상태 확인
정상 기동 시:

- `/live` → 200, `process_alive=true`
- `/health` → 200, `runtime=G2B_VNEXT_CLEAN`
- `/ready` → DB 준비 완료 시 200
- DB 오류가 있어도 uvicorn 프로세스는 유지되고 `/health`에서 `backend_ok=false`로 확인 가능

## 개발/배포
- GitHub: 코드·회귀·배포 SHA 기준
- Cafe24: 검증 완료된 `main` SHA를 clean rebuild하여 배포


## 3.1.1 Cafe24 기동 안정화
- Procfile은 셸의 `PORT` 확장에 의존하지 않고 `python run.py`를 실행합니다.
- `run.py`가 Python에서 `PORT`를 직접 읽어 uvicorn을 실행합니다.
- `uvicorn[standard]` 확장 패키지를 제거하고 검증된 `fastapi==0.141.1`, `uvicorn==0.53.0`만 사용합니다.
- ASGI 모듈 import가 실패해도 bootstrap 앱이 HTTP 포트를 열고 `/live`, `/health`, `/ready`로 오류를 표시합니다.
- SQLite 초기화는 background thread에서 실행되며 FastAPI startup/lifespan 완료를 기다리게 하지 않습니다.
- `/live`, `/health`, 루트 경로는 DB 연결을 기다리지 않습니다.
- SQLite 기본 lock timeout은 3초이며 WAL은 기본 OFF입니다.
- `/app/user_data`가 아직 없을 때는 임시 DB로 웹 기동 자체를 보장하고, 영구 마운트가 보이면 연결 시점에 영구 DB 경로를 사용합니다.


## 3.1.2 조회 정확도 보완
- 쇼핑몰/물품 검색을 현재 표시건수 안에서 거르는 방식이 아니라 전체 저장 RAW 대상 SQL 검색 후 페이지 제한을 적용합니다.
- 용역 라이프사이클 검색도 limit 적용 전에 전체 대상에서 검색합니다.
- 동일 업체명이라도 사업자번호가 다르면 서로 다른 업체로 집계합니다.
- 사업자번호 없는 행은 동일 상호에 단 하나의 사업자번호만 확인될 때에만 안전하게 병합합니다.
- 업체/조달 요약 계산에서 5,000건 임의 절단을 제거합니다.


## 3.1.3 최초 관리자 생성 단순화
- 배포 직후 최초 접속자가 `/setup`에서 아이디·비밀번호를 직접 생성합니다.
- 설정코드와 `G2B_SETUP_TOKEN` 의존성을 제거했습니다.
- 관리자 계정이 하나라도 존재하면 `/setup`으로 추가 관리자를 만들 수 없습니다.
