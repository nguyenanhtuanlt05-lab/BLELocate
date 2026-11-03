@echo off
rem Checks the bundled Bluetooth files and the adapter, then listens for 10 s.
setlocal
set "APP=%~dp0BLELocate.exe"
if not exist "%APP%" (
  echo [BLE Locate] BLELocate.exe is not next to this file. Unzip the whole folder again.
  pause
  exit /b 1
)
"%APP%" selftest --require-windows-scanner --scan 10 %*
set "CODE=%ERRORLEVEL%"
pause
exit /b %CODE%
