# AI-SPECE · LIGHTING SKETCH G2B DATA VIEW v2.3.2 TEST

## 역할
- ChatGPT: GitHub 소스 수정/검증
- Claude: Cafe24 AI SPACE 최신 GitHub 커밋 배포만 수행

## 테스트 로그인
- ID: `admin`
- Password: 환경변수 `DASHBOARD_PASSWORD`로 설정 (미설정 시 `1234`)

환경변수 입력이 불가능한 현재 테스트 단계에서는 `main.py`가 테스트 자격증명과 랜덤 세션 시크릿을 런타임에 준비합니다.

## 정상 상태
- `/health` → `configured: true`, `test_mode: true`
- `/__ai_space_health` → `platform_ok: true`, `configured: true`
- `/` → 로그인 화면으로 이동
- 로그인 후 샘플 데이터 대시보드 표시

## 실제 데이터 연결
로그인 후 설정 화면에서 공공데이터포털 서비스키를 입력할 수 있습니다. 자동수집은 테스트판에서 기본 OFF입니다.

## 실운영 전
테스트 설정을 제거하고 10자 이상의 비밀번호 및 32자 이상의 세션 시크릿으로 전환합니다.
