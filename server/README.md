# 현재 운영 경로 — GPT Site v5.2.0

Windows 수집기와 기존 GPT Site 조회 화면을 사용합니다. Cafe24 전송은 사용하지 않습니다.

- `gpt_sync_client.py`: 기존 영수증/RAW 해시를 읽기 전용 검증 후 허용된 공공 필드만 전송. 원천 API 호출0. 별도 receipt GET으로 확정하고 재실행 시 확인된 페이지 생략.
- `gpt_sync_entry.py`: Windows Job256MiB/전체90초, 회차 최대8페이지, 순차 처리와 원천별 오류 격리.
- `register_gpt_sync.ps1`: 현재 사용자가 직접 Windows 로그인한 뒤60초 및2분 주기. `G2B-GPT-Site-Sync`, 중복 실행 IgnoreNew. 기존 예약 작업 덮어쓰기 거부.
- 운영 설정 `D:\G2B\gpt-sync-config.json`: enabled, server_url(정확한 기존 GPT주소), token_file(DPAPI), state_directory, source_root. 토큰을 Git/문서/로그에 쓰지 않습니다.
- GPT Site 서비스 접근 권한은 현재 소유자 비공개 공개범위를 유지합니다. 향후 공개범위를 바꾸기 전에 수신 API의 별도 쓰기 권한을 먼저 구성해야 합니다.
- 동기화 영수증/로컬 ledger는 원천 수집 체크포인트와 별개입니다. 전송이 완료되지 않은 전체 범위나 원천 공식 잔여량은 추정하지 않습니다.

이하 내용은 기존 수집기 및 이전 전송 경로의 기록입니다.

# Windows 수집과 웹 자동 반영

## 실행 경로

`main.py`와 `run.py`는 조회 웹입니다. 이 폴더는 별도 Windows 서버용이며 웹 시작 코드가 import하지 않습니다.
검증된 수집기 원본을 재사용합니다. `reviewed-source-sha256.json`은 복사 전 검증본의 파일 해시입니다.
timezone 데이터만 `requirements.txt`의 tzdata로 설치하여 배포 패키지에 포함합니다.
과거 패키지 증빙이 없는 환경에서는 지방재정 실행 증빙을 불일치로 거부합니다. 이 예외 처리와 시험의 임시 출력 경로를 보완했습니다.
수집 코어의 `VERSION.txt`는 기존 데이터/수집기 계보를 나타내며 웹/저장소 버전은 루트의 `VERSION.txt`를 따릅니다.

기존 운영 경로는 나라장터 `D:\G2B\automatic-backfill-v5`, 지방재정 `D:\G2B\lofin-budget-v3`입니다.
GitHub 병합으로 실행 경로를 변경하거나 기존 폴더를 덮어쓰지 않습니다.
원본 `D:\G2B\program`, DB, RAW, 체크포인트, 기존 미결 요청을 보존합니다.

## 수집 정책

- 원천별 독립 프로세스. 원천 안에서는 순차 처리, 회당 8페이지 이하, 90초 이하, 작업자 256MiB.
- 나라장터 KST 하루 950회 이내, 지방재정365 자체 제한 100회. 공식 잔여량과 자체 장부를 구분합니다.
- KST 자정에 일일 장부 범위를 바꾸며 과거 불명 요청을 자동 해제하지 않습니다.
- 최신 완료일을 우선하고 2026-01-01까지 역순 진행합니다. 지방재정의 대량 기준일은 완성 후 다음 기준일로 진행합니다.
- 날짜/서비스 단위 오류만 격리합니다. 미전송 증거가 있는 요청은 체크포인트를 검증하여 재개합니다.
- 실제 전송 후 시간 초과/불명 요청은 자동 재호출하지 않습니다. 저장된 RUNNING만으로 프로세스를 활성으로 간주하지 않습니다.
- 사용자가 재부팅 후 로그인하면 기존 두 수집 작업이 각각 30초/45초 지연 후 재개합니다. 실제 정전·재부팅 실험은 별도입니다.

## 웹 자동 반영 준비

