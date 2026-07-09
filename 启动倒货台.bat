@echo off
setlocal
title Skin Desk
cd /d "%~dp0"

echo ========================================
echo    Skin Desk
echo ========================================
echo.

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
  where py >nul 2>nul && set "PY=py"
)
if not defined PY goto nopy

if not exist ".venv\Scripts\python.exe" (
  echo First run: creating environment and installing dependencies...
  echo This needs internet and may take a minute.
  %PY% -m venv .venv
  if errorlevel 1 goto venverr
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  if exist "requirements.txt" (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  ) else (
    ".venv\Scripts\python.exe" -m pip install flask requests
  )
  if errorlevel 1 goto piperr
  echo [OK] Environment ready.
  echo.
)

start "" /b cmd /c "ping -n 3 127.0.0.1 >nul & start http://127.0.0.1:8777"

echo Starting server... your browser will open http://127.0.0.1:8777
echo Keep this window open. Close it to stop the program.
echo ----------------------------------------
".venv\Scripts\python.exe" app.py
goto end

:nopy
echo [ERROR] Python was not found on your PATH.
echo Install Python from https://www.python.org/downloads/
echo and tick "Add Python to PATH" during install, then run this again.
goto end

:venverr
echo [ERROR] Could not create the virtual environment (.venv).
goto end

:piperr
echo [ERROR] Could not install flask/requests. Check your internet connection.
goto end

:end
echo.
pause
