# SINSUNG G2B vNext 변경 요약

## 현재 기준

- 버전: 3.1.7
- 운영 진입점: `main.py -> vnext_clean_app.py`
- 운영 상태: `MAIN_ACTIVE`
- 실원천 모드: `VALIDATION_ONLY`
- production scheduler: OFF
- bulk historical: HOLD
- `APPROVED_HISTORICAL`: 비활성
- 교육청 live transport: HOLD

## 3.1.7 pre-live 전수 감사

1. 운영 Python 전체 파일과 workflow/문서 상태를 main 기준으로 재검토
2. manual bounded canary를 retired feature branch가 아닌 main에서 실행하도록 수정
3. manual small-validation을 main에서 실행하도록 수정
4. 일반 regression workflow의 retired feature branch push trigger 제거
5. bounded canary 결과의 과거 `main_merge_hold=true` 표기를 현재 운영상태에 맞게 수정
6. 지방재정365 일일 quota 환경값이 비정상이어도 안전 기본값으로 복구
7. 저장 quota count가 손상되어도 음수/비정상값을 사용하지 않도록 보완
8. 회귀 검증에서 G2B/LOFIN/EDUINFO 키를 모두 강제 공백 처리
9. API credential이 저장되는 SQLite 파일 권한을 POSIX에서 owner-only로 보정
10. README와 과거 2.x TEST 문서 불일치 제거

## 3.1.6

- 실제 RAW/checkpoint 기반 `/collection-monitor` 추가
- 자료별 실행중/완료/오류/중단/갱신중단 상태 표시
- 처리 페이지, 저장건수, RAW 건수, 진행률, 최근 갱신시각 표시
- `/api/collection-status` 추가
- read-only 모니터이므로 source I/O나 HOLD 상태를 변경하지 않음

## 3.1.5

- 지방교육재정알리미 API 키 관리자 입력/삭제 지원
- 교육 API 키 설정 여부와 live HOLD 상태를 분리 표시
- 교육 live transport 자체는 계속 차단

## 3.1.4

- 나라장터/지방재정365 키를 관리자 화면에서 입력 가능
- 전용 credential table 사용
- 환경변수 우선순위 유지
- 키 원문 웹/API 비노출

## 주의

이 문서는 현재 clean vNext 운영판 기준입니다. 과거 2.x TEST의
`admin / 1234`, 샘플데이터 자동생성, test-mode 전용 화면 등은 현재 운영판에
적용되지 않습니다.
