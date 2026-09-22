@echo off
setlocal

rem ============================================================
rem  build.bat - Build pipeline for lpm
rem
rem  Usage:
rem    build.bat              Full pipeline (exe + installer)
rem    build.bat --exe        Build exe only
rem    build.bat --installer  Build installer only (requires dist/main.dist/lpm.exe)
rem ============================================================

set "PROJECT_DIR=%~dp0"
set "MAKENSIS=C:\Program Files (x86)\NSIS\makensis.exe"

if "%~1"=="--exe"        goto :build_exe
if "%~1"=="--installer"  goto :build_installer

rem --- Full pipeline ---
call :build_exe
if errorlevel 1 exit /b 1
call :build_installer
if errorlevel 1 exit /b 1

echo.
echo Build pipeline complete.
exit /b 0

rem ============================================================
:build_exe
echo Building lpm.exe ...
python "%PROJECT_DIR%build.py"
if errorlevel 1 (
    echo ERROR: exe build failed.
    exit /b 1
)
exit /b 0

rem ============================================================
:build_installer
if not exist "%PROJECT_DIR%dist\main.dist\lpm.exe" (
    echo ERROR: dist\main.dist\lpm.exe not found. Run build first.
    exit /b 1
)
if not exist "%MAKENSIS%" (
    echo ERROR: NSIS not found at "%MAKENSIS%".
    echo Install NSIS from https://nsis.sourceforge.io/Download
    exit /b 1
)
echo Building installer ...
"%MAKENSIS%" "%PROJECT_DIR%installer\lpm.nsi"
if errorlevel 1 (
    echo ERROR: installer build failed.
    exit /b 1
)
exit /b 0
