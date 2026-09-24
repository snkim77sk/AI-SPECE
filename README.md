# SINSUNG G2B vNext 3.0.1

## 운영 구조
G2B 2.x 런타임은 제거되었습니다. 현재 운영 진입점은 `main.py -> vnext_clean_app.py` 하나입니다.

핵심 데이터 흐름:

`전체수집 → RAW 원본보존 → 정규화 → 후분류 → 조달/영업 분석`

## 현재 운영 화면
- `/dashboard` — vNext 전체 현황
- `/budget` — 예산·영업후보
- `/service` — 용역 공고→개찰→낙찰→계약
- `/raw` — RAW 저장소
- `/settings` — 원천 연결/안전상태
- `/health`, `/__ai_space_health` — 배포 상태

## 저장소
기본 운영 DB는 `/app/user_data/g2b-vnext.sqlite3`입니다.

구형 `g2b.sqlite3`, 2.2 serving tables, scheduler state, legacy users/settings는 clean runtime 시작 시 제거 대상입니다.

## 인증
최초 접속 시 `/setup`에서 새 vNext 관리자 계정을 생성합니다.
기존 2.2 계정은 사용하지 않습니다.

## 원천 비밀키
- 나라장터: `G2B_SERVICE_KEY`
- 지방재정365: `LOFIN_API_KEY`
- 교육재정: `EDUINFO_API_KEY` (현재 live transport HOLD)

비밀키는 SQLite에 저장하지 않고 환경변수에서만 읽습니다.

## 수집 안전경계
- bounded canary 후 실원천 검증
- small-validation 후 범위 확대
- bulk historical: HOLD
- APPROVED_HISTORICAL: 비활성
- 교육 vNext live transport: HOLD
- 로컬 RAW/체크포인트가 존재해도 전체 원천 완전수집으로 간주하지 않음

## 개발/배포 역할
- ChatGPT: GitHub 개발·검증
- Cafe24/배포 환경: 검증된 `main` SHA 배포
