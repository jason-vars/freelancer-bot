-- Freelancer Bot — multi-user web app schema.
-- Run once in Supabase: Dashboard > SQL Editor > New query > paste > Run.
-- Safe to re-run: every statement is idempotent.
--
-- Who writes what:
--   * the Python worker (secret key, bypasses RLS) writes `jobs` and `worker_status`
--     and reads `app_settings`;
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

create index if not exists jobs_posted_at on public.jobs (posted_at desc);
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

-- Jobs joined with the CURRENT user's state. security_invoker makes the view
-- obey the callers' RLS on both tables.
create or replace view public.my_jobs with (security_invoker = true) as
  select j.*, uj.opened_at, uj.applied_at, uj.proposal, uj.amount, uj.period_days, uj.generated_at
  from public.jobs j
  left join public.user_jobs uj on uj.job_id = j.id and uj.user_id = auth.uid();

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

-- ── Row Level Security ───────────────────────────────────────────────────────────
alter table public.profiles      enable row level security;
alter table public.app_settings  enable row level security;
alter table public.user_settings enable row level security;
alter table public.jobs          enable row level security;
alter table public.user_jobs     enable row level security;
alter table public.worker_status enable row level security;

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

drop policy if exists "worker_status: admin" on public.worker_status;
create policy "worker_status: admin" on public.worker_status
  for all to authenticated using (public.is_admin()) with check (public.is_admin());

-- ── Make yourself admin (run once, after you register in the web app) ────────────
-- update public.profiles set role = 'admin', status = 'approved', approved_at = now()
--   where email = 'you@example.com';
