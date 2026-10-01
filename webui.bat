@echo off
REM ============================================================
REM  freelancer-bot - open the settings web UI (double-click)
REM  Edits .env (filters, search terms, bidding, integrations).
REM  Changes apply on the next run-loop cycle automatically.
REM  Press Ctrl+C in this window to stop the UI.
REM ============================================================
cd /d "%~dp0"
set PYTHONUTF8=1

if not exist ".venv\Scripts\python.exe" (
    echo [!] .venv not found. Run setup.bat first.
    pause
    exit /b 1
)

start "" http://127.0.0.1:8765
.venv\Scripts\python.exe -m bot.cli webui
echo.
echo Web UI exited.
pause
