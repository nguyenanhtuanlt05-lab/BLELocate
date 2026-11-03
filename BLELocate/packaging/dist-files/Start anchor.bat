@echo off
rem P2/P3: finds P1 on the network and scans. Uses BLELocate.exe from this folder only.
rem   "Start anchor.bat" --anchor P2 --hub 192.168.1.10   skips the question and the search
setlocal
set "APP=%~dp0BLELocate.exe"
if not exist "%APP%" (
  echo [BLE Locate] BLELocate.exe is not next to this file: "%APP%"
  echo Next step: unzip the whole BLELocate folder again; keep the _internal folder beside the exe.
  pause
  exit /b 1
)
set "ARGS=%*"
if "%~1"=="" (
  set /p "ROLE=Which anchor is this laptop? Type P2 or P3, or press Enter for the next free role: "
)
if defined ROLE set "ARGS=--anchor %ROLE%"
"%APP%" anchor %ARGS%
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
  echo.
  echo [BLE Locate] The anchor stopped, code %CODE%. Read the messages above; see TROUBLESHOOTING.md.
  pause
)
exit /b %CODE%
