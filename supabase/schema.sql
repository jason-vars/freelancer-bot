-- Freelancer Bot — multi-user web app schema.
-- Run once in Supabase: Dashboard > SQL Editor > New query > paste > Run.
-- Safe to re-run: every statement is idempotent.
--
-- Who writes what:
--   * the job fetcher (supabase/functions/fetch-jobs, or the older Python worker;
--     secret key, bypasses RLS) writes `jobs` and `worker_status` and reads `app_settings`;
--   * the web app writes everything else, as the signed-in user (RLS applies) or,
--     for the userscript API, with the secret key after checking the user's API key.

-- ── Profiles: one per auth user; new sign-ups wait for admin approval ─────────────
create table if not exists public.profiles (
  id           uuid primary key references auth.users (id) on delete cascade,
  email        text not null,
  full_name    text not null default '',
  role         text not null default 'user' check (role in ('user', 'admin')),
  status       text not null default 'pending' check (status in ('pending', 'approved', 'rejected')),
  -- Userscript API key: only its SHA-256 is stored; the key itself is shown once.
  api_key_hash   text unique,
  api_key_prefix text,
  created_at   timestamptz not null default now(),
  approved_at  timestamptz
);

create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, email, full_name)
  values (new.id, coalesce(new.email, ''), coalesce(new.raw_user_meta_data ->> 'full_name', ''))
  on conflict (id) do nothing;
  return new;
end $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- Role checks used by the policies. SECURITY DEFINER so they can read profiles
-- without recursing into profiles' own RLS.
create or replace function public.is_admin()
returns boolean language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from public.profiles
    where id = auth.uid() and role = 'admin' and status = 'approved'
  );
$$;

create or replace function public.is_approved()
returns boolean language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from public.profiles where id = auth.uid() and status = 'approved'
  );
$$;

-- ── Settings ──────────────────────────────────────────────────────────────────────
-- Admin settings, keyed by the same names the Python bot reads from .env
-- (BOT_KEYWORDS, OPENAI_API_KEY, ...). The worker overlays these on its .env each
-- polling cycle. Holds secrets, so admins only.
create table if not exists public.app_settings (
  key         text primary key,
  value       text not null default '',
  updated_at  timestamptz not null default now()
);

-- Per-user proposal + bid settings (same key names). A key missing here falls back
-- to the admin's value in app_settings, then to the built-in default.
create table if not exists public.user_settings (
  user_id     uuid primary key references public.profiles (id) on delete cascade,
  "values"    jsonb not null default '{}'::jsonb,
  updated_at  timestamptz not null default now()
);

-- ── Jobs: fetched once by the worker, shared by everyone ─────────────────────────
create table if not exists public.jobs (
  id             bigint primary key,           -- Freelancer project id
  title          text not null default '',
  url            text,                         -- seo path, e.g. "python/scrape-site"
  description    text,
  currency       text,
  budget_min     numeric,
  budget_max     numeric,
  bid_count      integer,
  bid_avg        numeric,
  skills         text,
  posted_at      timestamptz,
  bidperiod      integer,
  upgrades       jsonb,
  score          integer not null default 0,
  -- Worker status: new / alerted / notify_skipped / skipped (below min score) /
  -- filtered (with filter_reason) / external (only opened via the userscript).
  status         text not null default 'new',
  filter_reason  text,
  synced_at      timestamptz not null default now()
);

-- "fixed" | "hourly" (null = unknown, treated as fixed). Hourly jobs bid the user's
-- hourly rate instead of the fixed-price bid table. Added after launch, so it is an
-- ALTER to stay safe to re-run on an existing database.
alter table public.jobs add column if not exists project_type text;

create index if not exists jobs_posted_at on public.jobs (posted_at desc);
create index if not exists jobs_synced_at on public.jobs (synced_at desc);
create index if not exists jobs_status on public.jobs (status);
create index if not exists jobs_url on public.jobs (url);

