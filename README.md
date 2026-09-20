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


## SEOA read-only bridge (Draft / HOLD)

선택 환경변수:
- AI_SPACE_SEOA_BRIDGE_SECRET: SEOA 서버간 읽기 전용 HMAC secret. 32자 이상, 공백 금지.
- 미설정 시 bridge는 503으로 비활성화됩니다.

이 secret은 관리자 비밀번호, DASHBOARD_SECRET, 나라장터/LOFIN API 키와 반드시 분리합니다.
Bridge는 저장 DB를 SQLite mode=ro로만 열며 외부 API 수집·스키마 생성·수정 작업을 수행하지 않습니다.
