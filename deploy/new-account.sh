#!/usr/bin/env bash
# Create one account directory for the templated service (freelancer-bot@<name>).
#
#   sudo deploy/new-account.sh jaison
#   sudo deploy/new-account.sh user2
#
# Each account gets its own .env (tokens, filters, Telegram chat), its own
# bot.sqlite3, and its own web UI port. Nothing is shared but the code.
set -euo pipefail

ROOT=${ROOT:-/opt/freelancer-bot}
APP="$ROOT/app"
ACCOUNTS="$ROOT/accounts"
OWNER=${OWNER:-botuser}

name=${1:-}
if [[ -z $name ]]; then
    echo "usage: $0 <account-name>" >&2
    exit 1
fi
if [[ ! $name =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
    # The name becomes a systemd instance and a directory, so keep it boring.
    echo "Account name must be lowercase letters, digits, '-' or '_': $name" >&2
    exit 1
fi

dir="$ACCOUNTS/$name"
if [[ -e $dir ]]; then
    echo "Account already exists: $dir" >&2
    exit 1
fi
if [[ ! -f "$APP/.env.example" ]]; then
    echo "Code checkout not found at $APP (set ROOT=... if your layout differs)." >&2
    exit 1
fi

# Next free port, so accounts never collide: 8765, 8766, 8767, ...
# nullglob matters: with no accounts yet the glob must vanish rather than be passed
# to grep as a literal path (which silently "matched" and left every account on 8765).
shopt -s nullglob
used=("$ACCOUNTS"/*/service.env)
shopt -u nullglob
port=8765
while ((${#used[@]})) && grep -qs "^PORT=$port$" "${used[@]}"; do
    port=$((port + 1))
done

mkdir -p "$dir"
cp "$APP/.env.example" "$dir/.env"
printf 'PORT=%s\n' "$port" > "$dir/service.env"

chown -R "$OWNER:$OWNER" "$dir"
# .env holds the Freelancer token, the OpenAI key and the Telegram token: owner-only.
chmod 700 "$dir"
chmod 600 "$dir/.env"

cat <<EOF

Created $dir (web UI port $port)

Next:
  1. Fill in the account's own credentials:
       sudoedit $dir/.env
     Required: FLN_OAUTH_TOKEN (that account's own token), TELEGRAM_BOT_TOKEN,
     TELEGRAM_CHAT_ID, OPENAI_API_KEY. Keep BOT_DRY_RUN=1 for the first run.
  2. Start it:
       sudo systemctl enable --now freelancer-bot@$name
       journalctl -u freelancer-bot@$name -f
  3. Open its settings page from your own machine (the UI has no login, so it
     is bound to localhost on the server):
       ssh -L $port:127.0.0.1:$port <you>@<server>
     then browse to http://127.0.0.1:$port
EOF
