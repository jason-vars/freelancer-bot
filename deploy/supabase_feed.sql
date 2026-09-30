-- Shared job feed for BOT_FEED_MODE=publish/subscribe (see bot/feed.py).
-- Run once in Supabase: Dashboard > SQL Editor > New query > paste > Run.

create table if not exists public.feed_projects (
  id            bigint primary key,                 -- Freelancer project id
  seq           bigserial not null unique,          -- subscribers' read cursor
  published_at  timestamptz not null default now(),
  project       jsonb not null,                     -- the collector's project row
  client_status jsonb                               -- the client lookup, done once
);

create index if not exists feed_projects_published_at on public.feed_projects (published_at);

-- Row Level Security: the anon key (subscribers) may only READ. The service_role
-- key (publisher) bypasses RLS, so it can insert and prune.
alter table public.feed_projects enable row level security;

drop policy if exists "feed read" on public.feed_projects;
create policy "feed read" on public.feed_projects
  for select to anon, authenticated using (true);
