@echo off
rem Windows: double-click this file to open the Lead List Builder.
cd /d "%~dp0"

set "PY="
rem The one-line installer brings its own Python inside .venv.
if exist .venv\Scripts\python.exe set "PY=.venv\Scripts\python.exe"
if not defined PY where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python is not installed yet. Opening the download page...
  start https://www.python.org/downloads/
  echo Install it ^(tick "Add python.exe to PATH"^), then start the Lead List Builder again.
  pause
  exit /b 1
)

fc /b requirements.txt .venv\requirements.txt >nul 2>nul
if errorlevel 1 (
  echo Setting things up ^(about a minute, only the first time^)...
  if not exist .venv\Scripts\python.exe %PY% -m venv .venv || goto failed
  .venv\Scripts\python -m pip install --quiet --disable-pip-version-check -r requirements.txt || goto failed
  copy /y requirements.txt .venv\requirements.txt >nul
)

set PYTHONUTF8=1
.venv\Scripts\python leads.py app
pause
exit /b 0

:failed
echo Setup failed ^(see above^). Check your internet and try again.
pause
exit /b 1
