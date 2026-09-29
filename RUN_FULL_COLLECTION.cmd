@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_full_collection.ps1"
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo [G2B] 전체 수집 실행이 중단되었습니다.
  echo [G2B] 기존 DB는 삭제되지 않았습니다. 다시 실행하면 이어서 진행합니다.
  echo [G2B] 자세한 내용은 D:\G2B\logs 폴더를 확인하세요.
  pause
  exit /b %RC%
)

echo [G2B] 전체 수집 실행이 정상 종료되었습니다.
echo [G2B] D:\G2B\logs 와 snapshot 폴더에서 결과를 확인할 수 있습니다.
pause
exit /b 0