1. 전체 검증이 통과한 main을 기존 `sinsunglt2021-g2b` 앱에 배포합니다. 기존 DB/볼륨/설정을 보존합니다.
2. `/live`의 버전이 루트 `VERSION.txt`와 일치하고 `/api/sync-capabilities`가 `verified-source-pages-v2`를 지원하는지 확인합니다.
3. 기존 승인된 조회 접근 설정을 유지합니다. 공개 조회가 승인된 경우에만 해당 데이터의 `G2B_PUBLIC_READ=1`, `LOFIN_PUBLIC_READ=1`을 설정합니다. 회원/관리자 계정 기능은 아직 없습니다.
4. 웹의 `G2B_RESULT_SYNC_TOKEN`과 동일한 전송 전용 비밀을 서버 CurrentUser DPAPI로 저장합니다. 나라장터/지방재정 API 키를 전송 토큰으로 사용하지 않습니다. 키를 Git/명령 인수/로그/브라우저 조회 응답에 넣지 않습니다.
5. 검증된 `server/` 복사본을 별도 새 실행 폴더에 설치하고 전용 Python 환경에 `requirements.txt`를 설치합니다.
6. `D:\G2B\web-sync-config.json`에는 아래 비밀 없는 설정만 기록합니다. token_file 내용은 format=`windows-dpapi-current-user-v1`, ciphertext=DPAPI 암호문의 base64인 컨테이너입니다. 기존 CurrentUser DPAPI helper를 재사용합니다.

```json
{
  "enabled": true,
  "server_url": "https://sinsunglt2021-g2b.mycafe24.ai",
  "token_file": "D:/G2B/web-sync-credentials.dpapi.json",
  "state_directory": "D:/G2B/web-sync-state",
  "source_root": "D:/G2B"
}
```

설정·토큰은 이 저장소에 포함하지 않습니다. 설정이 없으면 전송은 시작되지 않습니다.

7. `python -B web_sync_entry.py`로 첫 전송을 실행하고 상태 파일의 확인 건수, 웹 검색 결과와 기준일을 대조합니다. 이 명령은 원천 API를 호출하지 않습니다.
8. 성공을 확인한 뒤 `register_web_sync.ps1 -Pythonw <전용 pythonw.exe> -RuntimeDirectory <검증된 새 폴더>`로 별도 작업을 등록합니다. 기존 작업이 있으면 덮어쓰지 않습니다.
9. 자연 예약 실행으로 새 자료가 추가되고 웹에서 같은 건을 다시 조회한 뒤 운영 완료로 기록합니다.

## 전송과 저장 원칙

수집 완료를 기다려 전체 DB를 보내는 대신 검증된 한 페이지씩 읽습니다.
원천 DB는 mode=ro와 query_only로 열고 한 페이지의 영수증·RAW 해시를 확인합니다.
전송 필드를 허용 목록으로 투영해 원천 자격증명·내부 메모·원본 경로는 보내지 않습니다.
HTTP 리디렉션을 거부하고 HTTPS 지정 원점에만 전송합니다. 인증 정보는 헤더에만 있습니다.
웹은 원천 API에 접속하지 않으며, 고정된 새 `g2b-query-v2.sqlite3`에만 저장합니다.
같은 페이지·행은 중복 저장하지 않고 오래된 전송과 다른 페이지의 키 겹침을 거부합니다.
실패하면 기존 웹 자료를 유지하며 다음 예약에서 재확인합니다. 웹의 멱등 전송 재개와 원천 API 재호출은 서로 다릅니다.

나라장터는 보존된 날짜들을 조회합니다. 예산은 가장 최근 반영된 기준일을 보여주고 이전 기준일도 DB에 보존합니다.
`서버 저장 / 웹 반영 / 제공처 전체`가 다르면 미완료로 표시합니다. 웹 갱신은 자료의 매일 전체 수집 완료를 뜻하지 않습니다.
금액은 제공처 원문 값입니다. 미확인 금액을 0으로 만들지 않고 단위 확인 전에는 통합 원화 예산을 확정하지 않습니다.

## 재현 시험

각 그룹은 모듈 이름이 같은 두 원천 코어가 섞이지 않도록 별도 프로세스로 실행합니다.

```text
python -X utf8 -B -m unittest -v test_live_collection_once test_backfill test_schedule test_restart_recovery test_schema_recheck
python -X utf8 -B -m unittest -v test_lofin_collection test_lofin_analysis test_lofin_viewer
python -X utf8 -B -m unittest -v test_web_sync
python -X utf8 -B -m unittest -v test_source_isolation
```

`g2b_recovery_candidate/`에서는 `python -X utf8 -B -m unittest -v test_recovery`로 원천 코어와 메모리 제한 시험을 실행합니다.

합성 시험은 원천 네트워크를 차단하고 임시 DB만 사용합니다. 실제 키·실제 원천 API 호출은 필요하지 않습니다.
원천 불명 요청, 전체 백필/예산 수집, 단위 확정, 실제 정전/재부팅과 운영 웹 반영은 각각 별도 상태로 보고합니다.
