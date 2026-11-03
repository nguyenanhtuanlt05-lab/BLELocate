@echo off
rem P1: hosts the dashboard and scans as anchor P1. Uses BLELocate.exe from this folder only.
setlocal
set "APP=%~dp0BLELocate.exe"
if not exist "%APP%" (
  echo [BLE Locate] BLELocate.exe is not next to this file: "%APP%"
  echo Next step: unzip the whole BLELocate folder again; keep the _internal folder beside the exe.
  pause
  exit /b 1
)
"%APP%" hub %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
  echo.
  echo [BLE Locate] P1 stopped with an error, code %CODE%. Read the messages above; see TROUBLESHOOTING.md.
  pause
)
exit /b %CODE%
