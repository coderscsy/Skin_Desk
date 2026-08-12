@echo off
@chcp 65001 >nul
setlocal
title Skin Desk
cd /d "%~dp0"

echo ========================================
echo    Skin Desk
echo ========================================
echo.

set "URL=http://127.0.0.1:8777"
set "OPEN_URL=http://127.0.0.1:8777/?launcher=1"

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -UseBasicParsing '%URL%/' -TimeoutSec 2; if ($r.Content -like '*Skin Desk*' -or $r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>nul
if not errorlevel 1 (
  echo [提示] 倒货台已经在运行，正在打开浏览器...
  start "" "%OPEN_URL%"
  exit /b 0
)

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
  where py >nul 2>nul && set "PY=py"
)
if not defined PY goto nopy

if not exist ".venv\Scripts\python.exe" (
  echo [首次启动] 正在自动准备运行环境和安装所需组件...
  echo 请保持网络连接并耐心等待，通常需要 1 到 5 分钟。
  echo 此过程不需要输入任何命令，请勿关闭窗口。
  %PY% -m venv .venv
  if errorlevel 1 goto venverr
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  if exist "requirements.txt" (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
      echo [重试] 官方下载源连接失败，正在自动切换到国内镜像...
      ".venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    )
  ) else (
    ".venv\Scripts\python.exe" -m pip install flask requests
    if errorlevel 1 (
      echo [重试] 官方下载源连接失败，正在自动切换到国内镜像...
      ".venv\Scripts\python.exe" -m pip install flask requests -i https://pypi.tuna.tsinghua.edu.cn/simple
    )
  )
  if errorlevel 1 goto piperr
  echo [完成] 运行环境准备完毕。
  echo.
)

start "" /b cmd /c "ping -n 3 127.0.0.1 >nul & start %OPEN_URL%"

echo 正在启动倒货台，浏览器将自动打开 %URL%
echo 使用期间请保持此窗口开启；关闭窗口会停止倒货台。
echo ----------------------------------------
".venv\Scripts\python.exe" app.py
goto end

:nopy
echo [无法启动] 电脑尚未安装 Python，或安装时没有加入系统路径。
echo 即将打开 Python 官方下载页面。
echo 安装时请务必勾选 "Add Python to PATH"，安装完成后重新双击本文件。
start "" "https://www.python.org/downloads/windows/"
goto end

:venverr
echo [安装失败] 无法创建运行环境。
echo 请确认 Python 安装完整，并尝试把本程序解压到桌面后重新启动。
goto end

:piperr
echo [安装失败] 所需组件下载失败。
echo 请检查网络连接，或开启能够访问 Python 软件源的网络后重新启动。
goto end

:end
echo.
pause
