@echo off
setlocal
cd /d "%~dp0"

rem ============================================================
rem  init-dev.bat - Set up the development virtualenv (.venv)
rem
rem  Idempotent: reuses the existing venv if present.
rem  Requires Python 3.13 (project target) or newer.
rem ============================================================

set "VENV_DIR=%~dp0.venv"

if exist "%VENV_DIR%\Scripts\python.exe" goto :install

echo Creating virtualenv: .venv ...
where py >nul 2>nul
if errorlevel 1 (
    python -m venv "%VENV_DIR%"
) else (
    rem Preferred: the pinned 3.13 runtime. The py launcher exits 0 even
    rem when 3.13 is missing, so verify the venv actually got created.
    py -3.13 -m venv "%VENV_DIR%" >nul 2>nul
    if not exist "%VENV_DIR%\Scripts\python.exe" py -m venv "%VENV_DIR%"
)
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo ERROR: failed to create virtualenv.
    echo Install Python 3.13+ from https://www.python.org/downloads/ and retry.
    exit /b 1
)

:install
echo Installing dev dependencies ...
"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
"%VENV_DIR%\Scripts\python.exe" -m pip install -r "%~dp0requirements-dev.txt"
if errorlevel 1 exit /b 1

echo.
echo Development environment ready.
echo   Active Python:
"%VENV_DIR%\Scripts\python.exe" --version
echo   Activate : .venv\Scripts\activate
echo   Init app : python main.py init
echo   Tests    : pytest
echo   Lint     : ruff check .   /   ruff format .
echo   Typecheck: mypy
exit /b 0