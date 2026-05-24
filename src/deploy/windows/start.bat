@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM start.bat  —  Start AlgoTrading dashboard on Windows
REM
REM Double-click this file, or run from cmd:
REM     start.bat
REM ─────────────────────────────────────────────────────────────────────────────

setlocal

REM Resolve the src/ directory (two levels up from deploy\windows\)
set "SCRIPT_DIR=%~dp0"
set "SRC_DIR=%SCRIPT_DIR%..\.."
cd /d "%SRC_DIR%"

REM Activate venv if present
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
    echo [server] venv activated
)

REM Copy .env.example → .env on first run
if not exist ".env" (
    if exist ".env.example" (
        copy ".env.example" ".env" >nul
        echo [server] Created .env from .env.example - edit it before going public!
    )
)

echo [server] Starting AlgoTrading dashboard on port 8765...
echo [server] Open: http://localhost:8765
echo.

python run_server.py

pause
