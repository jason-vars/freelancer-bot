@echo off
REM ============================================================
REM  freelancer-bot - start the polling loop (double-click)
REM  Uses BOT_POLL_INTERVAL_SECONDS from .env for the interval.
REM  Press Ctrl+C in the window to stop.
REM ============================================================
cd /d "%~dp0"
set PYTHONUTF8=1

if not exist ".venv\Scripts\python.exe" (
    echo [!] .venv not found. Run setup.bat first.
    pause
    exit /b 1
)
if not exist ".env" (
    echo [!] No .env file found in this folder. Create it before running.
    pause
    exit /b 1
)

echo Starting freelancer-bot polling loop. Close this window or press Ctrl+C to stop.
.venv\Scripts\python.exe -m bot.cli run-loop
echo.
echo Loop exited.
pause
