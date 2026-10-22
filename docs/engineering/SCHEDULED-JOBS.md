# Scheduled jobs and outgoing HTTP

Scope: Supabase Cron (`cron.schedule`) and `net.http_*` for environments on a shared
server. Written 2026-10-21, corrected and implemented 2026-10-22. Code:
`lab/scheduled_jobs.py` (the schemas, the login, the grants), `Runtime.scheduled_jobs_turn`
in `lab/durable_runtime.py`, `src/scheduler/` (schedule parsing, the egress policy, one
runner per environment), `lab/scheduler.ts` (the process the supervisor runs),
`src/control/scheduled-jobs.ts` (the console API). Reader guide:
[Scheduled jobs](../guides/scheduled-jobs.md). Status: works in disposable containers
against the pinned images; not run on a real server.

## Why

A Supabase application commonly schedules its maintenance with pg_cron and reaches its
own Edge Functions with pg_net, keeping the key in Vault:

```sql
select cron.schedule('app-maintenance', '* * * * *', $$
  select net.http_post(
    url := (select decrypted_secret from vault.decrypted_secrets where name = 'project_url') || '/functions/v1/maintenance',
    headers := jsonb_build_object('apikey', (select decrypted_secret from vault.decrypted_secrets where name = 'service_key')),
    body := '{}'::jsonb, timeout_milliseconds := 25000)
$$);
```

On a shared server both extensions are refused (`RESERVED_EXTENSIONS`, and the `cron`
and `net` schema names are reserved), so such an application could not run its maintenance.

## Sources

Read on 2026-10-21 and 2026-10-22.

