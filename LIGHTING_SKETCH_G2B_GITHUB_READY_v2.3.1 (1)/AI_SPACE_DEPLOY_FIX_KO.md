# LIGHTING SKETCH G2B DATA VIEW v2.3.1 — AI SPACE 배포 수정판

## 왜 v2.3에서 배포중이 계속될 수 있었나

v2.3은 보안 환경변수(DASHBOARD_PASSWORD, DASHBOARD_SECRET)가 없는 최초 실행에서 루트 URL에 HTTP 503을 반환했습니다.
AI SPACE가 최초 배포 상태를 확인하는 과정에서 2xx 응답을 기대하면, 환경변수 설정 메뉴가 열리기 전부터 배포가 완료되지 않는 순환 문제가 생길 수 있습니다.

v2.3.1은 최초 배포 시 환경변수가 없어도 **설정 안내 페이지를 HTTP 200으로 응답**합니다. 배포가 완료된 뒤 AI SPACE 설정에서 비밀번호/시크릿/API 키를 넣고 재시작하면 실제 대시보드가 시작됩니다.

또한 AI SPACE의 Python/FastAPI 자동 감지를 쉽게 하기 위해 `main.py`를 표준 진입점으로 추가하고 `Procfile`/`start.sh`도 `main:app` 기준으로 통일했습니다.

## 권장 재배포 순서

1. 현재 v2.3 배포가 계속 '배포중'이면 해당 배포를 취소/중지할 수 있는 경우 중지합니다. 공간 자체를 삭제할 필요는 없습니다.
2. 동일 프로젝트에서 새 배포로 `LIGHTING_SKETCH_G2B_DATA_VIEW_v2.3.1_AI_SPACE_FIXED.zip`을 업로드합니다.
3. 이번에는 보안 환경변수를 아직 넣지 않았어도 배포 자체가 완료되어야 합니다.
4. 사이트를 열면 '환경변수 설정이 필요합니다'라는 LIGHTING SKETCH 안내 페이지가 뜨는 것이 정상입니다.
5. AI SPACE 설정 메뉴에서 아래 값을 등록합니다.
   - `DASHBOARD_USER` (예: admin)
   - `DASHBOARD_PASSWORD` (10자 이상)
   - `DASHBOARD_SECRET` (32자 이상 임의 문자열)
   - `G2B_SERVICE_KEY` (공공데이터포털 기존 인증키)
   - `G2B_SEED_SAMPLE=0`
   - `G2B_API_DAILY_LIMIT=900`
   - `G2B_ALLOW_API_URL_EDIT=0`
6. 저장 후 앱을 재시작/재배포합니다.
7. 사이트에서 로그인 화면이 뜨면 성공입니다.

## 1차 확인 주소

- `/health` : 앱 런타임 확인용. HTTP 200이 정상입니다.
- `/__ai_space_health` : AI SPACE 배포 진단용. `platform_ok: true`가 정상입니다.

## 실데이터 수집 순서

로그인 후 설정에서 API 연결 테스트 → 최근 1일 → 14일 → 3년 순서로 진행하세요.
