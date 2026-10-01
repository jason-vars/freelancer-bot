#!/bin/bash
# ============================================================
#  freelancer-bot - INSTALL 24/7 SERVICE (double-click in Finder)
#  Registers a macOS LaunchAgent that:
#    - starts the bot (serve = web UI + polling loop) at login
#    - restarts it automatically if it ever crashes (KeepAlive)
#    - wraps it in `caffeinate -i` so an idle Mac does not sleep
#      and pause polling
#  Paths are derived from THIS file's location, so it works no
#  matter where the project folder lives. Re-run any time to
#  reinstall/update. To remove it, run uninstall-service.command.
# ============================================================
set -u

cd "$(dirname "$0")" || exit 1
APP_DIR="$(pwd)"
PY="$APP_DIR/.venv/bin/python"
LABEL="com.freelancerbot.serve"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_NUM="$(id -u)"

if [ ! -x "$PY" ]; then
    echo "[!] Python env not found at: $PY"
    echo "    Create it first:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    read -r -p "Press Enter to close..."
    exit 1
fi

# A LaunchAgent runs under launchd, which is often DENIED access to files inside
# ~/Documents, ~/Desktop, ~/Downloads (macOS privacy). Warn loudly — the bot would
# then fail to write its database even though it runs fine from Terminal.
case "$APP_DIR" in
    "$HOME/Documents"/*|"$HOME/Desktop"/*|"$HOME/Downloads"/*)
        echo "[!] WARNING: this folder is inside a macOS-protected location:"
        echo "      $APP_DIR"
        echo "    The background service may be blocked from writing its database here."
        echo "    Strongly recommended: move the folder to your home folder first, e.g.:"
        echo "      mv \"$APP_DIR\" \"$HOME/freelancer-bot\""
        echo "    then double-click install-service.command from the new location."
        read -r -p "Continue installing here anyway? [y/N] " ans
        case "$ans" in y|Y) : ;; *) echo "Aborted."; exit 1 ;; esac
        ;;
esac

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/caffeinate</string>
        <string>-i</string>
        <string>$PY</string>
        <string>-m</string>
        <string>bot.cli</string>
        <string>serve</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$APP_DIR</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>$APP_DIR/bot.log</string>
    <key>StandardErrorPath</key>
    <string>$APP_DIR/bot.log</string>
</dict>
</plist>
PLIST

echo "Wrote $PLIST"

# Modern launchctl (Catalina+). `bootout` first so a re-run cleanly replaces it.
launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null
if ! launchctl bootstrap "gui/$UID_NUM" "$PLIST"; then
    echo "[!] launchctl bootstrap failed. See any message above."
    read -r -p "Press Enter to close..."
    exit 1
fi
launchctl enable "gui/$UID_NUM/$LABEL"
launchctl kickstart -k "gui/$UID_NUM/$LABEL"

echo
echo "Installed. The bot will now run 24/7 and start automatically at login."
echo "  Status:   launchctl print gui/$UID_NUM/$LABEL | grep state"
echo "  Logs:     tail -f \"$APP_DIR/bot.log\""
echo "  Dashboard: http://127.0.0.1:8765"
echo "  Remove:   double-click uninstall-service.command"
read -r -p "Press Enter to close..."



# Option 1 — Run it, keep the terminal open (simplest)

# cd ~/freelancer-bot
# .venv/bin/python -m bot.cli serve
# Runs in the foreground, logs print right there. Closing the terminal stops it. Good for testing.

# Option 2 — Keep running after you close the terminal

# cd ~/freelancer-bot
# nohup caffeinate -i .venv/bin/python -m bot.cli serve >> bot.log 2>&1 &
# nohup … & → survives closing the terminal window
# caffeinate -i → stops the Mac sleeping and pausing it
# Check / watch / stop it:


# tail -f ~/freelancer-bot/bot.log          # watch live (Ctrl+C to stop watching)
# cat ~/freelancer-bot/bot.pid               # the running process id
# kill "$(cat ~/freelancer-bot/bot.pid)"     # stop it