@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-backend.ps1"
set "stopExitCode=%ERRORLEVEL%"

echo.
if not "%stopExitCode%"=="0" (
  echo Stop did not complete successfully. Review the message above.
)
pause
exit /b %stopExitCode%
