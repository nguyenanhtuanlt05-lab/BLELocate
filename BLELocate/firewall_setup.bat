@echo off
rem Run once on P1: lets the anchors and phones reach the dashboard (asks for admin rights).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\firewall.ps1" %*
