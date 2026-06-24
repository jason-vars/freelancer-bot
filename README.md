# Freelancer Bid Bot (API-first) — Python + AI proposals

This is a minimal, **official-API** workflow:

1. Search new projects via Freelancer API (Python SDK)
2. Score/filter them
3. Generate a proposal with AI (OpenAI Responses API example included)
4. Place a bid via Freelancer API
5. Store everything in SQLite for tracking

> ⚠️ You must follow Freelancer API Terms and rate limits, and set your own guardrails (max bids/day, min score, etc).

## Setup

### 1) Create a virtualenv and install deps
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate

pip install -r requirements.txt
```

### 2) Set environment variables
Copy `.env.example` to `.env` and fill it.

### 3) Run
Dry-run (no bids):
```bash
python -m bot.cli run --dry-run
```

Live bidding:
```bash
python -m bot.cli run
```

Webhook mode (one payload file):
```bash
python -m bot.cli webhook-once --payload-file sample_webhook.json --dry-run
```

Webhook mode (HTTP listener):
```bash
python -m bot.cli run-loop --dry-run --interval-seconds 120
python -m bot.cli webhook-listen --port 8080 --dry-run
# POST JSON to http://localhost:8080/webhook
```

Example webhook POST:
```bash
curl -X POST http://localhost:8080/webhook \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: YOUR_SECRET" \
  --data @sample_webhook.json
```

## Running on Windows

The bot is pure Python (no OS-specific dependencies), so it runs on Windows as-is.
Helper `.bat` launchers are included so you don't have to type commands:

| Script | What it does |
|--------|--------------|
| `setup.bat` | One-time: creates `.venv` and installs `requirements.txt`. Run first. |
| `run-loop.bat` | Starts the polling loop. Uses `BOT_POLL_INTERVAL_SECONDS` from `.env`. Ctrl+C / close window to stop. |
| `test-telegram.bat` | Sends a test Telegram message to verify `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`. |

**Steps**

1. Install **Python 3.10+** from [python.org](https://www.python.org/downloads/) and tick **"Add python.exe to PATH"**.
2. Copy the project folder over, **including your `.env`** (it holds your tokens).
3. Double-click **`setup.bat`**, then **`test-telegram.bat`**, then **`run-loop.bat`**.

Prefer the command line? The same commands work in PowerShell:
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1          # CMD: .venv\Scripts\activate.bat
pip install -r requirements.txt
python -m bot.cli run-loop          # interval comes from .env
```
If PowerShell blocks activation, run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

**Run persistently (auto-start, survives logout) — Task Scheduler**

- Create Task → Trigger: *At log on* (or *At startup*).
- Action: *Start a program* → Program/script: `C:\path\to\freelancer-bot\run-loop.bat`
- **Start in:** `C:\path\to\freelancer-bot` — required, so the relative `bot.sqlite3` path resolves to the project folder.
- Tick *Run whether user is logged on or not* for headless running.

**Windows gotchas**

- Always start from the project folder (the `.bat` files and Task Scheduler's *Start in* handle this) — the DB path `bot.sqlite3` is relative.
- The `.bat` files set `PYTHONUTF8=1` to avoid `UnicodeEncodeError` in older `cmd.exe` when printing the table/emoji to the console (Telegram rendering is unaffected either way).

## Files
- `bot/freelancer_client.py` — Freelancer SDK session helper
- `bot/collector.py` — search projects and store them
- `bot/scorer.py` — eligibility + scoring
- `bot/proposal_ai.py` — AI proposal generator (OpenAI example)
- `bot/bidder.py` — place bids
- `bot/db.py` — SQLite schema + helpers
- `bot/cli.py` — entrypoint

## Notes
- This uses SQLite for simplicity; swap to Postgres later.
- AI generation is optional; you can start with templates only.
- For webhook auth, set `WEBHOOK_SECRET` and send it in `X-Webhook-Secret`.
- Webhook flow is: receive project -> check client status -> wait `WEBHOOK_DELAY_SECONDS` -> generate AI proposal -> bid (or dry-run draft).


cd ~/10leksandr/freelancer-bot
git pull
source .venv/bin/activate

# 1) make sure .env has FLN_OAUTH_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, and BOT_DRY_RUN=1
python -m bot.cli test-telegram          # confirm Telegram works

# 2a) quick test in the foreground (Ctrl+C to stop)
python -m bot.cli run-loop --interval-seconds 30


sudo cp deploy/freelancer-poll.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now freelancer-poll      # starts now + on every boot
sudo systemctl status freelancer-poll            # should say: active (running)
journalctl -u freelancer-poll -f                 # watch it live
