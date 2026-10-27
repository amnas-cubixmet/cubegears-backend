@echo off
setlocal
cd /d "%~dp0"

if exist "venv\Scripts\python.exe" (
    set "PYTHON=%~dp0venv\Scripts\python.exe"
) else if exist ".venv\Scripts\python.exe" (
    set "PYTHON=%~dp0.venv\Scripts\python.exe"
) else (
    echo [error] No backend virtual environment found.
    echo Run: py -3 -m venv venv
    echo Then: venv\Scripts\python.exe -m pip install -r requirements.txt
    exit /b 1
)

"%PYTHON%" "%~dp0scripts\dev_services.py"
exit /b %errorlevel%