-- ── Per-user job state: opened / applied / last generated proposal ──────────────
create table if not exists public.user_jobs (
  user_id       uuid not null references public.profiles (id) on delete cascade,
  job_id        bigint not null references public.jobs (id) on delete cascade,
  opened_at     timestamptz,
  applied_at    timestamptz,
  proposal      text,
  amount        numeric,
  period_days   integer,
  generated_at  timestamptz,
  primary key (user_id, job_id)
);

create index if not exists user_jobs_job on public.user_jobs (job_id);

-- "Mark skipped" on the Jobs page: the user decided not to bid. Added after launch.
alter table public.user_jobs add column if not exists skipped_at timestamptz;

-- ── Shared "bad job" marks: one user flags a job, everyone skips it ─────────────
create table if not exists public.job_flags (
  job_id      bigint not null references public.jobs (id) on delete cascade,
  user_id     uuid not null references public.profiles (id) on delete cascade,
  reason      text not null default '',
  created_at  timestamptz not null default now(),
  primary key (job_id, user_id)
);
create index if not exists job_flags_created on public.job_flags (created_at desc);

-- ── Personal job filters (My settings › My job filters) ─────────────────────────
-- Applied on top of the admin's filters, which the worker already ran. Stored in
-- user_settings.values under the MY_* keys; must match userFilterReason() in
-- web/src/lib/jobs.ts, which applies the same rules to the userscript.

-- "a, B ,,c" -> {a,b,c}: lower-cased, trimmed, blanks dropped.
create or replace function public.csv_list(s text)
returns text[] language sql immutable as $$
  select coalesce(array_agg(lower(trim(x))) filter (where trim(x) <> ''), '{}')
  from unnest(string_to_array(coalesce(s, ''), ',')) as x;
$$;

-- Why the user's own filters hide this job, or null when it passes them.
create or replace function public.user_filter_reason(j public.jobs, v jsonb)
returns text language plpgsql stable as $$
declare
  kw text;
  title text := lower(coalesce(j.title, ''));
  descr text := lower(coalesce(j.description, ''));
  skills text := lower(coalesce(j.skills, ''));
  budget numeric := coalesce(j.budget_max, j.budget_min);
  wanted text[];
begin
  if v is null then return null; end if;
  wanted := public.csv_list(v ->> 'MY_KEYWORDS');
  if cardinality(wanted) > 0 and not exists (
       select 1 from unnest(wanted) k where position(k in title || ' ' || descr || ' ' || skills) > 0) then
    return 'none of my keywords';
  end if;
  foreach kw in array public.csv_list(v ->> 'MY_EXCLUDE_TITLE') loop
    if position(kw in title) > 0 then return 'title has "' || kw || '"'; end if;
  end loop;
  foreach kw in array public.csv_list(v ->> 'MY_EXCLUDE_DESC') loop
    if position(kw in descr) > 0 then return 'description has "' || kw || '"'; end if;
  end loop;
  foreach kw in array public.csv_list(v ->> 'MY_EXCLUDE_SKILLS') loop
    if position(kw in skills) > 0 then return 'skill "' || kw || '"'; end if;
  end loop;
  if lower(coalesce(j.currency, '')) = any (public.csv_list(v ->> 'MY_SKIP_CURRENCIES')) then
    return 'currency ' || j.currency;
  end if;
  if (v ->> 'MY_MIN_BUDGET') ~ '^\d+$' and (v ->> 'MY_MIN_BUDGET')::numeric > 0
     and budget is not null and budget < (v ->> 'MY_MIN_BUDGET')::numeric then
    return 'budget below ' || (v ->> 'MY_MIN_BUDGET');
  end if;
  if (v ->> 'MY_MAX_BIDS') ~ '^\d+$' and (v ->> 'MY_MAX_BIDS')::int > 0
     and coalesce(j.bid_count, 0) > (v ->> 'MY_MAX_BIDS')::int then
    return 'more than ' || (v ->> 'MY_MAX_BIDS') || ' bids';
  end if;
  return null;
