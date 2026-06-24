@echo off
REM ============================================================
REM  freelancer-bot - send a test Telegram notification
REM  Confirms your TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID work.
REM ============================================================
cd /d "%~dp0"
set PYTHONUTF8=1

if not exist ".venv\Scripts\python.exe" (
    echo [!] .venv not found. Run setup.bat first.
    pause
    exit /b 1
)

.venv\Scripts\python.exe -m bot.cli test-telegram
pause
