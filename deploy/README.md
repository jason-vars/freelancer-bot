# Automatic Telegram notifications

Two ways to run, both notify **only for "great" projects** (pass all filters and
score >= BOT_MIN_SCORE); INR/stale/rejected projects are processed silently.

- **Polling** (`run-loop`) — self-contained: the bot fetches new projects on a
  timer (default 30s) and notifies. No public URL or external feeder needed.
  Recommended unless you have something pushing projects to you.
- **Webhook** (`webhook-listen`) — instant, but only fires when an external
  feeder POSTs a project to `/webhook`. Needs a public, reachable port.

## Prerequisites (both modes)

In `.env`:

```
FLN_OAUTH_TOKEN=...           # required
TELEGRAM_BOT_TOKEN=...        # from @BotFather
TELEGRAM_CHAT_ID=...          # your chat id (e.g. from @userinfobot)
BOT_DRY_RUN=1                 # keep =1 so it only notifies + saves drafts (no real bids)
```

Verify Telegram first:

```bash
source .venv/bin/activate
python -m bot.cli test-telegram
```

## A) Polling — recommended, self-contained

Run manually:

```bash
source .venv/bin/activate
python -m bot.cli run-loop --interval-seconds 30
```

Run as a background service (survives reboot):

```bash
sudo cp deploy/freelancer-poll.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now freelancer-poll
sudo systemctl status freelancer-poll     # active (running)
journalctl -u freelancer-poll -f          # live logs
```

Tune the interval by editing `--interval-seconds` in the unit (lower = more
instant, but more API calls). After editing: `sudo systemctl daemon-reload &&
sudo systemctl restart freelancer-poll`.

### Mark-read button (optional, runs alongside either mode)

Each alert carries an inline **✅ Mark read** button. A separate listener process
long-polls Telegram for the button press and edits the message to mark it read.
The poller/webhook still works without it — taps just queue until it is running.

Run manually:

```bash
source .venv/bin/activate
python -m bot.cli telegram-listen
```

Run as a background service (alongside `freelancer-poll`):

```bash
sudo cp deploy/freelancer-telegram-listen.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now freelancer-telegram-listen
journalctl -u freelancer-telegram-listen -f   # live logs
```

Note: this uses Telegram's `getUpdates`, which is mutually exclusive with setting
a Telegram-side bot webhook. This bot doesn't set one, so it's fine — but don't
run two `telegram-listen` processes against the same bot token at once.

## B) Webhook — instant, needs an external feeder

The `webhook-listen` server receives Freelancer project payloads via HTTP POST
and notifies for each great project.

## Prerequisites

In the project's `.env` set:

```
FLN_OAUTH_TOKEN=...           # required
TELEGRAM_BOT_TOKEN=...        # from @BotFather
TELEGRAM_CHAT_ID=...          # your chat id (e.g. from @userinfobot)
# WEBHOOK_SECRET=some-secret  # optional; if set, callers must send header X-Webhook-Secret
```

Verify Telegram works first:

```bash
source .venv/bin/activate
python -m bot.cli test-telegram
```

## Run it manually (quick test)

```bash
source .venv/bin/activate
python -m bot.cli webhook-listen --host 0.0.0.0 --port 8080
# Listens on http://<server>:8080/webhook
```

Your webhook source (Freelancer integration / forwarder) must POST the project
JSON to `http://<server-ip>:8080/webhook`. If `WEBHOOK_SECRET` is set, include
the header `X-Webhook-Secret: <secret>`.

Send a test payload locally:

```bash
curl -X POST http://localhost:8080/webhook \
  -H 'Content-Type: application/json' \
  --data @sample_webhook.json
```

## Run it as a background service (survives reboot)

```bash
# adjust paths/User inside the unit file first if your layout differs
sudo cp deploy/freelancer-webhook.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now freelancer-webhook
sudo systemctl status freelancer-webhook      # check it's running
journalctl -u freelancer-webhook -f           # live logs
```

Open the port if a firewall is on: `sudo ufw allow 8080/tcp`.
