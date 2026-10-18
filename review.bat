@echo off
chcp 65001 >nul
cd /d "%~dp0"
set CH=%1
if "%CH%"=="" set CH=ch09
set PYTHONUTF8=1
rem 판정은 다시 하지 않고 검토 화면만 다시 연다
.venv\Scripts\python -m jev.server %CH%
pause
