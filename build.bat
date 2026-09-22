@echo off
setlocal

rem ============================================================
rem  build.bat - Build pipeline for lpm
rem
rem  Usage:
rem    build.bat              Full pipeline (exe + installer)
rem    build.bat --exe        Build exe only
rem    build.bat --installer  Build installer only (requires dist/main.dist/lpm.exe)
rem    build.bat --clean      Remove build artifacts, then run the full pipeline
rem ============================================================

set "PROJECT_DIR=%~dp0"
set "MAKENSIS=C:\Program Files (x86)\NSIS\makensis.exe"
set "PIPELINE_START=%TIME%"

if "%~1"=="--exe"        goto :build_exe
if "%~1"=="--installer"  goto :build_installer
if "%~1"=="--clean"      goto :build_clean

rem --- Full pipeline ---
call :build_exe
if errorlevel 1 exit /b 1
call :build_installer
if errorlevel 1 exit /b 1

echo.
call :show_elapsed "Whole pipeline" "%PIPELINE_START%"
echo Build pipeline complete.
exit /b 0

rem ============================================================
:build_clean
echo Cleaning previous build artifacts ...
if exist "%PROJECT_DIR%dist" (
    echo   Removing %PROJECT_DIR%dist
    rmdir /s /q "%PROJECT_DIR%dist"
)
if exist "%PROJECT_DIR%installer\lpm-setup.exe" (
    echo   Removing %PROJECT_DIR%installer\lpm-setup.exe
    del /f /q "%PROJECT_DIR%installer\lpm-setup.exe"
)
echo.
call :build_exe
if errorlevel 1 exit /b 1
call :build_installer
if errorlevel 1 exit /b 1
echo.
call :show_elapsed "Whole pipeline" "%PIPELINE_START%"
echo Build pipeline complete.
exit /b 0

rem ============================================================
:build_exe
set "EXE_START=%TIME%"
echo Building lpm.exe ...
python "%PROJECT_DIR%build.py"
if errorlevel 1 (
    echo ERROR: exe build failed.
    exit /b 1
)
call :show_elapsed "Compiling the project" "%EXE_START%"
exit /b 0

rem ============================================================
:build_installer
set "INST_START=%TIME%"
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
call :show_elapsed "Creating the installer" "%INST_START%"
exit /b 0

rem ============================================================
:show_elapsed
rem  %1 = human-readable label, %2 = start time string (%TIME% snapshot)
rem  Parses HH:MM:SS,cc strings without hitting cmd's octal parsing
rem  (leading zeros like 08/09 are stripped before arithmetic).
setlocal
set "LABEL=%~1"
set "T_START=%~2"
call :parse_time "%T_START%" S0
call :parse_time "%TIME%" S1
set /a "DIFF=S1-S0"
if %DIFF% lss 0 set /a "DIFF+=8640000"
set /a "SEC=DIFF/100"
set /a "MM=SEC/60"
set /a "SS=SEC%%60"
if %MM% lss 10 set "MM=0%MM%"
if %SS% lss 10 set "SS=0%SS%"
echo %LABEL% took %MM%:%SS%
endlocal
exit /b 0

rem ============================================================
:parse_time
rem  %1 = "HH:MM:SS,cc" string, %2 = output var name (gets hundredths)
setlocal
set "STR=%~1"
for /f "tokens=1-4 delims=:.," %%a in ("%STR%") do (
    set "H=%%a" & set "M=%%b" & set "S=%%c" & set "C=%%d"
)
if not defined H set "H=0"
if not defined M set "M=0"
if not defined S set "S=0"
if not defined C set "C=0"
if "%H:~0,1%"=="0" if not "%H:~1%"=="" set "H=%H:~1%"
if "%M:~0,1%"=="0" if not "%M:~1%"=="" set "M=%M:~1%"
if "%S:~0,1%"=="0" if not "%S:~1%"=="" set "S=%S:~1%"
if "%C:~0,1%"=="0" if not "%C:~1%"=="" set "C=%C:~1%"
set /a "TOTAL=(((H*60)+M)*60+S)*100+C"
endlocal & set "%2=%TOTAL%"
exit /b 0
