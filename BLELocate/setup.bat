@echo off
rem Creates .venv and installs everything BLE Locate needs (run once per laptop).
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1" %*
set "CODE=%ERRORLEVEL%"
echo.
pause
exit /b %CODE%
