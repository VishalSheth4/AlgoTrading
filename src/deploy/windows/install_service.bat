@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM install_service.bat  —  Install AlgoTrading as a Windows Service using NSSM
REM
REM Requirements:
REM   Download NSSM from https://nssm.cc/download
REM   Place nssm.exe in this folder or add it to PATH.
REM
REM Run as Administrator:
REM   Right-click → "Run as administrator"
REM
REM After install:
REM   Services panel → "AlgoTrading" → Start
REM   Or: sc start AlgoTrading
REM
REM Uninstall:
REM   nssm remove AlgoTrading confirm
REM ─────────────────────────────────────────────────────────────────────────────

setlocal

set "SCRIPT_DIR=%~dp0"
set "SRC_DIR=%SCRIPT_DIR%..\.."

REM Resolve absolute path
pushd "%SRC_DIR%"
set "SRC_DIR=%CD%"
popd

set "SERVICE_NAME=AlgoTrading"
set "PYTHON_EXE=%SRC_DIR%\venv\Scripts\python.exe"
set "APP_SCRIPT=%SRC_DIR%\run_server.py"
set "LOG_DIR=%SRC_DIR%\logs"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM Find nssm
set "NSSM=nssm.exe"
if exist "%SCRIPT_DIR%nssm.exe" set "NSSM=%SCRIPT_DIR%nssm.exe"

where %NSSM% >nul 2>&1
if errorlevel 1 (
    echo ERROR: nssm.exe not found.
    echo Download from https://nssm.cc/download and place next to this script.
    pause
    exit /b 1
)

echo Installing service: %SERVICE_NAME%
echo Python : %PYTHON_EXE%
echo Script : %APP_SCRIPT%

%NSSM% install %SERVICE_NAME% "%PYTHON_EXE%" "%APP_SCRIPT%"
%NSSM% set %SERVICE_NAME% AppDirectory "%SRC_DIR%"
%NSSM% set %SERVICE_NAME% AppStdout "%LOG_DIR%\service_stdout.log"
%NSSM% set %SERVICE_NAME% AppStderr "%LOG_DIR%\service_stderr.log"
%NSSM% set %SERVICE_NAME% AppRotateFiles 1
%NSSM% set %SERVICE_NAME% AppRotateBytes 5000000
%NSSM% set %SERVICE_NAME% Start SERVICE_AUTO_START
%NSSM% set %SERVICE_NAME% DisplayName "AlgoTrading Dashboard"
%NSSM% set %SERVICE_NAME% Description "AlgoTrading Django dashboard server on port 8765"

echo.
echo Service installed. Starting now...
%NSSM% start %SERVICE_NAME%

echo.
echo Done. Dashboard: http://localhost:8765
pause
