# Freelancer Bid Bot — web app (Vercel + Supabase)

Multi-user version of the bot. One Freelancer token fetches jobs for everyone; each
user signs in, sees the shared job list with **their own** Opened / Applied state, and
writes proposals with **their own** AI proposal and bid settings.

```
 fetch-jobs Edge Function             Supabase                      Vercel (this app)
 (pg_cron, every 30 s)      ──jobs──▶ jobs, worker_status  ◀──────  Jobs / Settings / Admin pages
   reads admin settings  ◀──────────── app_settings         ◀──────  Admin › Bot settings
   sends Telegram alerts (admin)       profiles, user_jobs  ◀──────  userscript API (/api/us/…)
                                       user_settings        ──Realtime──▶ Jobs page updates live
```

* **Users** manage *AI proposal* and *Bid defaults* (My settings) and use the userscript.
* **Admin** approves users and manages everything else: search keywords, filters, fetch
  interval and hours, alerts, the OpenAI key/model and the Freelancer token.
* Jobs are fetched by a Supabase Edge Function that pg_cron calls every 30 seconds, so no
  always-on machine is needed. (Vercel's Hobby cron runs only once a day.) The Python
  `run-loop` still works as an alternative; never run both.

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

## 4. Job fetcher

The fetcher is [`../supabase/functions/fetch-jobs`](../supabase/functions/fetch-jobs/index.ts),
a port of the Python worker's fetch → filter → score → alert cycle. It reads the admin's
settings (Freelancer token, keywords, filters, Telegram/Slack) from **Admin › Bot settings**.

1. **Re-run [`../supabase/schema.sql`](../supabase/schema.sql)** in the SQL Editor (adds the
   fetcher's tables and turns on Realtime for `jobs`).
2. **Pick a secret**, any long random string, e.g. `openssl rand -hex 32`.
3. **Deploy the function**, either way:
   * CLI, from the repo root:
     ```bash
     npx supabase login
     npx supabase link --project-ref <project-ref>
     npx supabase secrets set FETCH_JOBS_SECRET=<your secret>
     npx supabase functions deploy fetch-jobs --no-verify-jwt
     ```
   * Dashboard: **Edge Functions › Deploy a new function › Via editor**, name it
     `fetch-jobs`, paste `index.ts`, deploy. Then in its **Details** turn **off**
     "Verify JWT", and under **Edge Functions › Secrets** add `FETCH_JOBS_SECRET`.
4. **Schedule it**: open [`../supabase/fetcher_cron.sql`](../supabase/fetcher_cron.sql), put in
   your project URL and the same secret, and run it in the SQL Editor.
5. In **Admin › Bot settings**, set **Fetch every (seconds)** to `30` and make sure the
   Freelancer token, keywords and Telegram settings are filled in. (The function reads
   only these settings, not a `.env`.) Leave **Timezone** blank for UTC.
6. **Stop the Python worker** (`run-loop` / the `freelancer-poll` service). Running both
   sends every alert twice.

Within a minute the Admin page should show a fresh heartbeat ("Stored N new job(s),
alerted M."). To debug: **Edge Functions › fetch-jobs › Logs**, and
`select * from cron.job_run_details order by start_time desc limit 5;`.

Jobs posted before the fetcher started (or while it was paused or down) are stored
but not alerted, the same as the Python worker after a restart.

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
