@echo off
rem Checks Bluetooth support files and the adapter, then listens for 10 s.
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [BLE Locate] The Python environment is missing. Next step: run setup.bat first.
  pause
  exit /b 1
)
"%PY%" -m blelocate selftest --require-windows-scanner --scan 10 %*
set "CODE=%ERRORLEVEL%"
pause
exit /b %CODE%
