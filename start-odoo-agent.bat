@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Odoo Agent Python environment was not found.
  echo Expected: %~dp0.venv\Scripts\python.exe
  echo Please finish the project setup first.
  pause
  exit /b 1
)

powershell.exe -NoProfile -Command "try { Invoke-WebRequest 'http://127.0.0.1:8090/api/health' -UseBasicParsing -TimeoutSec 1 ^| Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  start "Odoo Agent Backend" powershell.exe -NoProfile -NoExit -ExecutionPolicy Bypass -File "%~dp0start-backend.ps1" -NoReload
)

powershell.exe -NoProfile -WindowStyle Hidden -Command "$url='http://127.0.0.1:8090/ui/index.html'; for ($i=0; $i -lt 40; $i++) { try { Invoke-WebRequest 'http://127.0.0.1:8090/api/health' -UseBasicParsing -TimeoutSec 1 ^| Out-Null; Start-Process $url; exit 0 } catch { Start-Sleep -Milliseconds 250 } }; exit 1"
if errorlevel 1 (
  echo Odoo Agent did not become ready at http://127.0.0.1:8090
  echo Review the backend window for details.
  pause
  exit /b 1
)

endlocal
