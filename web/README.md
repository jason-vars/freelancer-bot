# Freelancer Bid Bot — web app (Vercel + Supabase)

Multi-user version of the bot. One Freelancer token fetches jobs for everyone; each
user signs in, sees the shared job list with **their own** Opened / Applied state, and
writes proposals with **their own** AI proposal and bid settings.

```
 Python worker (your server)          Supabase                      Vercel (this app)
 python -m bot.cli run-loop  ──jobs──▶ jobs, worker_status  ◀──────  Jobs / Settings / Admin pages
   reads admin settings  ◀──────────── app_settings         ◀──────  Admin › Bot settings
   sends Telegram alerts (admin)       profiles, user_jobs  ◀──────  userscript API (/api/us/…)
                                       user_settings
```

* **Users** manage *AI proposal* and *Bid defaults* (My settings) and use the userscript.
* **Admin** approves users and manages everything else: search keywords, filters, fetch
  interval and hours, alerts, the OpenAI key/model and the Freelancer token.
* The fetching loop can't run on Vercel (Hobby plan cron runs once a day), so it stays a
  long-running Python process, the existing `run-loop`, on any always-on machine.

## 1. Supabase

1. Create a project at [supabase.com](https://supabase.com) (the free tier is enough).
2. **SQL Editor › New query**: paste [`../supabase/schema.sql`](../supabase/schema.sql) and run it.
3. **Project Settings › API Keys**: note the project URL, the **publishable** key and a
   **secret** key.
4. **Authentication › URL Configuration**: set *Site URL* to your Vercel URL and add
   `https://<your-app>.vercel.app/auth/callback` to *Redirect URLs*.
   (Optional: **Authentication › Sign In / Providers › Email**: turn off "Confirm email".
   Admin approval already controls who gets in.)

## 2. Vercel

1. **Add New › Project**, import this GitHub repo.
2. **Root Directory: `web`**. Leave "Include files outside the root directory" on: the build
   embeds `../userscript/` so the site can serve the userscript.
3. Environment variables:

   | Name | Value |
   |---|---|
   | `NEXT_PUBLIC_SUPABASE_URL` | `https://<project>.supabase.co` |
   | `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | the publishable key (`sb_publishable_…`) |
   | `SUPABASE_SECRET_KEY` | a secret key (`sb_secret_…`). Server-only, never `NEXT_PUBLIC_` |

4. Deploy.

## 3. Become admin

Register on the site, then run once in the Supabase SQL Editor:

```sql
update public.profiles set role = 'admin', status = 'approved', approved_at = now()
  where email = 'you@example.com';
```

Then open **Admin › Bot settings** and fill in the Freelancer token, OpenAI key, keywords,
filters, Telegram, and the fetch interval and hours. New users show up on **Admin**,
waiting for approval.

## 4. Job worker

On the always-on machine, using the existing Python setup (see the top-level README and
[`../deploy/README.md`](../deploy/README.md)), add to that checkout's `.env`:

```
CLOUD_SUPABASE_URL=https://<project>.supabase.co
CLOUD_SUPABASE_KEY=sb_secret_...
```

and run the poller as before:

```bash
python -m bot.cli run-loop            # or the freelancer-poll systemd service
python -m bot.cli telegram-listen     # optional: "Mark read" buttons on alerts
```

Each cycle the worker loads the admin's settings from Supabase (they override its `.env`),
fetches and filters jobs, alerts the admin's Telegram/Slack, and copies every stored job to
Supabase. The Admin page shows its last heartbeat and has a **Fetch now** button. Only
jobs found after the worker starts in cloud mode are copied.

## 5. Userscript (each user)

On **My settings**: install the userscript from the link shown (it points at this site),
create an API key, then on a freelancer.com project page open the Tampermonkey menu and choose
**Set bot API key**. The userscript generates the proposal with that user's settings, fills
the bid form, and marks the job Opened / Applied on their Jobs page.

## Local development

```bash
cp .env.example .env.local   # fill in the three Supabase values
npm install
npm run dev                  # http://localhost:3000
```

The proposal generator in `src/lib/proposal.ts` is a port of `bot/proposal_ai.py`; change
both together.
