# G2B Result View 5.0.2

새 **조회 전용** G2B 앱입니다. 이전 4.x 수집·분석·초기화 코드 대신 결과 수신/조회만 제공합니다. 이전 코드는 Git 이력에 남으며 운영 DB를 마이그레이션·삭제하지 않습니다. NO1은 별도 프로그램입니다.

## 운영 구조

`김수남 서버 D: 수집/원본 DB → 분류된 schema1 snapshot → HTTPS POST /api/result-sync → Cafe24 결과 조회`

- 나라장터·지방재정 API 호출, 자동 수집, 원천 키 저장, 관리자 초기화 기능이 없습니다.
- 브라우저는 결과 조회만 합니다. 동기화 성공과 수집 완료를 구분합니다.
- 현재 검증 자료는 2026-11-12의 13/14페이지(12,987/13,093)이며 **미완료**입니다. 14페이지 결과 불확실 HOLD를 해제하지 않습니다.
- 납품자료 검색/분류/페이지 탐색, 업체 조회, 상세정보, 모바일 대응 화면을 제공합니다.
- 초기 버전은 납품/업체 화면만 제공하며 예산·계약 분석·추천 기능은 제공하지 않습니다.

## 사용자 직접 Cafe24 배포

대상: `snkim77sk/AI-SPECE`, 기존 앱 `sinsunglt2021-g2b`. 새 프로젝트/상품/DB 구매 불필요 여부는 기존 계약에서 직접 확인하십시오. 이 저장소의 CI는 테스트만 하며 배포하지 않습니다.

1. GitHub에서 새 커밋의 **result-view-tests** 성공 확인. 기존 배포 커밋을 기록합니다.
2. 기존 Cafe24 AI SPACE 공식 앱 업데이트에서 이 저장소의 main을 선택합니다. 기존 PostgreSQL·다른 앱·원본 D: 자료를 삭제하지 마십시오.
3. Python 3.11~3.13, `requirements.txt` 또는 `pyproject.toml` 설치. 시작 명령 `python run.py` (`Procfile` 동일). 플랫폼 `PORT` 사용.
4. 지속 저장 디렉터리 `G2B_RESULT_DATA_DIR`를 설정합니다. 기본 `/app/user_data/g2b-result-view`이며 파일명은 `g2b-result-view.sqlite3`로 고정됩니다. 새 결과전용 저장소입니다. 기존 G2B 원본 SQLite/PostgreSQL DSN을 사용하지 않습니다.
5. **새 관리자·직원 비밀번호는 만들지 않습니다.** 기본 조회는 503 `READ_ACCESS_NOT_ENABLED`입니다. `G2B_PUBLIC_READ=1`은 인증 없는 공개조회이므로 운영자의 공개 범위 승인 또는 검증된 외부 접근제어 없이 켜지 마십시오. 기존 로그인 제거만으로 자동 공개되지 않습니다. Cafe24에서 기존 접근제어를 재사용할 수 있는지는 별도 확인 대상입니다.
6. 동기화만 `G2B_RESULT_SYNC_TOKEN` (32자 이상 임의 비밀)을 사용합니다. 기존 동기화 토큰을 안전한 서버 환경설정으로만 전달하십시오. 원천 API 키와 다르며 Git/브라우저/명령이력에 기록하지 마십시오. 미설정이면 수신503, 잘못된 토큰401입니다.
7. 배포 생존 점검은 `/live` 또는 `/__ai_space_health`를 사용하십시오. `/ready`는 조회 접근이 설정되고 첫 snapshot이 저장되어야200입니다. 초기 health probe로 `/ready`를 사용하면 안 됩니다.
8. 승인된 서버에서만 동기화하고 응답 snapshot_id/행수와 `/api/results`, 실제 브라우저 검색을 비교하십시오. 데이터 공개·토큰 설정·업로드·배포는 테스트 통과와 별도 행위입니다.