end $$;

-- Jobs joined with the CURRENT user's state and personal filter verdict.
-- security_invoker makes the view obey the caller's RLS on every table.
-- Dropped first: `j.*` grows when jobs gains a column, and "create or replace"
-- refuses to change a view's existing columns.
drop view if exists public.my_jobs;
create view public.my_jobs with (security_invoker = true) as
  select j.*, uj.opened_at, uj.applied_at, uj.proposal, uj.amount, uj.period_days, uj.generated_at, uj.skipped_at,
         public.user_filter_reason(j, us."values") as my_filter_reason,
         coalesce(fl.bad_count, 0) as bad_count,
         coalesce(fl.bad_by_me, false) as bad_by_me,
         fl.bad_reasons
  from public.jobs j
  left join public.user_jobs uj on uj.job_id = j.id and uj.user_id = auth.uid()
  left join public.user_settings us on us.user_id = auth.uid()
  left join lateral (
    select count(*) as bad_count,
           bool_or(f.user_id = auth.uid()) as bad_by_me,
           string_agg(distinct nullif(trim(f.reason), ''), '; ') as bad_reasons
    from public.job_flags f where f.job_id = j.id
  ) fl on true;

-- Admin view: how many users opened / applied to each job.
create or replace view public.job_activity with (security_invoker = true) as
  select job_id,
         count(opened_at)  as opened_count,
         count(applied_at) as applied_count
  from public.user_jobs
  group by job_id;

-- ── Worker heartbeat, shown on the admin page ────────────────────────────────────
create table if not exists public.worker_status (
  id          smallint primary key default 1 check (id = 1),
  last_cycle_at timestamptz,
  message     text not null default '',
  -- Set by the admin's "Fetch now" button; the worker runs a cycle early and clears it.
  run_requested_at timestamptz
);
insert into public.worker_status (id) values (1) on conflict (id) do nothing;

-- ── Cloud fetcher (supabase/functions/fetch-jobs, run by pg_cron) ────────────────
-- lock_until: the running cycle's lock, so overlapping cron calls can't alert twice.
-- alert_floor_at: jobs posted before this are never alerted (reset after a pause/gap).
alter table public.worker_status add column if not exists lock_until timestamptz;
alter table public.worker_status add column if not exists alert_floor_at timestamptz;
alter table public.worker_status add column if not exists last_fetch_at timestamptz;
-- When the last cycle STARTED: "Fetch every" counts from here, so a slow cycle
-- doesn't push the next one back a whole cron tick.
alter table public.worker_status add column if not exists last_started_at timestamptz;

-- Projects the fetcher already evaluated, including ones it didn't store (skipped
-- currency, too old). Pruned after a few days. Secret key only: RLS, no policies.
create table if not exists public.fetch_seen (
  id       bigint primary key,
  seen_at  timestamptz not null default now()
);
create index if not exists fetch_seen_at on public.fetch_seen (seen_at);
alter table public.fetch_seen enable row level security;

-- Claims the next cycle: returns the worker_status row when this call may run (no
-- other run holds the lock, and "Fetch every" has passed or "Fetch now" was pressed),
-- else nothing.
create or replace function public.begin_fetch_cycle(p_min_interval int, p_lease int)
returns setof public.worker_status language sql security definer set search_path = public as $$
  update public.worker_status
     set lock_until = now() + make_interval(secs => p_lease), run_requested_at = null,
         last_started_at = now()
   where id = 1
     and (lock_until is null or lock_until < now())
     and (run_requested_at is not null or last_started_at is null
          or last_started_at <= now() - make_interval(secs => greatest(p_min_interval - 5, 0)))
  returning *;
$$;
revoke execute on function public.begin_fetch_cycle(int, int) from public, anon, authenticated;
grant execute on function public.begin_fetch_cycle(int, int) to service_role;

