@echo off
setlocal EnableDelayedExpansion
title AlgoTrading Pro

echo.
echo  =========================================
echo   AlgoTrading Pro Platform
echo  =========================================
echo.

:: ── Check Python ──────────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install from https://python.org
    pause & exit /b 1
)
echo [ok] Python found

:: ── Check Node ────────────────────────────────────────────────────────────────
node --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js not found. Install from https://nodejs.org
    pause & exit /b 1
)
echo [ok] Node.js found

:: ── Install Python packages ────────────────────────────────────────────────────
echo.
echo [setup] Installing Python packages...
pip install --quiet --upgrade pip
pip install --quiet django channels daphne djangorestframework django-cors-headers ^
    pandas numpy pyyaml python-dotenv MetaTrader5
echo [ok] Python packages ready

:: ── Install Node packages ──────────────────────────────────────────────────────
if not exist "frontend\node_modules" (
    echo [setup] Running npm install in frontend\...
    cd frontend
    call npm install
    cd ..
    echo [ok] Node packages installed
) else (
    echo [ok] Node packages ready
)

:: ── Run Backtest (fetch fresh MT5 data + compute results) ─────────────────────
echo.
echo  -----------------------------------------
echo   Step 1: Running Backtest (MT5 data fetch)
echo  -----------------------------------------
echo   Make sure MetaTrader5 terminal is open and logged in!
echo.
cd src
python -m algoTrading.main_backtest
if errorlevel 1 (
    echo.
    echo [warn] Backtest failed or MT5 not available.
    echo        Dashboard will show last saved results.
    echo        To retry: run  cd src  then  python -m algoTrading.main_backtest
)
cd ..

:: ── Start servers ─────────────────────────────────────────────────────────────
echo.
echo  -----------------------------------------
echo   Step 2: Starting Servers
echo   Backend   http://localhost:8000
echo   Frontend  http://localhost:5173
echo  -----------------------------------------
echo   Open http://localhost:5173 in browser
echo   Press Ctrl+C to stop
echo  -----------------------------------------
echo.
python run.py

pause
