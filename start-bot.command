#!/bin/bash
# ============================================================
#  freelancer-bot - START (double-click in Finder)
#  Mac equivalent of start-bot.vbs. Runs the polling loop +
#  web UI in the background, then opens the dashboard in your
#  browser. To stop it later, double-click stop-bot.command.
#  Logs go to bot.log in this folder.
# ============================================================
set -u

# Always work from the folder this script lives in.
cd "$(dirname "$0")" || exit 1

PY=".venv/bin/python"

if [ ! -x "$PY" ]; then
    osascript -e 'display alert "Freelancer Bot" message "Python environment not found (.venv/bin/python).\n\nCreate it first:\n  python3 -m venv .venv\n  source .venv/bin/activate\n  pip install -r requirements.txt" as critical' >/dev/null 2>&1
    echo "[!] .venv not found. Run: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    read -r -p "Press Enter to close..."
    exit 1
fi

if [ ! -f ".env" ]; then
    osascript -e 'display alert "Freelancer Bot" message "No .env file found in this folder. Create it before running." as warning' >/dev/null 2>&1
    echo "[!] No .env file found in this folder."
    read -r -p "Press Enter to close..."
    exit 1
fi

# If a bot is already running, just open the dashboard instead of starting a second one.
if [ -f "bot.pid" ]; then
    OLD_PID="$(tr -d '[:space:]' < bot.pid)"
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Bot already running (PID $OLD_PID). Opening dashboard..."
        open "http://127.0.0.1:8765"
        exit 0
    fi
fi

# Launch serve (web UI + polling loop) detached, logging to bot.log.
nohup "$PY" -m bot.cli serve >> bot.log 2>&1 &

# Give the web server a moment to bind, then open the dashboard.
sleep 2
open "http://127.0.0.1:8765"

echo "Bot started in the background. Dashboard: http://127.0.0.1:8765"
echo "Logs: bot.log   |   Stop it with stop-bot.command"
