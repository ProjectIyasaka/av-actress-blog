@echo off
REM av-actress-blog auto-generation runner
REM 使い方: run_generate.bat [--limit N] [--force]

setlocal
cd /d "%~dp0"

if not exist logs mkdir logs

REM Date-stamped log file (locale-independent via Python)
for /f %%i in ('python -c "import datetime; print(datetime.date.today().strftime('%%Y-%%m-%%d'))"') do set LOGDATE=%%i
set LOGFILE=logs\generate_%LOGDATE%.log

REM Rotate: delete log files older than 7 days
forfiles /p logs /m "generate_*.log" /d -7 /c "cmd /c del @path" 2>nul

REM Activate venv if present
if exist .venv\Scripts\activate.bat (
  call .venv\Scripts\activate.bat
)

echo ===== %date% %time% START ===== >> %LOGFILE%

python scripts\generate.py %* >> %LOGFILE% 2>&1
set GEN_EXIT=%errorlevel%

if %GEN_EXIT% neq 0 (
  echo generate.py failed with exit code %GEN_EXIT% >> %LOGFILE%
  python scripts\notify.py "av-actress-blog generate FAILED (exit %GEN_EXIT%)" >> %LOGFILE% 2>&1
  echo ===== %date% %time% END (FAILED) ===== >> %LOGFILE%
  exit /b %GEN_EXIT%
)

REM Stage only generation outputs (whitelist)
git add actress\ genre\ ranking\ index.html ranking-top10.html sitemap.xml manifest.json 2>> %LOGFILE%

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
  python scripts\notify.py "av-actress-blog git push FAILED (exit %PUSH_EXIT%)" >> %LOGFILE% 2>&1
  echo ===== %date% %time% END (PUSH FAILED) ===== >> %LOGFILE%
  exit /b %PUSH_EXIT%
)

echo ===== %date% %time% END (success) ===== >> %LOGFILE%
exit /b 0
