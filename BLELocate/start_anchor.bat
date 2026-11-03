@echo off
rem P2/P3: finds P1 on the network and scans. Examples:
rem   start_anchor.bat                      asks for the role, finds P1 automatically
rem   start_anchor.bat --anchor P2 --hub 192.168.1.10
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo.
  echo [BLE Locate] The Python environment is missing: "%PY%"
  echo Next step: double-click setup.bat in this folder, then start the anchor again.
  echo.
  pause
  exit /b 1
)
set "ARGS=%*"
if "%~1"=="" (
  set /p "ROLE=Which anchor is this laptop? Type P2 or P3, or press Enter for the next free role: "
)
if defined ROLE set "ARGS=--anchor %ROLE%"
"%PY%" -m blelocate anchor %ARGS%
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
  echo.
  echo [BLE Locate] The anchor stopped, code %CODE%. Read the messages above; see TROUBLESHOOTING.md.
  pause
)
exit /b %CODE%
