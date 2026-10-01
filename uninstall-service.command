#!/bin/bash
# ============================================================
#  freelancer-bot - UNINSTALL 24/7 SERVICE (double-click in Finder)
#  Stops the background bot and removes the LaunchAgent so it no
#  longer starts at login. Does NOT touch your .env, database,
#  or logs. Re-run install-service.command to set it up again.
# ============================================================
set -u

LABEL="com.freelancerbot.serve"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_NUM="$(id -u)"

launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null && \
    echo "Stopped and unloaded $LABEL." || \
    echo "$LABEL was not loaded (nothing to stop)."

if [ -f "$PLIST" ]; then
    rm -f "$PLIST"
    echo "Removed $PLIST"
fi

echo "Done. The bot will no longer run automatically."
read -r -p "Press Enter to close..."
