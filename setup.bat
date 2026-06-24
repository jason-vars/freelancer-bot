@echo off
REM ============================================================
REM  freelancer-bot - one-time Windows setup
REM  Creates the .venv virtualenv and installs dependencies.
REM  Double-click this once before using run-loop.bat.
REM ============================================================
cd /d "%~dp0"
set PYTHONUTF8=1

echo Creating virtual environment (.venv)...
python -m venv .venv
if errorlevel 1 (
    echo.
    echo [!] Could not create the venv. Is Python installed and on PATH?
    echo     Get it from https://www.python.org/downloads/ and tick
    echo     "Add python.exe to PATH" during install.
    pause
    exit /b 1
)

echo Upgrading pip...
.venv\Scripts\python.exe -m pip install --upgrade pip

echo Installing dependencies from requirements.txt...
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [!] pip install failed. See the error above.
    pause
    exit /b 1
)

echo.
echo Done. Make sure a .env file exists in this folder, then:
echo   - double-click run-loop.bat to start polling, or
echo   - run "test-telegram.bat" to verify Telegram delivery.
pause