- pg_cron at tag v1.6.4 (https://github.com/citusdata/pg_cron): `src/entry.c`
  (`parse_cron_entry`, `get_range`, `get_number`), `src/job_metadata.c` (`ScheduleCronJob`,
  `ParseSchedule`, `TryParseInterval`, `MarkPendingRunsAsFailed`), `src/pg_cron.c`
  (`StartAllPendingRuns`, `StartPendingRuns`, `ShouldRunTask`), `include/cron.h` (the month
  and day names). README: one run of a job at a time, a second run is queued.
- pg_net at tag v0.20.4 (https://github.com/supabase/pg_net): `src/core.c`
  (`consume_request_queue`, `insert_response`, `CURLOPT_FOLLOWLOCATION`, http and https
  only), `src/worker.c` (`pg_net.ttl`, 6 hours).
- Measured in the pinned image `supabase/postgres:17.6.1.166`, with both extensions created
  in a disposable container: pg_cron 1.6.4, pg_net 0.20.4, `cron.database_name = postgres`,
  `cron.use_background_workers = off`, `cron.max_running_jobs = 32`, `cron.timezone = GMT`,
  `pg_net.database_name = postgres`, `pg_net.batch_size = 200`. The exact columns, function
  signatures, defaults and grants below are copied from `\d`, `pg_get_function_arguments`
  and `pg_get_functiondef` there.
- AWS RDS pg_cron guide, Neon pg_cron docs, Supabase Cron docs
  (https://supabase.com/docs/guides/cron), Supabase pg_net docs.
- IANA IPv4 and IPv6 special-purpose address registries, for the egress policy.

## Why not the real extensions

- **One database per cluster.** pg_cron's launcher and pg_net's worker each live in one
  database (`postgres` here). `CREATE EXTENSION pg_cron` anywhere else raises "can only
  create extension in database postgres". pg_net's README: multiple databases are not
  supported.
- **Cross-database scheduling is a privilege, not isolation.** `cron.schedule_in_database`
  lets any role with USAGE on `cron` schedule into every database where it has CONNECT.
  Jobs and their history live in `postgres`, so an environment's backup, restore and
  Studio never see them.
- **Running as the tenant needs HBA trust or background workers.** With
  `use_background_workers = off` pg_cron opens a libpq connection as the job's user, which
  needs a trust rule or a `.pgpass` per login; with it on, workers bypass HBA entirely.
- **Shared limits.** `cron.max_running_jobs` is cluster wide: one environment can starve
  the rest.
- **Version.** 1.6.4 predates 1.6.5's fix to the superuser check before a job enters the
  cache.
- **pg_net has no egress policy.** Its worker reaches loopback, the Docker networks and
  cloud metadata addresses: a request from one environment could reach another's
  services.
- Neon and RDS confirm the pattern: pg_cron works where one customer owns the cluster.

## Design

Both extensions stay refused. An environment whose owner turns **Scheduled jobs** on gets:

1. **A compatibility schema `cron`** in its own database: tables `cron.job` and
   `cron.job_run_details` with pg_cron 1.6.4's columns, and its functions with the exact
   signatures and defaults: `schedule(text,text)`, `schedule(text,text,text)` (by name,
   replacing the job of the same name and user), `schedule_in_database(text,text,text,text,
   text,boolean)` (only the current database, only the caller as user), `unschedule(bigint)`,
   `unschedule(text)`, `alter_job(bigint,text,text,text,text,boolean)`.
2. **A compatibility schema `net`**: `net.http_get(text,jsonb,jsonb,integer)`,
   `net.http_post(text,jsonb,jsonb,jsonb,integer)` and
   `net.http_delete(text,jsonb,jsonb,integer,jsonb)` with pg_net 0.20.4's defaults and its
   Content-Type rule for POST, the queue `net.http_request_queue`, the results
   `net._http_response`, and the types `net.http_method`, `net.request_status`,
   `net.http_response` and `net.http_response_result`.
3. **The scheduler** (`lab/scheduler.ts`), a supervisor child that never connects to a database
   itself and runs one process per environment (`--environment`, bound to it by
   `lab/parent_bound.py`), each with the runner that runs that environment's due jobs and
   performs its queued requests under an egress policy. Whatever tenant code makes a runner do
   (a key derivation, a burst of work, a crash) stays in that environment's process; the second
   review found a job could stall one shared process for every environment (below).

Applications and migrations that call `cron.schedule(...)` and `net.http_post(...)` work
unchanged. Jobs and their history are in the environment's own database, so they travel
with its backups. Turning Scheduled jobs off stops the runs and keeps the schemas and the
jobs; turning it on again picks them up.

### Who owns what

The platform's superuser creates both schemas, their tables, sequences, types and functions,
so tenant code cannot add a trigger, a rule or a policy to them, change a column, or replace a
function. **No function is SECURITY DEFINER**: each runs as its caller, with its search path
pinned to `pg_catalog, pg_temp`, and every name it uses qualified. A function the superuser owns
and a tenant calls therefore never acts with the superuser's rights.

The draft wanted a NOLOGIN owner role per environment and definer functions, so that the
developer could not write `cron.job` directly (on RDS that is how a job's user is rewritten).
That protects `username`, and here `username` decides nothing: **every job runs as the
environment's developer role**, whatever the row says. The column is recorded for display and
for pg_cron's uniqueness rule on `(jobname, username)`. So the developer may write `cron.job`
directly, as `postgres` may write `cron.job_run_details` on Supabase, and the extra role (one
more name in every role inventory) buys nothing.

Grants, as on Supabase where it matters to applications:

- `<e>_developer`: USAGE on both schemas; SELECT, INSERT, UPDATE, DELETE on `cron.job` and on
  `cron.job_run_details` (as `postgres` on Supabase); SELECT, INSERT, DELETE on the queue and on
  `net._http_response`; EXECUTE on the functions; the sequences. Everything `<e>_cron` holds on
  these schemas, so a job that runs `RESET ROLE` gains nothing (the first review found it could
  otherwise write its own history and plant responses, which the developer could not).
- `anon`, `authenticated`, `service_role`: USAGE on `net`, EXECUTE on `net.http_*` and INSERT on
  the queue, so a trigger that calls `net.http_post` works whichever role writes the row, as a
  Supabase database webhook does. Only `service_role` and the developer read responses. None
  of them reaches `cron`. Neither schema can be exposed through the Data API: both are reserved
  names (`lab/data_api.py`).
- `PUBLIC`: nothing. EXECUTE is revoked from PUBLIC on every function.

A CHECK constraint on `cron.job` refuses a schedule the parser refuses (`cron.schedule_valid`,
pure plpgsql), a command over 64 KiB and a job name over 1 KiB. A trigger refuses the 101st job.
The scheduler parses again and skips what it cannot read. The `database` column is recorded, not
used: the functions refuse another database, and a job runs in the database it is stored in. A
CHECK that `database` is the current database was tried and dropped: an in-place restore replays
the archive into a stage database of another name (`restore_cutover.py`), where every row would
fail it.

### Who runs a job

Never a superuser session: the reserved-schema guard skips superusers, and a superuser
session that only switched role could switch back with `RESET ROLE`.

A per-environment login `<e>_cron` (NOINHERIT, its password kept in the runtime file as
the environment's `cron` value, CONNECTION LIMIT 4, an HBA rule like the other scoped logins)
connects to the environment's database. It is a member of `<e>_developer` WITH INHERIT FALSE,
SET TRUE. For each run the scheduler opens a new connection, runs `SET ROLE <e>_developer`, applies
the developer's own role settings (`set_config`, never `role` or `session_authorization`), runs the
command, and closes the connection. Every connection of the login is the scheduler's own minimal
client of the simple query protocol (`src/scheduler/wire.ts`, SCRAM-SHA-256); for a run it keeps the
last command tag and the first error and drops rows, COPY data and notices as they stream in. The first review reproduced a
job returning 600 MB per run driving the shared scheduler process to 2 GB through Bun's driver,
which holds every row (about three times the result). The live check now runs a job returning 1 GB:
the process peaks below the result's size (socket buffers the engine has not collected yet; the
client prompts a collection every 32 MiB it drops) and is back within 128 MiB once collected. COPY in either direction fails the run with `COPY not supported`, as pg_cron
records it (the session is left, so the server stops sending), and `LISTEN`, which hung Bun's driver,
returns at once. A job that runs `RESET ROLE` falls back to
`<e>_cron`, which holds only the grants the scheduler needs on these two schemas, all of which
the developer has too, so it gains nothing.

The developer role exists once database access was turned on. If it does not, the run records
"The developer role does not exist yet; turn database access on once" and nothing else
happens. Turning database access off makes the developer NOLOGIN (`database_turn`); it does not
drop the role or its memberships, and the job's session user is `<e>_cron`, so jobs keep
running. Direct access and scheduled jobs are separate switches.

Turning Scheduled jobs on creates `<e>_cron` the first time (its password generated once and
kept), resets its attributes, password and role settings, writes the schemas and grants, and
opens it. Turning it off closes it (NOLOGIN), ends its sessions and **takes back every grant it
has in the environment's database**, but keeps the role, as Realtime's login is kept. So a dump
taken while Scheduled jobs are off names no such role and restores anywhere; a dump taken while
they are on names it, and the in-place restore (`restore_cutover.py`, which refuses an archive
whose grantee is missing) finds it because the role is never dropped. The draft dropped the role;
that would make every backup taken while Scheduled jobs were on unrestorable once they were
turned off.

A new scoped login has a known footprint, copied from the Realtime login:

| Where | What |
|---|---|
| `hba_content` | `host <e> <e>_cron 0.0.0.0/0 scram-sha-256` for every environment; the rule admits nobody while the role does not exist or is NOLOGIN. Turning on refuses until the rule is published ("restart Sbarbase once"), as Realtime does. |
| `cluster_inventory.py` | Recognised as `cron`, rule `_BASE` (no inherit, no bypass, no replication). |
| `cluster_rebuild.py` | A toggled login; its password is read from the runtime file, and without one it is rebuilt closed. |
| `write_pause.py` | Closed with the developer and Studio logins; reopened by `resume` when Scheduled jobs are on. A start (`scheduled_jobs_back`) leaves it closed while a pause is recorded. |
| `recovery-export.py` | An export refuses while the login can sign in or holds a grant on `cron` ("Scheduled jobs are off"): the export carries the three service logins only. |
| `connection_budget.py` | `SCHEDULER_CONNECTIONS = 4` added to the database limit while it is on; turning on refuses when it does not fit, as for Realtime. |
| `dev.py`, `realtime.py` | The console toggle runs in the same per-environment child as Realtime's; a turn on waits while a write pause is recorded. The scheduler is a supervisor child. |
| `lifecycle_shared.py`, `recovery_inventory.py`, `transfer_journal.py` | Unchanged. Their role identities come from a recovery receipt, which an export refuses to make while the login is in use; their allowlists refuse the login, so a move or purge that met it would fail closed. Deleting an environment does not reach them on an ordinary installation yet (CHANGELOG, Still open). |
| `import_inspect.py` | Unchanged: a Supabase project's pg_cron jobs live in its `postgres` database, which an import does not read. |

### Semantics, matched to pg_cron

- Five-field cron syntax in GMT, read by the same rules as `parse_cron_entry`: lists, ranges,
  steps after `*` or a range (`1/20` is refused, as in pg_cron), month and day names in any
  case, `7` for Sunday, `$` alone for the last day of the month, `@hourly`, `@daily`,
  `@midnight`, `@weekly`, `@monthly`, `@yearly`, `@annually`, and `N seconds` or `N second` for
  1 to 59 seconds. Day of month and day of week both restricted means either one; either one
  `*` means both. `@reboot` is refused here: a shared server has no reboot of the tenant's own.
- **Differences found in the source.** pg_cron 1.6.4's `@yearly` clears every month (it passes
  `FIRST_MINUTE` as the month), so a `@yearly` job never runs there; here it runs at 00:00 GMT
  on 1 January. pg_cron ignores text after the fifth field and after an unparsable character in
  a field; here such a schedule is refused. pg_cron reads numbers with `atoi` and `%u`, so it takes
  `+5 seconds` and values that wrap; here a number has at most 9 digits and no sign (so
  `0000000001 * * * *` is refused). pg_cron refuses `1 second ` with a blank after the singular;
  here it is accepted. The plpgsql check and the TypeScript parser agree on all of these.
- **A run that falls due while the previous run of the same job still runs waits, once.** The
  draft said it is skipped; pg_cron queues it (`pendingRunCount`) without limit. Here at most one
  run waits, as pg_cron does for second intervals, so a job slower than its schedule does not
  build an unbounded backlog.
- After a pause of one to five minutes (a slow loop) the minutes in between are looked at, as
  pg_cron's `CLOCK_PROGRESSED`; a job due in several of them runs once, since at most one run
  waits. Ticks missed while the scheduler, the server or the environment's database was away (a
  fence, a restore, a closed login) are not run later: a runner, and a runner whose session failed,
  starts again at the next whole minute. A clock that goes back runs nothing until it passes the
  last minute again; pg_cron's `MinutesPassed` reads a negative difference as 0, so its
  `CLOCK_JUMP_BACKWARD` branch is never taken there either.
- Each run writes one row to `cron.job_run_details`: `starting`, then `running` with the
  backend pid, then `succeeded` or `failed`, with the message pg_cron's `PollForTask` records:
  `N rows` (or `1 row`) when the last statement returned rows or was empty, its command tag
  otherwise (`INSERT 0 1`, `VACUUM`), and for an error the text libpq's `PQresultErrorMessage`
  gives at its default verbosity (`ERROR:  ...` with `DETAIL:` and `HINT:` lines; the statement
  position and `CONTEXT` lines are not added). The third review found the tag keyed on the command
  text instead, so `SELECT INTO` and `INSERT ... RETURNING` differed.
  A run the scheduler lost (a crash, a stop for an upgrade, the database restarting, or a row a
  restored database carried) is marked `failed` with `return_message = 'server restarted'` when the
  runner opens its session with no run in flight, as pg_cron's `MarkPendingRunsAsFailed` does; a
  backend such a run left alive (the scheduler killed outright) is terminated first.
- A command runs as one simple query, as pg_cron's `PQsendQuery`: several statements run in
  one implicit transaction unless the command commits itself, and `VACUUM` alone works.
- History is kept 7 days and pruned by the scheduler once a minute; pg_cron keeps it forever.
- `alter_job` with nothing to change raises `no updates specified`, and one for a job that is
  missing or another user's raises `Job N does not exist or you don't own it`, as pg_cron's
  `AlterJob`. `unschedule` raises pg_cron's `could not find valid entry for job`.

### When the scheduler must not write

The scheduler runs a job or performs a request for an environment only while:

- its `endpoints.json` entry says Scheduled jobs are on (`cron`), and
- no write pause is recorded for it (`.lab/upstream/write-pauses/<e>.json`), and
- its login can connect: a fence (`ALLOW_CONNECTIONS false`), a write pause (NOLOGIN) and an
  in-place restore (which fences the database while it replaces it, `restore_cutover.py`) all
  close it inside PostgreSQL itself, and the runner retries every few seconds without recording
  anything. When it connects again with no run of its own in flight, it marks any run the database
  still shows as running (one a restored backup carried, say) as failed, as at a start.

It is a supervisor child like the worker: when an upgrade or rollback drains the supervisor
(`paused()`), the scheduler is stopped before `idle()` can say yes, and it starts again when the
drain ends. On SIGTERM it starts nothing new, terminates the backends of the runs in flight, and
records them `failed` with "stopped for maintenance"; what it could not record is marked by the
next start, as after a crash.

### Limits per environment

- At most 2 runs at once and 100 jobs. A run is ended after 10 minutes (Supabase's advice) by
  the scheduler: its connection sets `statement_timeout`, which a job can change, so the runner
  also terminates the backend at the deadline, through a connection opened for that alone (the
  login's fourth), tried three times. The first review found the kill went through the bookkeeping
  session, which a job can keep failing (by locking `cron.job_run_details`), so it was skipped.
- The bookkeeping session sets `lock_timeout = 5s` and `statement_timeout = 15s` and pins its
  search path, so a tenant transaction left open on `cron.job` or on the queue delays only this
  environment's runner. Every statement names its schema.
- At most 8 requests in flight per environment, and no server-wide admission, so no environment
  waits for another's turn (a shared limit was tried and dropped: environments that keep it full
  hold every other one off); the environment limit (8) bounds the total. As many rows are taken
  from a queue at a time as may run; a request body over 1 MiB, a URL over 8 KiB, headers over
  64 KiB or more than 100 headers are answered with an error row instead.
- `<e>_cron` connection limit 4: the bookkeeping session, two runs and the deadline kill.
- `username` is bounded to 128 bytes (`job_username_size`): the developer may write it, and a run
  copies it into its history.
- Each second the runner reads only `jobid` and `schedule` of at most 100 active jobs; a run reads
  its command (at most 64 KiB) when it starts.
- Every statement that changes `<e>_cron` (close, reset, open) first ends that login's sessions, in
  the same transaction, with `lock_timeout = 10s` and `statement_timeout = 30s`. A job that runs
  `RESET ROLE` acts as `<e>_cron`, and any login may change its own password or role settings: left
  open in a transaction, that holds the login's role row, which an `ALTER ROLE` under the operation
  lock would otherwise wait for (the same fix `developer_login_sql` has). Only `<e>_cron` itself can
  change those rows; the developer has no ADMIN on it.

### Invariants

The ones in [Data API schemas](DATA-API-SCHEMAS.md#invariants), applied here:

- **Nothing a tenant can make wait holds what other environments wait for.** The console's
  change runs in the toggle child (`lab/realtime.py`), which holds the operation lock only for
  bounded statements: creating and granting the schemas runs with `lock_timeout` and
  `statement_timeout`, after the login's sessions were ended, and so does the database's connection
  limit (`limit_sql`). The fourth review found that a developer session can take the database's
  catalog row with a no-op `GRANT ... ON DATABASE` left open, which held an unbounded
  `ALTER DATABASE ... CONNECTION LIMIT`, and with it the operation lock, for as long as it liked; a
  live check now holds the row and sees the statement fail after its bound. The fifth review found
  that a job could hold the same row against the older toggles' identical statement (Realtime,
  database access and Studio: `realtime_database`, `realtime_turn`, `database_turn`,
  `developer_open`, `studio.py`), whose cleanup ends only the developer's sessions, so those use
  `limit_sql` now too. The sixth and seventh reviews found Realtime's setup scripts (`realtime_database`,
  whose grants on the database need the same row, and `realtime_grants`, whose grants on the
  `realtime` schema a job acting as `authenticated` can hold with a no-op GRANT) unbounded as well;
  each is now one transaction with the same bound (`ROW_BOUNDS`), and a live check holds each row
  from a job while the real script runs. The seventh review audited every other statement under the
  operation and write pause locks and found the rest bounded or out of a job's reach. A job or developer session holding the row makes such a change fail after
  its bound instead of waiting; turning Scheduled jobs off ends every job first.
- **A write pause ends the scheduler login first.** `write_pause.close_logins` runs `close_sql`
  before it closes the developer and Studio logins: a job acting as the developer could otherwise
  hold the developer's role row (its own password changed in a transaction left open) against that
  unbounded `ALTER ROLE`, under the installation-wide pause lock (fifth review). A live check holds
  the row from a job and sees the close finish at once. In the scheduler every environment
  has its own process, runner, connections, timeouts and request cap; nothing is shared between
  environments but the parent, which only starts and stops them.
- **No lock is held across environments.** The scheduler takes no file lock and no database
  lock outside the environment it works on.
- **What is recorded is what the database holds.** The run's row is written by the runner that
  ran it; a row it could not finish is settled at the next start, never guessed.

### Outgoing HTTP

The runner takes as many rows from its queue as may run now, at most 8 (`DELETE ... RETURNING`,
oldest first, as pg_net)
and performs each request itself, writing one row to `net._http_response` with the same columns
pg_net writes. Rules:

- Only `http` and `https`, ports 80 and 443 unless the operator lists more in
  `SBARBASE_SCHEDULER_PORTS`. At most 5 redirects, as curl's default bound is far higher; each
  redirect is checked again from the start. A redirect from https to http is refused.
- The name is resolved once and the connection goes to that address, with the original name for
  TLS and the `Host` header, so a second lookup cannot point it elsewhere (DNS rebinding). If any
  answer is refused, the request is refused: the check and the connection cannot disagree.
- Refused addresses, from the IANA special-purpose registries: `0.0.0.0/8`, `10/8`,
  `100.64/10`, `127/8`, `169.254/16`, `172.16/12`, `192.0.0/24`, `192.0.2/24`, `192.88.99/24`,
  `192.168/16`, `198.18/15`, `198.51.100/24`, `203.0.113/24`, `224/4`, `240/4` (which includes the
  broadcast address); `::/128`, `::1/128`, `::/96` (IPv4 compatible), `::ffff:0:0/96` checked as
  the IPv4 address it carries, `64:ff9b::/96` and `64:ff9b:1::/48` (NAT64), `100::/64`,
  `2001::/23` (Teredo and the other IETF assignments), `2001:db8::/32`, `2002::/16` (6to4),
  `fc00::/7`, `fe80::/10`, `fec0::/10`, `ff00::/8`.
- Also refused: **every address of the server itself** (read from its interfaces every 10 s)
  and **every subnet of every Docker network** on the daemon (read the same way), public or not.
  From the server its own public address takes the local route and reaches services the firewall
  hides from the internet, such as the direct database listener; and Docker may hand out address
  pools outside the private ranges.
- The one exception is **the exact origin of the installation's public address**
  (`SBARBASE_PUBLIC_URL`: scheme, host and port), so an application reaches its own Edge Functions
  through the TLS proxy and the gateway like any client. The exception lifts only the server's
  own addresses; it never admits a loopback, private or other special-purpose address. In a lab
  whose public address is `http://localhost`, requests to it are refused.
- DNS: names are resolved with c-ares (`node:dns` `Resolver`, 2 s per try, two tries), which waits on
  its own sockets, never on the process-wide pool `getaddrinfo` runs on, so one tenant's slow name
  servers cannot hold up another environment's lookups. At most 4 lookups at a time per environment,
  5 s in all. `/etc/hosts` is not read: `localhost` and the server's own names resolve only as public
  DNS answers them, and are checked like any other answer.
- Timeout as given (pg_net's default 5 s), at most 60 s, for the whole request including the
  lookup and every redirect. Response body kept up to 1 MiB (cut there, and `error_msg` says
  so); headers as a JSON object, the last value of a repeated header winning, as pg_net's object
  does. Responses are kept 6 hours (`pg_net.ttl`) and pruned by the scheduler.
- Requests are not retried. Rows taken from the queue and lost to a crash are lost, as with
  pg_net, whose queue is unlogged too.

## What stays different from Supabase

- `create extension pg_cron` and `create extension pg_net` still fail, and
  `select * from pg_extension where extname = 'pg_cron'` finds nothing. On PostgreSQL 17 an
  extension needs its control file in the image, so a compatibility schema cannot appear in
  `pg_extension`. Code that checks for the extension should check for the function instead:
  `to_regprocedure('cron.schedule(text,text,text)') is not null`.
- Studio's Cron page asks for the `pg_cron` extension and stays closed. Jobs are created with
  SQL and listed in `cron.job`; the console shows whether Scheduled jobs are on.
- Jobs run as the developer role, not as `postgres`; `username` is recorded, not used.
- `net.http_collect_response`, `net._http_collect_response`, `net._await_response`,
  `net.worker_restart`, `net.wait_until_running`, `net.wake` and `net.check_worker_is_up` are not
  provided.
- Requests from the database cannot reach private or loopback addresses, or the server itself
  other than through its public address.

## Rejected

- The real extensions in the `postgres` database with `schedule_in_database`: see "Why not".
- A superuser session that switches role for each job: `RESET ROLE` returns to the superuser.
- A definer function owner role per environment (the draft): see "Who owns what".
- Running requests from a container on the outbound network, as Edge Functions do: a container
  still reaches every address of the host ([FUNCTIONS-NETWORK-ISOLATION](FUNCTIONS-NETWORK-ISOLATION.md)),
  so the address policy is needed anyway, and the scheduler would need a pinned image.
- `docker exec psql` for runs: a run could not be ended at its deadline from outside without
  killing the exec, and every run would cost a process in the database container.

## Limits

- Not run on a real server; the live check runs against the pinned PostgreSQL in disposable
  containers ([evidence](../evidence/scheduled-jobs-checks-2026-10-22.json)).
- A recovery export or a move refuses an environment while Scheduled jobs are on.
- The server's own addresses are read from its interfaces. Behind a NAT that the server does
  not see (a cloud's elastic address), the public address is not on an interface; a request to
  it leaves the server and comes back through the provider, where the firewall applies.
- The scheduler's clock is the host's. Times are GMT, as `cron.timezone` defaults to.
- A runner whose session fails opens it again after 5 s; an environment's process that exits is
  started again by the parent after 5 s, and a parent that exits by the supervisor after 5 s.
- SCRAM key derivation runs on a process's own thread, and a job that runs `RESET ROLE` may rewrite
  the scheduler login's password with any iteration count (`set scram_iterations`); Bun's driver
  derives the key whatever the count (the second review measured 9 s for 100 million), and its pool
  signs in again by itself after a backend is ended, so a check before connecting does not hold. So
  the scheduler login never goes through Bun's driver: the bookkeeping session, the runs and the
  deadline kill all use the scheduler's own client, which refuses more than 100 000 iterations
  before any work (`wire.ts MAX_ITERATIONS`), and the platform writes the login's password with
  4096 (`login_sql`). The bookkeeping session sends one statement at a time, with values as dollar
  quoted literals under a random tag checked absent from the value, and keeps at most 24 MiB of rows.
  A tenant who rewrites the password stops only its own runs until the next turn on or start; a live
  check sets 20 million iterations, ends the session three times, and sees the process's event loop
  held up by less than 100 ms.
- A result that streams in faster than it is read would keep the client's loop on microtasks alone,
  so no timer (the run's own deadline) fired: the second security review froze the process for a
  whole run with `select generate_series(1, 200000000)`. The loop now gives way to the event loop
  every 5 ms; a live check streams 400 million rows with `statement_timeout = 0` and sees the run
  ended at its deadline.
- A job may change the scheduler login's own password or role settings (after `RESET ROLE`), for
  example `local_preload_libraries` naming a library that is not there, after which every login
  fails; its environment's runs then fail to connect until the next turn on or start writes them
  again. Only that environment is affected (the security review reproduced it).
- After an in-place restore, or at a start, the schemas and the login's grants are written again
  (`scheduled_jobs_repair`, from `backup.py start_services` and `scheduled_jobs_back`): an archive
  taken while Scheduled jobs were off carries no grant to the login, and one taken before they were
  first turned on has no schemas. An archive taken while they were on, restored while they are off,
  has its grants to the login taken back (`revoke_sql`), so a later dump names no such role and a
  recovery export does not refuse it. A failure there is reported and stops neither; the runner
  keeps retrying meanwhile and logs why, and the console still says On.
- A Docker network created after the last refresh is not refused until the next one, at most 10 s
  later. Docker's default pools lie in ranges refused anyway; this matters only where an operator
  configured pools outside them.
- A job that changes the scheduler login's password and runs on cannot be ended by a new
  connection, which can no longer sign in; after three tries the bookkeeping session, signed in
  already, ends it, and when the job ended that session too, the scheduler ends the backend as the
  superuser through the database container (`docker exec ... psql`, 10 s bound), which nothing the
  job controls can refuse. The fourth and fifth reviews reproduced both cases; live checks cover
  both.
- The deadline kill runs `SET ROLE NONE` first: a job may give the login a `role` setting, after
  which a new session of it could not signal the login's own backends (the third review reproduced
  it). Log lines escape control characters, so text a server sends (an error a job provoked) cannot
  forge a line for another environment.

## Tests

Unit (`tests/scheduler-cron.test.ts`, `tests/scheduler-egress.test.ts`, `tests/scheduler-wire.test.ts`,
`tests/scheduled-jobs-api.test.ts`, `lab/test_scheduled_jobs.py`): every schedule form and the
refusals, `ShouldRunTask`'s day rule and `$`, the minute catch-up, every refused range in plain
and mapped form, the public origin exception, redirects, the DNS answer that mixes public and
private addresses, the SQL that creates, grants and takes back, the footprint lists, the console
API's roles and states, and the job connection against a fake server: rows and COPY data far over its
cap dropped as they stream across many reads, the first error kept, COPY FROM STDIN refused, a
closed connection ending the query.

Live (`lab/scheduled-jobs-check.ts`): the shim built from the runtime's own SQL in the pinned
PostgreSQL; the plpgsql validator and the TypeScript parser agree on a corpus of 62 schedules;
pg_cron's functions for the developer, and their refusals; app roles reach `net.http_*` and no
role reaches `cron` without the grant; a job runs as the developer with its search path, records
its history with the command tag, gains nothing by `RESET ROLE`, is ended at its deadline with its
backend gone, also while it keeps the bookkeeping session failing, is not run twice at once, keeps
running after its schedule changes during a run, and returns 1 GB of rows without the process
ever holding them; old history and responses are pruned; `VACUUM` and several statements in one command; a lost run
is marked failed; an environment without a developer says so; requests to loopback, metadata,
private addresses, the database's own address and other schemes are refused and a public HTTPS
one is answered; the scheduler process follows `endpoints.json`, stops for a write pause and
records the run it ends on SIGTERM; turning off closes the login, takes back its grants and keeps
the jobs, and a dump then names no such role; turning on again clears role settings a job wrote;
a developer transaction holding ACCESS EXCLUSIVE on all four tables does not hold up the schema
SQL and grants run again (they finish in under 4 s): what locks a table runs only when it is missing;
a job holding the scheduler login's role row (it changed its own password and sleeps with no
timeout) does not hold up turning off, turning on or a start (each under 5 s, the job ended); a
queued `net.http_post` reaches a test server with its body, headers and parameters and the answer
is recorded under its id (the runner given the policy's check with loopback admitted for that one
server); and a `pg_dump -Fc` of the environment, planned by `restore_sql.plan_archive` as the
in-place restore plans it, replays into a stage database of another name with its jobs and the
roles it names present.
The success path through the public-origin exception is a unit test, not a live one: a lab has no
public address of its own.
