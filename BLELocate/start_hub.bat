@echo off
rem P1: hosts the dashboard and scans as anchor P1. Extra options are passed on,
rem e.g.  start_hub.bat --port 8790   or   start_hub.bat --no-scan
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo.
  echo [BLE Locate] The Python environment is missing: "%PY%"
  echo Next step: double-click setup.bat in this folder, then start P1 again.
  echo.
  pause
  exit /b 1
)
"%PY%" -m blelocate hub %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
  echo.
  echo [BLE Locate] P1 stopped with an error, code %CODE%. Read the messages above; see TROUBLESHOOTING.md.
  pause
)
exit /b %CODE%
