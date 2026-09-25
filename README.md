# SINSUNG G2B vNext 3.1.0

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

구형 `g2b.sqlite3`, 2.2 serving tables, scheduler state, legacy users/settings는 clean runtime에서 사용하지 않습니다.

## 최초 관리자
최초 접속 시 `/setup`에서 새 vNext 관리자 계정을 만듭니다.

보안을 위해 최초 관리자 생성에는 1회용 설정코드가 필요합니다.

- 권장: Cafe24 환경변수 `G2B_SETUP_TOKEN` 등록
- 미등록: 서버가 임의 코드를 생성하고 시작 로그에 `G2B_VNEXT_SETUP_TOKEN=...` 형식으로 출력
- 관리자 생성 후 설정코드는 무효화됩니다.

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
- 최초 관리자: 1회용 설정코드
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
