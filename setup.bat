@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/4] Python venv
where python >nul 2>nul || (echo Python 3.10+ 를 먼저 설치하세요: https://www.python.org/ & pause & exit /b 1)
if not exist .venv python -m venv .venv
.venv\Scripts\python -m pip install -q --upgrade pip
.venv\Scripts\python -m pip install -q -r requirements.txt || (echo pip 설치 실패 & pause & exit /b 1)
echo [2/4] Codex CLI
where codex >nul 2>nul || (
  where npm >nul 2>nul || (echo Node.js 를 먼저 설치하세요: https://nodejs.org/ & pause & exit /b 1)
  call npm install -g @openai/codex
)
echo [3/4] Codex OAuth 로그인 확인
call codex login status | findstr /i "Logged in" >nul || (
  echo 브라우저가 열리면 ChatGPT 계정으로 로그인하세요.
  call codex login
)
call codex login status
echo [4/4] 폴더 준비
if not exist _context mkdir _context
if not exist work mkdir work
if not exist output mkdir output
echo.
echo 설치 완료. _context 에 교재 PDF와 법령 파일을 넣고 run.bat 을 실행하세요.
pause
