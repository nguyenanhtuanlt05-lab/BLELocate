@echo off
rem Run once on P1: lets anchors and phones reach the dashboard (asks for admin rights).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0firewall.ps1" %*
