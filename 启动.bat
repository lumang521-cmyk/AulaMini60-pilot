@echo off
chcp 65001 >nul
cd /d "%~dp0"

where pythonw.exe >nul 2>nul
if not errorlevel 1 (
    start "" pythonw.exe run.pyw
    exit /b 0
)

where pyw.exe >nul 2>nul
if not errorlevel 1 (
    start "" pyw.exe -3 run.pyw
    exit /b 0
)

echo 没有找到 Python。请先安装 Python 3.10+，安装时勾选 "Add Python to PATH"。
pause
