@echo off
chcp 65001 >nul
cd /d "%~dp0"
set CH=%1
if "%CH%"=="" set CH=ch09
if not exist .venv\Scripts\python.exe (echo 먼저 setup.bat 을 실행하세요. & pause & exit /b 1)
set PYTHONUTF8=1
.venv\Scripts\python -m jev.pipeline %CH% --serve
pause
