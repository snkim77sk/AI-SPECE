# LIGHTING SKETCH G2B DATA VIEW

나라장터 종합쇼핑몰·입찰 데이터를 수집/분석하는 라이팅스케치 웹 대시보드입니다.

## 현재 버전

- v2.3.1 AI SPACE FIXED
- Cafe24 AI SPACE용 FastAPI 진입점: `main.py`
- 실행: `uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}`

## 필수 환경변수

실제 값은 GitHub에 커밋하지 말고 Cafe24 AI SPACE 환경변수/Secret 설정에 등록합니다.

- `DASHBOARD_USER`
- `DASHBOARD_PASSWORD` (10자 이상)
- `DASHBOARD_SECRET` (32자 이상)
- `G2B_SERVICE_KEY`
- `G2B_SEED_SAMPLE=0`
- `G2B_API_DAILY_LIMIT=900`
- `G2B_ALLOW_API_URL_EDIT=0`

권장 자동수집 설정:

- `G2B_AUTO_SYNC=1`
- `G2B_AUTO_SYNC_HOURS=3`
- `G2B_AUTO_SYNC_DAYS=14`

## 상태 확인

- `/health`
- `/__ai_space_health`

최초 배포 시 보안 환경변수가 아직 없어도 상태 URL과 설정 안내 페이지는 HTTP 200을 반환합니다.

## GitHub Actions

`.github/workflows/ci.yml`에서 다음을 자동 수행합니다.

1. Python 의존성 설치
2. Python 문법 컴파일 검사
3. FastAPI 기동 및 health check
4. Cafe24 AI SPACE 업로드용 ZIP 패키지 생성

## 주의

`.env`, SQLite DB, 서비스키, 로그인 비밀번호/시크릿은 저장소에 올리지 않습니다.
