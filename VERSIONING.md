# G2B vNext 버전 관리

## 3.0 기준
SINSUNG G2B 3.0은 기존 2.x 런타임과 호환성을 유지하지 않는 clean vNext 기준판입니다.

1. 운영 진입점은 `main.py -> vnext_clean_app.py` 하나만 사용합니다.
2. 2.x 대시보드, scheduler, collector_v200, sinsung patch chain, serving tables는 사용하지 않습니다.
3. 기본 DB는 `g2b-vnext.sqlite3`입니다.
4. API 비밀키는 배포 환경변수 또는 로그인한 관리자 설정 화면에서 등록할 수 있습니다. 관리자 저장키는 일반 `app_settings`와 분리된 vNext 전용 credential table에 저장하며 화면/API에 원문을 다시 노출하지 않습니다. 환경변수가 있으면 환경변수가 우선합니다.
5. 수집 단계에서는 LED/조명/등주 키워드로 RAW를 버리지 않습니다.
6. RAW 원본과 revision을 먼저 보존한 뒤 정규화·후분류·분석합니다.
7. 전체 원천 완전수집은 실제 원천 검증 없이 선언하지 않습니다.
8. bounded canary와 small-validation을 통과하기 전 bulk historical을 열지 않습니다.
9. `APPROVED_HISTORICAL`은 별도 승인 전 비활성 상태를 유지합니다.
10. 교육예산 vNext live transport는 별도 검증 전 HOLD입니다.
11. 모든 배포 전 전체 pytest, compile, vNext runtime HTTP smoke를 통과해야 합니다.

## 버전 변경
- 3.1.7: pre-live 전수 감사. main 기준 manual validation workflow 활성화, canary metadata 정합화, LOFIN quota 파싱 보강, regression source credential 격리 강화, credential DB 파일 권한 보정, 운영 문서 동기화.
- 3.1.4: 관리자 설정 화면에서 나라장터/지방재정365 API 키를 전용 credential table에 등록·삭제 가능하게 변경. 환경변수 우선순위와 키 원문 비노출 유지.
- 3.1.3: 최초 관리자 생성에서 setup token 의존성을 제거하고 최초 접속자가 직접 관리자 계정을 생성하도록 단순화.
- 3.1.6: 실제 collection checkpoint와 RAW 건수를 이용한 수집 상태 모니터를 추가합니다. 단계별 실행중/완료/오류/갱신중단, 페이지·저장건수·최근시각을 5초 자동 새로고침으로 표시하며 외부 API 호출이나 HOLD 해제는 하지 않습니다.
- 3.1.5: 관리자 설정에서 지방교육재정알리미 API 키를 별도 등록·삭제할 수 있으며, 교육 예산 live transport는 bounded validation 전까지 HOLD를 유지합니다.
- 3.0.x: clean vNext 런타임 내부 수정 및 운영 안정화
- 3.1.x: 검증된 신규 source/분석 기능 추가
- 4.0.0: 데이터 계약 또는 운영 아키텍처의 호환 불가 변경

## 폐기된 계열
2.x 코드는 운영 저장소에서 제거되며 Git 이력만 과거 감사용으로 남습니다.
