# Automatic Telegram notifications

> Running **several Freelancer accounts** on one always-on server? See
> [Several accounts on one server](#several-accounts-on-one-server) at the end.


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


## Several accounts on one server

One code checkout, one systemd instance per Freelancer account. Each account keeps
its own `.env`, its own `bot.sqlite3` and its own web UI port, so filters, proposals,
Telegram chat and bid history never mix.

**A Freelancer token belongs to one account** — there is no way to bid for several
accounts with one token. Each account authorises your app once and gets its own
token. What you share is the app (one Client ID/Secret) and, if you want, the
OpenAI key.

### Layout

```
/opt/freelancer-bot/app/                     the repo + .venv (shared code)
/opt/freelancer-bot/accounts/jaison/.env     account settings + tokens  (port 8765)
/opt/freelancer-bot/accounts/jaison/bot.sqlite3
/opt/freelancer-bot/accounts/user2/.env                                 (port 8766)
```

### Install

```bash
sudo useradd -r -m -d /opt/freelancer-bot botuser
sudo -u botuser git clone <your-repo> /opt/freelancer-bot/app
cd /opt/freelancer-bot/app
sudo -u botuser python3 -m venv .venv
sudo -u botuser .venv/bin/pip install -r requirements.txt
sudo mkdir -p /opt/freelancer-bot/accounts

sudo cp deploy/freelancer-bot@.service /etc/systemd/system/
sudo systemctl daemon-reload
```

### Add an account

```bash
sudo deploy/new-account.sh jaison        # creates the dir, copies .env.example, picks a free port
sudoedit /opt/freelancer-bot/accounts/jaison/.env
sudo systemctl enable --now freelancer-bot@jaison
journalctl -u freelancer-bot@jaison -f
```

Repeat per account. `systemctl … freelancer-bot@user2` is a completely separate
instance; stopping or restarting one never touches the others.

### Reaching the settings page

The web UI has **no login and displays the account's tokens**, so the unit binds it
to `127.0.0.1`. Tunnel in from your own machine instead of opening the port:

```bash
ssh -L 8765:127.0.0.1:8765 you@your-server     # then http://127.0.0.1:8765
```

The browser userscript talks to `http://127.0.0.1:8765` too, so with the tunnel open
it keeps working unchanged against the server-side bot.

**Never** put this UI on a public port. Anyone who reaches it can read your
Freelancer token, your OpenAI key and your Telegram token.

### Two things to watch

- **Shared IP.** Every account bids from the server's one IP, which is how platforms
  spot linked accounts — and a ban usually hits all of them. Fine for accounts that
  are genuinely yours to run; for several *different people*, give each instance its
  own outbound proxy or run it on their own machine.
- **One Telegram bot per account.** Two `getUpdates` pollers on the same bot token
  conflict, so give each account its own bot from @BotFather (or at least its own
  chat id and only one listener).

### Why `BOT_ENV_FILE` is set in the unit

`bot.sqlite3` and `bot.pid` are resolved relative to the working directory, so those
separate themselves. Settings do not: without `BOT_ENV_FILE` the bot finds the
`.env` next to the shared code checkout, and the Settings page would write there —
every account editing the same file. The unit sets it per instance.

### Shared job feed: one Freelancer token for job searching

By default every account searches Freelancer and looks up every client with its own
token, so N accounts make N times the API calls for the same jobs. With the shared
feed, one account does the searching and the rest read the results from Supabase.

1. Create a free Supabase project, open **SQL Editor**, and run
   [`supabase_feed.sql`](supabase_feed.sql). It creates the `feed_projects` table,
   readable with the anon key and writable only with the service_role key.
2. Pick **one** account as the publisher. In its Settings > **Shared job feed**:
   - Feed mode: `publish`
   - Feed keywords: every account's Keywords combined (subscribers only ever see
     jobs this search returns)
   - Supabase URL and the **service_role** key (Project Settings > API)
3. Every other account: Feed mode `subscribe`, the same URL, and the **anon** key.

Each subscriber still applies its own Keywords, filters, score and notification
settings to the feed, and still bids and generates proposals with its own
Freelancer token. Only the job search and client lookups are shared.

If Supabase is unreachable, the publisher keeps what it couldn't send and retries
on the next poll. Subscribers simply read nothing until it's back. Rows older than
3 days are pruned automatically.
