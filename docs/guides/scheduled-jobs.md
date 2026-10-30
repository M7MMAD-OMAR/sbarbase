[العربية](scheduled-jobs.ar.md)

# Scheduled jobs

Scheduled jobs let an environment run SQL on a schedule with `cron.schedule()`, and call other services from the database with `net.http_get()`, `net.http_post()` and `net.http_delete()`, as with Supabase Cron and pg_net. Each environment turns them on for itself. Not production ready: checked in disposable containers, not yet on a real server.

## Turn them on

1. Open the environment in the console, then **Services**.
2. Open **Scheduled jobs** in the side list and turn its switch on. It shows **Starting…**, then **On**, usually within seconds.

Every member can read the [Services page](services.md); only owners and admins can switch a service. If the server has no database connections to spare, the section says so and nothing changes.

Jobs run as the environment's developer role, the one [direct database access](database-access.md) signs in with. That role exists once database access was turned on once; until then every run is recorded as failed with "The developer role does not exist yet; turn database access on once". Direct access may be off afterwards: jobs keep running.

## Use them

Your migrations and SQL work unchanged:

```sql
select cron.schedule('nightly-cleanup', '0 3 * * *', $$delete from public.events where created_at < now() - interval '30 days'$$);

select cron.schedule('app-maintenance', '* * * * *', $$
  select net.http_post(
    url := (select decrypted_secret from vault.decrypted_secrets where name = 'project_url') || '/functions/v1/maintenance',
    headers := jsonb_build_object('apikey', (select decrypted_secret from vault.decrypted_secrets where name = 'service_key')),
    body := '{}'::jsonb, timeout_milliseconds := 25000)
$$);

select jobid, jobname, schedule, active from cron.job;
select status, return_message, start_time from cron.job_run_details order by start_time desc limit 20;
select cron.unschedule('nightly-cleanup');
```

- **Schedules:** five fields in GMT (`*/5 * * * *`, `0 3 * * mon-fri`, `0 0 $ * *` for the last day of the month), `@hourly`, `@daily`, `@weekly`, `@monthly`, `@yearly`, or `'30 seconds'` for 1 to 59 seconds.
- **Functions:** `cron.schedule`, `cron.schedule_in_database` (this database only), `cron.unschedule`, `cron.alter_job`, `net.http_get`, `net.http_post`, `net.http_delete`, with the same arguments and defaults as on Supabase. A request's answer is in `net._http_response`, under the id the call returned, for 6 hours.
- **History:** each run is a row in `cron.job_run_details` with `succeeded` or `failed` and the message, kept 7 days.
- **Triggers:** a trigger that calls `net.http_post` works whichever role writes the row (`anon`, `authenticated`, `service_role`), as a Supabase database webhook does. Only the developer and `service_role` read the answers.

To check for the feature in code, look for the function rather than the extension:

```sql
select to_regprocedure('cron.schedule(text,text,text)') is not null;
```

## Outgoing requests

Requests leave from the server, so they follow a stricter rule than pg_net's:

- Only `http` and `https`, on ports 80 and 443.
- Never to a loopback, private, link-local or other reserved address, and never to the server's own addresses or its Docker networks. A name that resolves to any such address is refused, and the connection goes to the address that was checked.
- The one way back to this server is its public address (`SBARBASE_PUBLIC_URL`, exact scheme, host and port), so a job can call the environment's own Edge Functions through the gateway like any client.
- At most 5 redirects, each checked again. At most 60 seconds per request, 1 MiB of answer kept.

A refused request gets an answer row with `error_msg` starting with "Refused by the server's outgoing request policy".

## What stays different

- `create extension pg_cron` and `create extension pg_net` still fail, and Studio's **Cron** page stays closed: it looks for the extension. Use SQL, as above.
- Jobs run as the developer role, not as `postgres`. The `username` column is recorded, not used.
- Each environment runs at most 2 jobs at once and keeps at most 100 jobs; a run is ended after 10 minutes. A run that falls due while the previous one still runs waits for it, once.
- Turning scheduled jobs off stops runs and requests and keeps the jobs and their history. While a [write pause](write-pause.md) holds the environment nothing runs.
- A recovery export refuses an environment while its scheduled jobs are on; turn them off first.

How it works, and why: [design note](../engineering/SCHEDULED-JOBS.md).
