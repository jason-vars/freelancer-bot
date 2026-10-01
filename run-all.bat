@echo off
REM ============================================================
REM  freelancer-bot - start EVERYTHING (double-click)
REM  Runs the polling loop AND the settings/jobs web UI at once.
REM    * Web UI    -> opens in its own window + your browser
REM    * Poll loop -> runs in THIS window
REM  Close a window (or press Ctrl+C in it) to stop that part.
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

REM 1) Launch the web UI in its own window so its logs stay separate.
start "Freelancer Bot - Web UI" .venv\Scripts\python.exe -m bot.cli webui

REM 2) Give the server a moment to bind, then open the browser.
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:8765

REM 3) Run the polling loop in THIS window (Ctrl+C here stops the loop only).
echo.
echo Web UI is running in a separate window: http://127.0.0.1:8765
echo Polling loop is running in THIS window.
echo Close this window or press Ctrl+C to stop the loop (the Web UI keeps running).
echo.
.venv\Scripts\python.exe -m bot.cli run-loop
echo.
echo Loop exited. The Web UI window may still be open - close it to stop the UI.
pause