-- ── Row Level Security ───────────────────────────────────────────────────────────
alter table public.profiles      enable row level security;
alter table public.app_settings  enable row level security;
alter table public.user_settings enable row level security;
alter table public.jobs          enable row level security;
alter table public.user_jobs     enable row level security;
alter table public.worker_status enable row level security;
alter table public.job_flags     enable row level security;

drop policy if exists "profiles: read own or admin" on public.profiles;
create policy "profiles: read own or admin" on public.profiles
  for select to authenticated using (id = auth.uid() or public.is_admin());

-- Only admins change role / approval. Users never update their own row directly
-- (the API key is set through the server with the secret key).
drop policy if exists "profiles: admin update" on public.profiles;
create policy "profiles: admin update" on public.profiles
  for update to authenticated using (public.is_admin()) with check (public.is_admin());

drop policy if exists "profiles: admin delete" on public.profiles;
create policy "profiles: admin delete" on public.profiles
  for delete to authenticated using (public.is_admin());

drop policy if exists "app_settings: admin" on public.app_settings;
create policy "app_settings: admin" on public.app_settings
  for all to authenticated using (public.is_admin()) with check (public.is_admin());

drop policy if exists "user_settings: own" on public.user_settings;
create policy "user_settings: own" on public.user_settings
  for all to authenticated
  using (user_id = auth.uid() and public.is_approved())
  with check (user_id = auth.uid() and public.is_approved());

drop policy if exists "user_settings: admin read" on public.user_settings;
create policy "user_settings: admin read" on public.user_settings
  for select to authenticated using (public.is_admin());

drop policy if exists "jobs: approved read" on public.jobs;
create policy "jobs: approved read" on public.jobs
  for select to authenticated using (public.is_approved());

drop policy if exists "user_jobs: own" on public.user_jobs;
create policy "user_jobs: own" on public.user_jobs
  for all to authenticated
  using (user_id = auth.uid() and public.is_approved())
  with check (user_id = auth.uid() and public.is_approved());

drop policy if exists "user_jobs: admin read" on public.user_jobs;
create policy "user_jobs: admin read" on public.user_jobs
  for select to authenticated using (public.is_admin());

-- Bad marks are shared: every approved user sees all of them, adds or changes only
-- their own, and admins can clear anyone's.
drop policy if exists "job_flags: approved read" on public.job_flags;
create policy "job_flags: approved read" on public.job_flags
  for select to authenticated using (public.is_approved());

drop policy if exists "job_flags: own insert" on public.job_flags;
create policy "job_flags: own insert" on public.job_flags
  for insert to authenticated with check (user_id = auth.uid() and public.is_approved());

drop policy if exists "job_flags: own update" on public.job_flags;
create policy "job_flags: own update" on public.job_flags
  for update to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());

drop policy if exists "job_flags: own or admin delete" on public.job_flags;
create policy "job_flags: own or admin delete" on public.job_flags
  for delete to authenticated using (user_id = auth.uid() or public.is_admin());

-- ── Realtime: the Jobs page subscribes to new jobs and bad marks so they show for
-- everyone at once. Realtime still applies the select policies above. Guarded so
-- re-running is safe.
do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'job_flags'
  ) then
    alter publication supabase_realtime add table public.job_flags;
  end if;
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'jobs'
  ) then
    alter publication supabase_realtime add table public.jobs;
  end if;
  -- Own opened/applied changes (e.g. from the userscript) move jobs between tabs live.
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'user_jobs'
  ) then
    alter publication supabase_realtime add table public.user_jobs;
  end if;
end $$;

drop policy if exists "worker_status: admin" on public.worker_status;
create policy "worker_status: admin" on public.worker_status
  for all to authenticated using (public.is_admin()) with check (public.is_admin());

-- ── Make yourself admin (run once, after you register in the web app) ────────────
-- update public.profiles set role = 'admin', status = 'approved', approved_at = now()
--   where email = 'you@example.com';