## 동기화 계약

- `POST /api/result-sync`, `Authorization: Bearer <service token>`
- `Content-Encoding: gzip`, `Content-Type: application/json`, 정확한 `Content-Length`
- schema_version 정수1, snapshot_id, timezone 포함 generated_at_utc, sections 필요.
- gzip 최대4MiB, 해제 JSON 최대12MiB, 총500,000행. 큰 자료는 오류로 거부하며 기존 결과는 유지합니다. 분할 업로드 프로토콜은 없습니다.
- 동일 내용 재전송은 idempotent. 같은 ID의 다른 내용 또는 기존보다 오래되거나 같은 시각의 다른 snapshot은 거부합니다.
- 저장 도중 실패는 트랜잭션 롤백. 승인된 최신 snapshot은 **이 앱의 파생 조회 결과만** 교체합니다. 원천/기존 운영 DB를 건드리지 않습니다.
- HTTPS는 Cafe24 인입에서 제공해야 합니다. 이 코드가 TLS·방화벽·공유 권한을 변경하지 않습니다.

## 검증 및 한계

개발 검증은 먼저 `python -m pip install -r requirements-dev.txt`로 시험 의존성을 설치합니다. 운영 설치는 `requirements.txt`만 사용합니다.

`python -m unittest -v` 및 `python -m compileall -q .`

합성/단위 테스트: 인증 실패, 기본조회 차단, XSS/SQL문자열, 잘못된 gzip/JSON/schema, 용량상한, 중복/과거 snapshot, 실패롤백, 기존 DB 보호, 네트워크 없는 조회, 미완료 표시.

2026-11-13 D: 복사본 실제자료 시험: 분류12,987건 → 대상784건/업체267개 → gzip102,994B → 새앱 수신·조회·HTML 생성 성공. 네트워크 호출0, 원본SHA256 전후동일. 이것은 실제 Cafe24 화면·인증·전송·메모리 한도 검증 완료를 뜻하지 않습니다.

운영 계약/과금, 배포 화면, 지속볼륨 복원, 실사용 브라우저, Cafe24 256MiB 최대부하 검증은 별도입니다. 기존 자료 백업으로 이 snapshot을 취급하지 마십시오.

## 롤백

Git 이전 커밋으로 코드만 되돌릴 수 있으나 이전4.x는 UNIFIED 자동수집을 시작할 수 있습니다. 롤백 전 원천수집 비활성 설정과 프로세스 상태를 운영자가 확인해야 합니다. 기존 데이터 삭제·자동 롤백은 제공하지 않습니다.

## 버전 관리

화면 상단·하단과 `/live`는 `VERSION.txt`의 동일 버전을 표시합니다. 조회 접근이 허용된 경우 `/release-notes`에서 변경이력을 볼 수 있습니다. 변경 시 [VERSIONING.md](VERSIONING.md)의 절차를 따르십시오. GitHub 코드 반영과 Cafe24 실제 배포 버전은 별개입니다.

## Cafe24 호환 진입점 (5.0.2)

공식 지원 스택 FastAPI로 앱 진입점을 명시했습니다. `main:app`은 ASGI FastAPI 인스턴스이며, `python run.py` 또는 `python main.py`로 실행합니다. 기본 `0.0.0.0:8000`, 플랫폼 PORT가 있으면 그 값을 사용합니다. 내부 WSGI 데이터/권한 로직은 그대로입니다. Swagger/OpenAPI 공개 경로는 없습니다.

이 변경은 순수 WSGI 자동분석 호환성 위험을 줄이지만, 제공된 ‘분석 단계 예상치 못한 오류’의 확정 원인이나 Cafe24 배포 성공을 증명하지 않습니다. 상세 플랫폼 로그 없이 반복 배포하지 마십시오. 공식 가이드: https://help.cafe24.com/faq/ai/ai-space/ai-space-usage-guide/
