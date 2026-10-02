@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_full_collection.ps1"
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo [G2B] COLLECTION STOPPED.
  echo [G2B] Local compatibility SQLite was not deleted. Run this file again to resume.
  echo [G2B] Check D:\G2B\logs for details.
  pause
  exit /b %RC%
)

echo [G2B] COLLECTION COMPLETE.
echo [G2B] Check D:\G2B\logs and D:\G2B\snapshot for results.
pause
exit /b 0
