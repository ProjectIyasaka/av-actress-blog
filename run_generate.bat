@echo off
REM av-actress-blog auto-generation runner
REM 使い方: run_generate.bat [--limit N] [--force]

setlocal
cd /d "%~dp0"

if not exist logs mkdir logs

set LOGFILE=logs\generate.log

echo ===== %date% %time% START ===== >> %LOGFILE%

REM Activate venv if present
if exist .venv\Scripts\activate.bat (
  call .venv\Scripts\activate.bat
)

python scripts\generate.py %* >> %LOGFILE% 2>&1
set GEN_EXIT=%errorlevel%

if %GEN_EXIT% neq 0 (
  echo generate.py failed with exit code %GEN_EXIT% >> %LOGFILE%
  call :notify_failure %GEN_EXIT%
  exit /b %GEN_EXIT%
)

REM Stage only generation outputs (whitelist)
git add actress\ index.html ranking-top10.html sitemap.xml manifest.json 2>> %LOGFILE%

REM Commit only if there are staged changes
git diff --cached --quiet
if %errorlevel% equ 0 (
  echo no changes to commit >> %LOGFILE%
  echo ===== %date% %time% END (no changes) ===== >> %LOGFILE%
  exit /b 0
)

git commit -m "chore: auto-update via FANZA API" >> %LOGFILE% 2>&1
git push >> %LOGFILE% 2>&1
set PUSH_EXIT=%errorlevel%

if %PUSH_EXIT% neq 0 (
  echo git push failed with exit code %PUSH_EXIT% >> %LOGFILE%
  call :notify_failure %PUSH_EXIT%
  exit /b %PUSH_EXIT%
)

echo ===== %date% %time% END (success) ===== >> %LOGFILE%
exit /b 0

:notify_failure
if defined DISCORD_WEBHOOK_URL (
  curl -s -H "Content-Type: application/json" -d "{\"content\":\"av-actress-blog generate FAILED (exit %1)\"}" "%DISCORD_WEBHOOK_URL%" >> %LOGFILE% 2>&1
)
exit /b 0
