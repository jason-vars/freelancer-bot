#!/bin/bash
# ============================================================
#  freelancer-bot - STOP (double-click in Finder)
#  Mac equivalent of stop-bot.vbs. Stops the background bot
#  started by start-bot.command, using the process id in
#  bot.pid.
# ============================================================
set -u

cd "$(dirname "$0")" || exit 1

if [ ! -f "bot.pid" ]; then
    osascript -e 'display notification "No running bot found (bot.pid is missing). It may already be stopped." with title "Freelancer Bot"' >/dev/null 2>&1
    echo "No running bot found (bot.pid is missing)."
    exit 0
fi

PID="$(tr -d '[:space:]' < bot.pid)"

if [ -z "$PID" ]; then
    rm -f bot.pid
    echo "bot.pid was empty - nothing to stop."
    exit 0
fi

if ! kill -0 "$PID" 2>/dev/null; then
    echo "Process $PID is not running (stale pid). Cleaning up."
    rm -f bot.pid
    exit 0
fi

# Ask it to stop gracefully first, then force-kill if it lingers.
kill -TERM "$PID" 2>/dev/null
for _ in 1 2 3 4 5; do
    kill -0 "$PID" 2>/dev/null || break
    sleep 1
done
if kill -0 "$PID" 2>/dev/null; then
    kill -KILL "$PID" 2>/dev/null
fi

rm -f bot.pid
osascript -e 'display notification "Bot stopped." with title "Freelancer Bot"' >/dev/null 2>&1
echo "Bot stopped."
