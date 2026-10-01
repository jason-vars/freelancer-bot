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

# Verify we can actually write in this folder BEFORE claiming success. macOS blocks
# apps from writing inside Documents/Desktop/Downloads (TCC): the write fails with
# "Operation not permitted", the redirect below never runs, and the bot silently
# never starts. Catch that here and explain it, instead of printing a false "started".
if ! ( : > ".write-test.$$" ) 2>/dev/null; then
    HERE="$(pwd)"
    MSG="Cannot write files in this folder:\n$HERE\n\nmacOS is blocking it (likely because it is inside Documents, Desktop, or Downloads).\n\nFix: move the whole freelancer-bot folder to your home folder, e.g.\n  ~/freelancer-bot\nthen double-click start-bot.command again.\n\n(Or grant Terminal Full Disk Access in System Settings > Privacy & Security.)"
    osascript -e "display alert \"Freelancer Bot - cannot start\" message \"$MSG\" as critical" >/dev/null 2>&1
    echo "[!] Cannot write in $HERE (macOS 'Operation not permitted')."
    echo "    Move this folder out of Documents/Desktop/Downloads (e.g. to ~/freelancer-bot) and retry."
    read -r -p "Press Enter to close..."
    exit 1
fi
rm -f ".write-test.$$"

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
BOT_PID=$!

# Give the web server a moment to bind, then confirm the process is still alive.
# If serve crashed on startup (bad .env, port in use, import error), don't lie about
# success — show the tail of bot.log so the real reason is visible.
sleep 3
if ! kill -0 "$BOT_PID" 2>/dev/null; then
    echo "[!] The bot exited immediately after starting. Last lines of bot.log:"
    echo "------------------------------------------------------------"
    tail -n 20 "bot.log" 2>/dev/null || echo "(bot.log unavailable)"
    echo "------------------------------------------------------------"
    osascript -e 'display alert "Freelancer Bot - failed to start" message "The bot exited right after launching. See the terminal window (tail of bot.log) for the reason." as critical' >/dev/null 2>&1
    read -r -p "Press Enter to close..."
    exit 1
fi

open "http://127.0.0.1:8765"

echo "Bot started in the background (PID $BOT_PID). Dashboard: http://127.0.0.1:8765"
echo "Logs: bot.log   |   Stop it with stop-bot.command"
