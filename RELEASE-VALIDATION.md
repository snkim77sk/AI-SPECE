# v5.2.0 GPT Site integration validation —2026-11-15

- Final query host: existing owner-private GPT Site, native Site version37; source commit b135282caef0212c5fa06d5d13ed00f6be59684d. Cafe24 is excluded from active operation by the user.
- Existing menus, delivery/vendor/SmartLED/budget/history/observations/matching/exports preserved. Site tests682/682, TypeScript and production build passed. Windows sync tests16/16; root query regression78/78.
- Read-only real-source audit:406 pages,380534 source records;314109 deliveries (7781 target records),54945 QWGJK,11480 AIDFA. Every page normalized and committed in separate local SQLite; no provider calls, no export/storage errors. Node peak observed heap74.10MiB; this is local validation, not hosted Worker memory telemetry.
- Original169 files compared by size, mtime and SHA256: no changes.
- Hosted first transfer:8/8 attempted pages confirmed by receipt GET,7309 source rows and5049 normalized target/budget rows;20.428seconds, no errors, provider calls0. Same records read through existing delivery/budget APIs: one per dataset,29 identity/date/amount fields compared.
- Initial source inventory grew to407 pages while the existing collectors continued;399 pages remained after the first bounded transfer. These are pending imports, not completed nationwide coverage.
- Runtime: D:/G2B/gpt-site-sync-v5.2.0 with a dedicated Python venv and pinned tzdata2025.2. Current-user DPAPI service credential, exact GPT hostname, no redirects. Credentials and data are excluded from Git.
- Automatic task G2B-GPT-Site-Sync:15minutes and60seconds after this user's interactive login, IgnoreNew,90seconds,256MiB child cap. First natural scheduled run started2026-11-15 15:00:56KST and completed successfully (Task Scheduler result0):8 new pages,7992 source rows,5099 normalized rows,33.912seconds, no errors. Separate existing query APIs matched a newly scheduled record for each of the three datasets. Total16 pages15301 source rows reflected;391 pages pending at that observation.
- Code version5.2.0 and data revisions are distinct; each committed page advances the appropriate data epoch. Source timeouts/holds, source quotas, keys and ChatGPT15minute automation are untouched.

Previous release validation follows.

# 5.1.0 검증 기록 · 2026-11-15

## 코드 변경

`server/`에 기존 Windows 수집기와 장애 복구 시험을 보존했습니다.
`server/web_sync_client.py`, `web_sync_entry.py`, `register_web_sync.ps1`은 수집과 독립된 웹 전송 작업입니다.
`query_store.py`, `query_ui.py`, `main.py`는 인증된 페이지 수신, 조회 DB, 검색과 60초 갱신 확인을 담당합니다.
운영 원본·RAW·체크포인트·키를 GitHub에 포함하지 않았습니다.

## 로컬 회귀시험

| 시험 | 통과 |
|---|---:|
| 웹 / ASGI / 동기화 / 접근 설정 / Python 호환 | 78 |
| 나라장터 실행·재개·일정·오류 응답 | 43 |
| 지방재정 저장·분석·조회 | 23 |
| 독립 프로세스·키·장애 분리 | 3 |
| 일일 페이지 전송·8페이지 제한·확인 응답·읽기 잠금 | 10 |
| 원천 코어·한도·중단·페이지별 메모리 | 30 |
| 합계 | 187 |

원천 API를 차단한 합성/임시 DB 시험입니다. `compileall`과 `git diff --check`도 통과했습니다.
Windows CI와 Python 3.11 / 3.12 / 3.13 웹 CI는 PR에서 다시 실행합니다.
새 Windows CI 환경에서 과거 지방재정 패키지가 없는 경우를 확인해, 증빙 파일 부재를 명확한 런타임 불일치로 거부하도록 보완했습니다. 시험은 운영 D: 경로 대신 임시 경로만 사용합니다.

## 실제 저장자료의 격리 웹 검증

원천 DB는 읽기 전용으로 열고, 저장 영수증과 RAW 해시가 일치한 페이지를 별도 임시 웹 DB에 저장했습니다.
웹 DB의 모든 공개 필드를 원천 투영과 다시 대조하고 API 검색·HTML 출력·중복 재전송을 확인했습니다.

| 자료 | 페이지 | 저장 후 재확인한 행 |
|---|---:|---:|
| 나라장터 납품 | 327 | 303,052 |
| 지방재정 세부사업 예산·집행 | 55 | 54,945 |
| 지방재정 구조별 세출예산 | 12 | 11,480 |
| 합계 | 394 | 369,477 |

- 이번 웹 연동 검증의 실제 원천 API 호출: **0회**. 외부 운영 웹 업로드: **0회**.
- 원본 보존: **169개 파일의 크기·수정시각·SHA-256 동일**, 운영 원본 DB 동일.
- 나라장터 API·HTML·검색 묶음 0.892초, 업체 0.261초, 예산 상세 0.159초, 구조예산 0.045초. 로컬 단일 사용자 측정이며 운영 동시부하 결과는 아닙니다.
- 페이지 처리 중 관측한 Windows peak working set: 전송 54.16MiB, 행 대조 54.85MiB. 최종 조회 검증 프로세스 45.31MiB.
- 별도 수집기 메모리 시험: 15,984행/16페이지, 최대 private bytes 26.64MiB, 페이지 간 Python 잔류 메모리 범위 0.963MiB.
- 조회 DB(검색 인덱스 포함): 719,790,080바이트, 약 686MiB. 원천 자료와 범위가 늘면 저장공간도 증가합니다.
- 임시 시험 DB 정리는 실행 환경의 자동 승인 검토에서 차단되어 보류했습니다. 시험 DB는 로컬에 남고 GitHub에 포함되지 않습니다.

실제 자료 검증에서 발견한 읽기 시간 초과는 64행 단위 읽기 후 잠금 해제, 검색 전용 인덱스, 결과 50행만 가져오기로 수정했습니다.
시험 코드의 종료 시 연결 미종료 오류는 수정했고 남아 있던 시험 DB를 별도 읽기 전용 검사로 재검증했습니다.
원본 보호 잠금은 자연 예약 실행 중 해제될 때까지 기다렸으며 수집기를 중단하거나 잠금을 우회하지 않았습니다.

## 적용 상태와 운영 조건

이 검증은 GitHub 코드와 로컬 격리 시험의 결과입니다. 현재 운영 웹에서 확인한 버전은 **5.0.5**, `/live` HTTP200입니다.
main 병합 자체가 카페24 배포나 자동 웹 반영 완료를 의미하지 않습니다.

운영 웹은 5.1.0 배포, 지속 저장공간 확인, 웹 전용 동기화 토큰 연결, 조회 접근 설정이 필요합니다.
그다음 첫 실제 페이지 전송·검색 재조회 성공을 확인하고 별도 웹 동기화 예약을 등록합니다.
자연 예약 실행으로 새 자료가 반영되고 동일 자료가 웹에서 재조회되어야 웹 자동 갱신 운영 성공으로 기록합니다.
카페24 배포 기능과 운영 웹 동기화 설정은 이번 환경에서 연결되지 않아 실행하지 않았습니다.

나라장터와 지방재정의 기존 Windows 예약 작업·실행 폴더는 변경하지 않았습니다.
11월 12일 미결 14페이지와 기존 보류 범위는 보존합니다. 지방재정 세부사업 전체 수집도 아직 진행 중입니다.
공식 제공처 잔량은 별도 미확인이며 로컬 일일 사용량과 동일한 값으로 표시하지 않습니다.
관리자 로그인·회원가입은 사용자가 지정한 후속 단계로 남깁니다.
