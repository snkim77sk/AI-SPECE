# 버전 및 사용자 직접 배포 절차

## 다음 변경부터
1. 변경 전 main HEAD를 확인하고 그 커밋부터 작업한다. 기존 이력 삭제/force push 금지.
2. VERSION.txt를 MAJOR.MINOR.PATCH 형식으로 증가한다. 오류수정은 PATCH, 호환 기능추가는 MINOR, 호환 불가 변경은 MAJOR.
3. pyproject.toml의 버전을 일치시키고 CHANGELOG.md 상단에 버전·날짜·수정·검증 범위·제한을 기록한다.
4. python -m unittest -v 및 python -m compileall -q main.py run.py test_viewer.py 실행. CI 성공 확인.
5. 승인된 범위만 main에 반영하고 커밋 SHA를 기록한다. 변경이력 날짜는 코드 반영일이지 배포 완료일이 아니다.

## Cafe24 사용자 직접 배포 확인
1. 배포 전 기존 배포 커밋/버전과 새 main 커밋 SHA 및 VERSION.txt를 기록한다.
2. 기존 앱에서 해당 커밋을 배포한다. 데이터 초기화나 접근범위 변경은 이 절차에 포함하지 않는다.
3. /live JSON의 version이 배포 대상 VERSION.txt와 일치하고 runtime_role=RESULT_VIEW_ONLY, source_collection_enabled=false인지 확인한다. 캐시 새로고침을 수행한다.
4. 기존 승인된 비공개 접근 하에서 화면 상단·하단 버전과 변경이력을 확인한다. 접근 설정이 없으면 루트는 자료 없는 준비안내200, 실제 조회 API는503이다. 준비안내200을 조회 허용으로 오인하거나 공개조회 설정을 임의로 켜지 않는다.
5. 실제자료 검색·분류·페이지 이동, snapshot 행수와 미완료 표시를 확인한다. /live 성공만으로 자료 동기화·정상 운영을 판단하지 않는다.
6. 보고에 코드 커밋, 기대 버전, 실제 /live 버전, 배포시각, 시험 결과, 남은 문제를 각각 기록한다. 확인하지 못한 항목은 미확인으로 남긴다.

코드 롤백은 이전 커밋 재배포로 한다. 원본 또는 조회 DB를 삭제하지 않는다. 4.x는 수집 기능이 있으므로 별도 안전 검토 없이 롤백하지 않는다.
