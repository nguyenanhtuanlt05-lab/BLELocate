@echo off
rem Builds dist\BLELocate\BLELocate.exe and a zip to copy to P1, P2 and P3.
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_exe.ps1" %*
set "CODE=%ERRORLEVEL%"
echo.
pause
exit /b %CODE%
