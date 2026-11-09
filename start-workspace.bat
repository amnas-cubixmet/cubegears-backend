@echo off
setlocal EnableExtensions
title Cubixgear - Start all local services
cd /d "%~dp0"

rem This file lives in backend\. The other applications are sibling folders.
set "BACKEND=%~dp0"
for %%I in ("%BACKEND%..") do set "ROOT=%%~fI"
set "COMPANY=%ROOT%\company-panel"
set "PUBLIC=%ROOT%\public-website"
set "CUSTOMERS=%ROOT%\customers-website"
if not exist "%CUSTOMERS%\package.json" set "CUSTOMERS=%ROOT%\cubegears-customers-website"
if not exist "%CUSTOMERS%\package.json" set "CUSTOMERS=%ROOT%\customer-website"
set "SETUP=0"
if /I "%~1"=="--setup" set "SETUP=1"

echo.
echo ============================================================
echo    CUBIXGEAR - WINDOWS LOCAL WORKSPACE
echo    Python venv + Django migrations + Redis/Celery + npm
echo ============================================================
echo.

if not exist "%BACKEND%.env" (
  echo [ERROR] backend\.env does not exist.
  echo Make a PRIVATE local .env from .env.example and set DB_ENGINE,
  echo DB_NAME and your other local settings first.
  echo Existing databases and .env files will never be overwritten.
  goto failed
)
if not exist "%BACKEND%start-all.bat" (
  echo [ERROR] backend\start-all.bat was not found.
  goto failed
)
if not exist "%COMPANY%\package.json" (
  echo [ERROR] Company Panel folder was not found: "%COMPANY%"
  goto failed
)
if not exist "%PUBLIC%\package.json" (
  echo [ERROR] Public Website folder was not found: "%PUBLIC%"
  goto failed
)

where npm.cmd >nul 2>nul
if errorlevel 1 (
  echo [ERROR] npm.cmd not found. Install Node.js 22+ and reopen the terminal.
  goto failed
)
where node.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] node.exe not found. Install Node.js 22+ first.
  goto failed
)

set "PYTHON=%BACKEND%venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=%BACKEND%.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo [setup] Creating backend Python virtual environment...
  where py.exe >nul 2>nul
  if not errorlevel 1 (
    py -3 -m venv "%BACKEND%venv"
  ) else (
    where python.exe >nul 2>nul
    if errorlevel 1 (
      echo [ERROR] Python 3.12+ was not found.
      goto failed
    )
    python -m venv "%BACKEND%venv"
  )
  if errorlevel 1 goto failed
  set "PYTHON=%BACKEND%venv\Scripts\python.exe"
)
if not exist "%PYTHON%" (
  echo [ERROR] The Python virtual environment is not ready.
  goto failed
)
echo [ok] Backend Python venv: "%PYTHON%"

"%PYTHON%" -c "import django,celery,redis,dotenv,rest_framework" >nul 2>nul
if errorlevel 1 set "SETUP=1"
if "%SETUP%"=="1" (
  echo [setup] Installing backend Python requirements...
  "%PYTHON%" -m pip install -r "%BACKEND%requirements.txt"
  if errorlevel 1 goto failed
)

echo.
echo [check] Verifying local-only development configuration BEFORE any migration...
"%PYTHON%" scripts\workspace_preflight.py
if errorlevel 1 goto failed

echo [check] Checking Django settings and your existing database...
"%PYTHON%" manage.py check
if errorlevel 1 goto failed

rem Snapshot the actual Django-configured SQLite file (including custom DB_NAME).
rem PostgreSQL remains untouched and no database is deleted or replaced.
echo [backup] Safeguarding the local database before migrations...
"%PYTHON%" scripts\backup_local_sqlite.py
if errorlevel 1 (
  echo [ERROR] Database backup failed. Migration was NOT attempted.
  goto failed
)

echo [migrate] Applying pending migrations (existing DB, no makemigrations)...
"%PYTHON%" manage.py migrate --noinput
if errorlevel 1 (
  echo [ERROR] Migration failed. Services were NOT started.
  goto failed
)

echo.
echo [npm] Checking Company Panel dependencies...
call :prepare_npm "%COMPANY%"
if errorlevel 1 goto failed
echo [npm] Checking Public Website dependencies...
call :prepare_npm "%PUBLIC%"
if errorlevel 1 goto failed
if exist "%CUSTOMERS%\package.json" (
  echo [npm] Checking Customer Website dependencies...
  call :prepare_npm "%CUSTOMERS%"
  if errorlevel 1 goto failed
) else (
  echo [warning] Customer Website folder is missing - skipping that app.
)

echo.
echo [start] Django + Redis + Celery worker + Celery beat...
start "Cubixgear Backend - Django Redis Celery" /D "%BACKEND%" cmd.exe /k "call start-all.bat"

echo [start] Company Panel - http://localhost:5173
start "Cubixgear Company Panel - npm" /D "%COMPANY%" cmd.exe /k "npm run dev -- --host 127.0.0.1"

echo [start] Public Website - http://localhost:3000
start "Cubixgear Public Website - npm" /D "%PUBLIC%" cmd.exe /k "npm run dev"

if exist "%CUSTOMERS%\package.json" (
  echo [start] Customer Website - http://localhost:5174
  start "Cubixgear Customer Website - npm" /D "%CUSTOMERS%" cmd.exe /k "npm run dev"
)

echo.
echo [done] Check each opened terminal for errors:
echo   Backend:          http://127.0.0.1:8000
echo   Company Panel:    http://localhost:5173
echo   Public Website:   http://localhost:3000
echo   Customer Website: http://localhost:5174
echo.
echo Note: Celery starts its WORKER and BEAT, not any payroll payout.
echo Note: Django migrate runs before services. No new models are generated.
echo Stop: Ctrl+C in each terminal. Backend Ctrl+C also stops Celery.
echo First-time/repair: start-workspace.bat --setup
echo.
exit /b 0

:prepare_npm
set "APP_DIR=%~1"
if not exist "%APP_DIR%\package.json" exit /b 1
if "%SETUP%"=="0" if exist "%APP_DIR%\node_modules" exit /b 0
pushd "%APP_DIR%"
if exist "package-lock.json" (
  echo [npm] npm ci: "%APP_DIR%"
  call npm.cmd ci --no-audit --no-fund
) else (
  echo [npm] npm install: "%APP_DIR%"
  call npm.cmd install --no-audit --no-fund
)
if errorlevel 1 (
  popd
  echo [ERROR] npm dependency installation failed for "%APP_DIR%"
  exit /b 1
)
popd
exit /b 0

:failed
echo.
echo [STOPPED] Check the error above. Existing local data was not reset.
exit /b 1
