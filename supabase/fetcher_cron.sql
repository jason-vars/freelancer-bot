-- Runs the fetch-jobs Edge Function every 30 seconds (replaces the Python run-loop).
-- Run once in Supabase: Dashboard > SQL Editor, AFTER schema.sql and after deploying
-- the function (see web/README.md, "4. Job fetcher").
--
-- The function itself waits for "Fetch every (seconds)" in Admin › Bot settings, so
-- set that to 30 to fetch every 30 seconds. "Fetch now" runs it on the next tick.

create extension if not exists pg_cron;
create extension if not exists pg_net;

-- 1. Store the project URL and the shared secret in Vault (once). Use the SAME
--    secret you set as the function's FETCH_JOBS_SECRET. To change one later:
--    select vault.update_secret((select id from vault.secrets where name = 'fetch_jobs_secret'), '<new>');
select vault.create_secret('https://<project-ref>.supabase.co', 'project_url');
select vault.create_secret('<the FETCH_JOBS_SECRET value>', 'fetch_jobs_secret');

-- 2. Schedule it (re-running replaces the old schedule).
select cron.unschedule('fetch-jobs') where exists (select 1 from cron.job where jobname = 'fetch-jobs');
select cron.schedule(
  'fetch-jobs',
  '30 seconds',
  $$
  select net.http_post(
    url     := (select decrypted_secret from vault.decrypted_secrets where name = 'project_url') || '/functions/v1/fetch-jobs',
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'x-fetch-secret', (select decrypted_secret from vault.decrypted_secrets where name = 'fetch_jobs_secret')
    ),
    body    := '{}'::jsonb
  );
  $$
);

-- Check it: select * from cron.job_run_details order by start_time desc limit 5;
-- Stop it:  select cron.unschedule('fetch-jobs');
