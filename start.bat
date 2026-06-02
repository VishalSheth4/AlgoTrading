@echo off
setlocal EnableDelayedExpansion
title AlgoTrading Pro — Startup

echo.
echo  =========================================
echo   AlgoTrading Pro Platform
echo  =========================================
echo.

:: ── Check Python ──────────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ from https://python.org
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

echo.
echo [setup] Installing / verifying Python packages...
echo.

:: ── Install Python packages ────────────────────────────────────────────────────
pip install --quiet --upgrade pip

pip install --quiet ^
    django ^
    channels ^
    daphne ^
    djangorestframework ^
    django-cors-headers ^
    pandas ^
    numpy ^
    pyyaml ^
    python-dotenv ^
    MetaTrader5

echo [ok] Python packages ready

:: ── Install Node packages ──────────────────────────────────────────────────────
if not exist "frontend\node_modules" (
    echo.
    echo [setup] Running npm install in frontend\...
    cd frontend
    call npm install
    cd ..
    echo [ok] Node packages installed
) else (
    echo [ok] Node packages already installed
)

echo.
echo  -----------------------------------------
echo   Backend   http://localhost:8000
echo   Frontend  http://localhost:5173
echo  -----------------------------------------
echo   Open http://localhost:5173 in browser
echo   Press Ctrl+C to stop
echo  -----------------------------------------
echo.

:: ── Launch both servers ────────────────────────────────────────────────────────
python run.py

pause
