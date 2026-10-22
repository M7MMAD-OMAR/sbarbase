# Data API schemas

Scope: which schemas each environment's PostgREST exposes, its extra search path and
its row limit. Written 2026-10-21. Code: `lab/data_api.py`, `src/control/data-api.ts`,
`Runtime.data_api` in `lab/durable_runtime.py`. Reader guide: [Data API](../guides/data-api.md).

## Why

Until 0.5.14 every environment's PostgREST was started with `PGRST_DB_SCHEMAS=public`
and nothing could change it. Supabase lets a project expose any schema of its own
(Settings, Data API, Exposed schemas; `[api] schemas` in `config.toml`). An application
that keeps its tables in a schema of its own, as Supabase's hardening guide recommends,
therefore got `PGRST106` for every call on Sbarbase. The first application moved here
(`schemas = ["public", "i2s"]`) failed exactly this way.

## Sources

Read on 2026-10-21. PostgREST at tag v14.18 (the pinned image) and main.

- PostgREST configuration: `db-schemas`, `db-extra-search-path`, `db-max-rows`, and
  in-database configuration (`db-config`, on by default). https://docs.postgrest.org/en/v13/references/configuration.html
- Schema selection with `Accept-Profile` and `Content-Profile`; the first listed schema is
  the default; an unlisted one answers `PGRST106`. https://docs.postgrest.org/en/v13/references/api/schemas.html
- `queryDbSettings` in `src/PostgREST/Config/Database.hs` reads `pg_db_role_setting` for
  `CURRENT_USER` with `setdatabase IN (0, current database)`, database specific values
  first, and before that runs the `db-pre-config` function, whose `set_config('pgrst.*')`
  calls count as in-database settings too. The configuration page recommends the function
  over role settings.
- `NOTIFY pgrst, 'reload config'` reloads those settings; `'reload schema'` reloads the
  schema cache. NOTIFY is per database, so it reaches only that environment's PostgREST. The
  same reloads follow `SIGUSR1` on the pinned PostgREST (it reloads the schema cache and, measured,
  the configuration with it; `SIGUSR2` alone reloads only the configuration), which the platform
  sends to the environment's own container instead (see the Limits entry on the notification queue).
- Supabase: using custom schemas (the grants a schema needs), https://supabase.com/docs/guides/api/using-custom-schemas;
  hardening the Data API, https://supabase.com/docs/guides/api/hardening-data-api;
  `PATCH /v1/projects/{ref}/postgrest`, https://supabase.com/docs/reference/api/v1-update-postgrest-service-config;
  new tables no longer granted automatically, https://supabase.com/changelog/45329-breaking-change-tables-not-exposed-to-data-and-graphql-api-automatically.
- Studio: the internal schemas it never exposes (`apps/studio/hooks/useProtectedSchemas.ts`),
  and self-hosted Studio reading `PGRST_DB_SCHEMAS` once per process.

## Measured

`lab/data-api-check.ts`, against the pinned `supabase/postgres:17.6.1.166` and
`postgrest:v14.18`, in disposable containers. 60 of 60 checks passed on 2026-10-22
([evidence](../evidence/data-api-checks-2026-10-21.json)). Findings that shaped the design:

1. A `db-pre-config` function named in the container's environment
   (`PGRST_DB_PRE_CONFIG=sbarbase_rest.pre_config`) is applied at startup, after `reload
   config` and after a Docker restart, whatever `PGRST_DB_SCHEMAS` says. Named
   only as an in-database setting (`pgrst.db_pre_config`) it is ignored at startup until
   the first reload, so PostgREST serves `public` meanwhile: after any crash restart an
   environment whose owner turned the Data API off would publish `public`. If the function
   is missing, PostgREST logs it and serves its container's own configuration, which is why
   that configuration names the empty schema (finding below).
2. **When one exposed schema is missing, PostgREST answers every request with 503
   (`PGRST002`), `public` included,** after a reload and after a restart alike. A rename
   or a drop of an exposed schema takes the environment's whole API down.
3. **Tenant code can act as the REST login.** An ordinary function that runs `RESET ROLE`
   returns to the session user, and PostgREST runs functions in a session of the REST
   login (found in review). Any role setting or privilege of that login is therefore the
   tenant's. Role settings live in `pg_db_role_setting`, one catalog the whole cluster
   shares: with the settings stored there, an environment read every other environment's
   exposed schemas and row limit this way, whatever `REVOKE` was applied per database. So
   nothing of an environment is kept there.
4. Acting as the REST login, tenant code cannot set `pgrst.*` role settings (`permission
   denied to set parameter`), cannot replace a function the superuser owns, and reads only
   its own database's `sbarbase_rest.settings()`; it can change that login's own
   `statement_timeout` and `transaction_timeout` in its database.
5. **PostgREST reads every in-database key with `current_setting()`** inside the same
   transaction as the pre-config function (23 keys in v14.18, `dbSettingsNames`), and a value
   left in a pooled connection's session by tenant code counts. Measured in review: without
   a guard, a session value for `pgrst.db_schemas`, `db_anon_role` or `jwt_secret` became the
   configuration at the next reload. `pre_config` therefore sets the keys the platform writes
   for the load and stops the load, with an error, when the session carries any other key;
   PostgREST does not always keep the configuration it had: **a refused load can make it fall
   back to the container's own configuration** (measured in review: it logs "Failed to query
   database settings", then "Config reloaded" from `PGRST_DB_SCHEMAS`; at startup it always does,
   and a later review saw it keep what it had after a refused reload, so both happen). The platform
   therefore starts every REST container with the empty schema as that configuration
   (`CONTAINER_ENV`), and `pre_config` supplies `public` itself for an environment with no settings:
   a refused load either changes nothing or answers 404 to every table, and never publishes `public`. The three keys it manages may hold
   an empty string, which a connection keeps once a local value ended. A path that starts REST ends
   the REST login's connections first (REST is not serving then). A settings save does not: it
   used to, and PostgREST then loaded its configuration on a connection that had just been ended,
   fell back to the empty schema, and answered 503 and then 404 for about 3 s on every save
   (measured in review; the live check now polls through a save).
6. **The query that loads the configuration calls `unnest` without a schema.** With the REST
   login's default search path (`"$user", public`) a function a developer plants in `public`
   is chosen over the built-in, and its rows rank above the pre-config's (role settings
   come first). The REST login therefore has `search_path = pg_catalog` in its database,
   which the per-request search path PostgREST sets does not touch. `pre_config` has no
   search path of its own and refuses when the caller's is not `pg_catalog`, so a session
   level change cannot get around the pin: the load fails, and the container falls back to the
   empty schema (finding 1).
   Without the pin the load is refused and nothing is served (the live check shows it), not
   what the planted function says.
7. An ordinary login cannot `ALTER ROLE` the REST login (`permission denied to alter
   role`): only CREATEROLE with ADMIN may. The developer login has neither.

## Design

- **Source of truth: the catalog.** `data_api_settings(runtime, schemas,
  extra_search_path, max_rows, applied_schemas, applied_extra_search_path,
  applied_max_rows, served_schemas, served_extra_search_path, served_max_rows,
  start_report, state, failure, actor, updated_at)`. The first three are what was saved; the `applied_*`
  columns are the last settings an apply accepted, written only when it succeeds; the
  `served_*` columns are what PostgREST serves now, written by the runtime every time it
  writes the settings (the applied ones, less any schema a start found missing).
  **Ownership:** the saved settings, `state` and `failure` belong to the console and the
  supervisor's apply only. A start, resume or in-place restore writes nothing but the
  `served_*` columns and `start_report`, its own account (a schema left out, settings it
  could not write), which every run replaces or clears. A change saved while a start runs
  is therefore neither lost nor marked applied, and an old report never lingers. A refused change therefore stays saved and failed and is never served, not
  even after a restart. Without applied settings `pre_config` serves the defaults: `public`,
  search path `public`, no row limit.
- **Writers:** the console route `PUT /management/v1/environments/{id}/data-api` and
  `PATCH /v1/projects/{ref}/postgrest`, owners and admins only, viewers read. Both validate
  with the same rules as `lab/data_api.py` and mark the row pending. A second change
  does not wait: while one is pending the route answers 409 and the caller saves again.
- **Apply:** the supervisor runs `lab/data_api.py apply <runtime>`, which runs
  `durable_runtime.py data-api <runtime>` (`data_api_command`). It holds the environment's own
  lock (`data_api.environment_lock`, a file beside the operation lock) for its write and again
  for recording, and the installation's operation lock only twice. First, like the other per-environment
  commands, it requires settled effect receipts, a running database, a published environment
  and an unfenced database; either lock held then is exit 75, and nothing is written. Then,
  under the environment's lock alone, it installs the guard, pins the REST login, checks every
  schema exists and writes the settings: these are the statements a tenant session can make
  wait. Right after the commit it signals the environment's REST container
  (`Runtime.tell_rest`, `SIGUSR1`): from then on the guard protects the new list, so PostgREST
  must not keep serving the old one while the command waits (found in review). Last it lets
  the environment's lock go and waits for the operation lock, without a bound, since what
  holds it is another operation that ends (a provisioning, a start); then it tries the
  environment's lock again, reads the settings back and records the whole outcome in one
  transaction, `applied` included. A start or restore that wrote other settings in between, a restore holding the
  environment's lock then, or a read-back that fails, makes it exit 75 (`DataApiBusy`): the database holds those
  settings and the supervisor asks again. If the outcome cannot be recorded after the commit it
  exits 4 and the console says the settings were written, never that the previous ones are
  still served; a change that wrote nothing (the schema lock held, or a schema dropped, on
  every try) exits 3 with its reason. An in-place restore tries the environment's lock too
  before it writes the settings, so the two never write the settings at once; its guard and pin
  come before that and can meet a console change's (listed under Limits). Neither path waits
  for it while holding the operation lock (a
  write pause holds it when it restores), so a restore that finds it held skips writing the
  settings, reports that, and leaves them to the console change in progress and the next start.
  A schema that does not exist refuses the whole change (exit 3, the reason shown in the
  console) and PostgREST keeps serving what it served. A running Studio restarts to read
  the new list: the command marks its session `failed` with no reason, which the
  supervisor's own Studio child (`lab/dev.py schedule_studios`) starts again, or stops when a
  Stop came meanwhile, and records.
- **Start and resume:** `Runtime.resume` writes the applied settings again from the
  catalog before the services start. A schema missing at that point (a restore of an
  older dump, a manual change as superuser) is left out, the rest is served, and the row
  records it in `start_report`; once every schema is back, the next start clears it. A schema named in the extra
  search path and missing is left out and reported the same way. The catalog is read with
  retries; if it stays busy the start stops with an error instead of reading "no
  settings", which would reset PostgREST to `public`. Like any resume failure this stops
  the whole start (`resume_published_environments` does not isolate environments). Finding 2 is
  why: serving fewer schemas beats answering 503 to everything. When none of the exposed
  schemas is left, the empty platform schema `sbarbase_empty` is served instead, so
  requests answer 404 and `public` is not published for an application that chose not to
  expose it. An environment that was never configured and has lost `public` gets a row
  with the defaults (actor `system:start`) and goes through the same write, retries and
  lock claim; with that row the next start serves `public` again once it exists, rather
  than reading the empty schema as settings a moved database brought.
- **Guard:** `sbarbase_guard.exposed_schemas()`, an event trigger the superuser owns, on
  `ALTER SCHEMA`, `DROP SCHEMA`, `DROP OWNED`, `CREATE EXTENSION`, `ALTER EXTENSION` and
  `DROP EXTENSION`, and `sbarbase_guard.exposed_schemas_dropped()` on `sql_drop`, for any
  command that drops a schema whatever its tag. After such a command they check that every
  exposed schema still exists and otherwise raise, so the command rolls back. Both run as
  their owner, since the settings they read belong to the platform. With no
  setting it protects `public`. The superuser passes, so platform work (restore) is not
  blocked. It is installed when the environment is provisioned, by a project import
  (`lab/import_project.py`), and by every in-place restore and write pause resume
  (`backup.start_services`, after REST is stopped and its login's sessions ended), and by a
  console change, a start or resume when the presence check (`guard_present_sql`,
  `Runtime.ensure_guard`) finds anything missing. Installing takes catalog rows a REST login
  session can hold open, so turning database access on, which runs under the operation lock
  with REST serving, never installs it: it refuses while it is missing, and a start repairs it.
- **Validation:** lowercase unquoted identifiers only (the value is comma split by
  PostgREST and travels through SQL); at most 16; no duplicates; never Studio's internal
  schemas (`auth`, `storage`, `vault`, `cron`, `net`, `graphql`, `realtime`, `extensions`,
  `supabase_functions`, `supabase_migrations` and the rest), `pg_*`, `information_schema`,
  or names starting with `sbarbase` or `supabase`. The extra search path may also name
  `extensions`. Row limit 1 to 1000000 or none.
- **Where the settings live.** In the environment's own database, as the function
  `sbarbase_rest.settings()`, which answers `key, value` rows (`pgrst.db_schemas`,
  `pgrst.db_extra_search_path`, `pgrst.db_max_rows`; no rows for the defaults, which `pre_config`
  supplies).
  A write replaces it with literals that passed validation. `sbarbase_rest.pre_config()`
  applies the rows with `set_config` and is what every REST container names as
  `PGRST_DB_PRE_CONFIG` (`data_api.CONTAINER_ENV`, added by `Runtime.activate_services`, not by
  `lab/run.py`, which the disposable lab containers share). The schema and both functions
  belong to the superuser; only the environment's REST login may use the schema and run
  them. A function rather than a table, so a tenant's own `pg_dump` of every schema still
  works without a privilege on it.
- **Only the three managed keys are taken from `settings()`.** `pre_config` and `serving_sql` read
  `pgrst.db_schemas`, `pgrst.db_extra_search_path` and `pgrst.db_max_rows` and nothing else from it,
  so a `settings()` that an archive from another installation or version brought, answering
  `db_anon_role` or `jwt_secret` as well, changes nothing: `pre_config` ignores those rows (it does not
  refuse the load) and sets the key from nowhere, so PostgREST keeps its own default for it. A start
  compares the three keys only.
- **REST containers.** A retained REST container started before this change differs from its
  desired configuration only in `PGRST_DB_PRE_CONFIG`, `PGRST_DB_SCHEMAS` (now the empty schema)
  and `PGRST_DB_EXTRA_SEARCH_PATH`, which `TUNABLE` lists for `rest`, so the next
  start removes it and creates it again with the same image (it is stateless). The schema
  and functions are installed when the environment is provisioned, before REST first
  starts, by a project import, again by every in-place restore and write pause resume, and by a
  console change, a start or resume when the presence check (`guard_present_sql`) finds
  anything missing, including the REST login's right to run them.
- **REST deadlines** (`statement_timeout` 8 s, `transaction_timeout` 12 s on the REST login)
  are written again by every start when they differ (finding 4), and every other role
  setting of that login, in any database or globally, is removed. A start used to refuse
  instead, and since tenant code can change them, one environment could stop the start of
  every other.
- **Studio** reads what PostgREST serves from the database (`data_api.serving_sql`) at
  session start, the empty fallback schema included, so the two never disagree.
- **Recovery:** the settings travel in the database dump as `sbarbase_rest.settings()`,
  so an archive brings the settings it was taken with. An in-place restore therefore
  replays the archive's, and `backup.start_services` writes the catalog's settings again
  before REST starts; resume does the same at every start. The in-place restore first installs
  the settings function, `pre_config` and the empty schema (`guard_sql`) an archive from before
  them lacks, and does not start Auth or REST when that or the pin fails. Both restore paths require every
  role a dump names to exist, and the REST login does. `recovery-restore-db.py` still
  refuses every `pgrst.*` role setting.
- **Superuser statements start from pg_catalog.** Every statement this feature runs in an
  environment's database begins with `SET search_path TO pg_catalog, pg_temp` (or `SET
  LOCAL` inside the write) and names `pg_catalog` on its function calls. The developer login
  may create functions in `public`, and `public.unnest(text[])` matches better than
  `pg_catalog.unnest(anyarray)`; with the superuser's own search path it ran as the
  superuser and made the developer login a superuser of the cluster (found in review; the
  live check plants such functions, and runs the old query as a control that does
  escalate). The guard functions, which run in the tenant's own session, set
  `search_path = pg_catalog, pg_temp`: with `pg_temp` left out, PostgreSQL searches the
  session's temporary schema first for types. The reserved-schema guard from 0.5.8 had the
  same gap and is fixed with it, in place at every start for environments set up before. The developer grants script and Studio's role script
  stay on pg_catalog to their end: before this, `studio_identity_sql` called
  `pg_has_role(...)` unqualified as the superuser, so a tenant's `public.pg_has_role(text,
  text, text)` made the developer login a superuser of the cluster whenever database
  access was turned on or Studio started (found in review; present since 0.5.8, fixed here;
  the live check plants it, with a control). A third guard function runs on `sql_drop` and
  refuses any command that drops an exposed schema, whatever its tag: an extension the
  tenant owns (`lo`), with an exposed schema added to it, is otherwise dropped by `DROP
  EXTENSION` or by a cascade from a table its view depends on.
- **Off.** An empty list of exposed schemas turns the Data API off: the empty platform
  schema is served, and the console says Off. `PATCH` with `db_schema: ""` does the same.
- **`public` missing without saved settings** (only a superuser change or a restore does
  this): the start serves the empty schema rather than leave PostgREST answering 503, and
  reports it when the environment has a catalog row.
- **The platform's schemas must belong to a superuser.** `guard_sql` refuses (and
  `guard_present_sql` answers false) when `sbarbase_rest`, `sbarbase_guard` or `sbarbase_empty`, or a
  function in the first two, belongs to a role that is not a superuser, because the guards and the
  start's reads of `settings()` run as the superuser. A database brought from outside that has them
  that way makes the whole Sbarbase start fail until an operator fixes the ownership (the guard raises
  inside the resume, which does not isolate environments); the live
  check shows it. The live system cannot produce it: the reserved-schema guard refuses `sbarbase*`
  schemas to every other login.
- **The independent restore target.** Its REST container is created by
  `lab/recovery-check-services.py` from the same configuration plus `CONTAINER_ENV`, so the
  restored `sbarbase_rest.settings()` is applied there as well. Before REST starts, both that
  script and `target_runtime.py up` run `guard_sql` on the restored database, which installs the
  settings function, `pre_config` and the empty schema an archive exported before this change does
  not hold (without them every request answers 503), and pin the REST login. Found in review: the
  container was first created with the plain lab configuration, which served `public` and ignored
  the settings; then with the new one but against a database that had no function to name.
- **A moved database** (restored on another installation, so without a catalog row) keeps
  the Data API settings it brings when they pass the rules and their schemas exist; they
  become its saved settings, even when a schema they name is missing (that one is then
  left out and reported, never replaced by `public`). If the catalog cannot record them
  (busy), the start stops
  rather than reset them; if a row appears meanwhile, or the catalog has no table yet, the
  database keeps what it brought. Settings no rule admits are reset to the defaults.
- **What is served is read back** from the database after every start or apply
  (`serving_sql`, which reads `sbarbase_rest.settings()`), never assumed from what was
  written.
- **The extra search path** is guarded like the exposed schemas: a missing schema there
  also makes PostgREST answer 503.
- **Existence** is decided by the database: `present_sql` asks about exactly the
  configured names and gets back only those that match a schema exactly. Reading every
  schema name and splitting the output once let a tenant's schema named `x`, newline,
  `phantom` make `phantom` look present; PostgREST then answered every request with 503
  and every start failed (found in review, now a live check).
- **No window between the check and the write.** The write is one transaction
  (`write_sql`): it tries a transaction advisory lock in the environment's database, checks
  again that every schema it names exists and writes the settings. It sends nothing: PostgREST is
  signalled after the commit.
  The guard takes the same lock, waiting for it, before it checks a rename or drop, and
  requires read committed so its check reads what the platform committed while it waited.
  A write that holds the lock makes a drop wait and then be refused. A drop that holds it
  makes the write answer busy at once; the runtime tries again a second later, up to three
  times, and then sees the schema gone. The platform never waits on the lock, so a tenant
  session that holds it (any login can take an advisory lock) cannot keep the platform
  waiting: a console change is then refused with that reason.
  A start or an in-place restore does not leave what is served to a tenant session: after
  a first busy try, its write ends the non-superuser sessions of that database that hold
  or wait for the lock (`release_sql`) and then queues for the lock itself in the same
  transaction, so a session that asks later waits behind the platform. If the write still
  cannot be done, the start keeps going, records what the database serves, and says so in
  `start_report`. An in-place restore that cannot write the
  settings still starts Auth and REST, and the next start writes them. Found in
  review as a race a tenant won 3 times in 8; the live check runs both orders and the
  held lock.
- **The empty schema stays empty:** the guard also runs on `CREATE EXTENSION` and `ALTER
  EXTENSION` and refuses any object in `sbarbase_empty`. Through supautils the developer
  login could otherwise place a superuser owned extension there.
- **`/v1` shape:** GET answers what is served: `db_schema`, `db_extra_search_path`,
  `max_rows`, `db_pool` (fixed at 3), `db_anon_role` and `role_claim_key`. It never answers
  `jwt_secret`; the empty fallback schema shows as an empty `db_schema`. PATCH changes only
  the fields it names, starting from the settings last accepted (not from what a start had
  to leave out), as Supabase does, and refuses a different `db_pool`.
- **Gateway:** `src/gateway/handler.ts` forwards `Accept-Profile` and `Content-Profile`
  to PostgREST (its `forwardedHeaders`), checked by `tests/data-api.test.ts`.

## Invariants

Every path that writes or records the Data API settings keeps these, and a review checks each
failure branch against them:

- **Nothing a tenant can make wait holds what other environments wait for.** No statement a
  tenant session can hold up runs under the installation's operation lock on a console change, and
  each environment's change runs in its own supervisor child (`lab/dev.py schedule_data_api`).
  Where a start or restore runs such a statement under the operation lock, it is bounded and the
  REST login's sessions are ended first.
- **No file lock is waited for while the other is held.** The environment's Data API lock is only
  ever tried, and a console change lets it go before it waits for the operation lock. Database lock
  waits under either one are bounded (`lock_timeout`).
- **What is recorded is what the database holds.** A console change records `applied` and `served`
  only after reading the settings back under both locks, in one transaction (`record_applied`). A
  start records what it wrote or read back under the operation lock; the races left, all an
  operator's in-place restore, are listed below.
- **A settings write one environment holds up never stops a start.** A write that stays busy or
  races a drop, a reset to the defaults included, becomes that environment's report; it is not
  raised into the start of every environment.
- **Races only an operator or the platform can cause are listed under Limits**, with how the
  environment recovers.

## Rejected

- Role settings of the REST login (`ALTER ROLE ... IN DATABASE ... SET pgrst.db_schemas`),
  the first design: finding 3. Closing `pg_db_role_setting` per database (`REVOKE SELECT
  ... FROM PUBLIC`, with a grant to the REST login) was tried next; the grant reaches the
  tenant through `RESET ROLE` and the catalog holds every environment's rows.
- `pgrst.db_pre_config` as an in-database setting, which would have kept the containers as
  they were: finding 1.
- Changing `PGRST_DB_SCHEMAS` in the container on every change: a recreate per change is
  slower and riskier than a signal to the container. The container changes once, to name the
  function and to fall back to the empty schema.
- Exposing a schema without checking it exists: finding 2.

## Limits

- Exposing a schema does not grant anything. Each schema needs `GRANT USAGE` and table
  grants for `anon`, `authenticated` and `service_role`, and row level security, as on
  Supabase. The security advisor (`/v1/projects/{ref}/advisors/security`) still checks
  `public` only.
- `db_pool` stays 3 per environment; the connection budget depends on it.
- GraphQL is not served. `graphql_public`, which Supabase's `config.toml` lists by
  default, is refused with that reason instead of being saved and then failing.
- Renaming or dropping a schema needs the read committed isolation level (PostgreSQL's
  default), for the guard's check.
- **What tenant code can still do to its own environment's API.** Each path needs a function
  that PostgREST runs, which its developer or Studio login can write. Two reach beyond the
  environment, both bounded: the role settings it can write for itself in other databases (they
  only affect its own login and the next pin clears them), and waiting platform statements, below.
  The shared notification queue, which any login can fill, is described last; the Data API does
  not depend on it (the project's SQL import and the agent SQL route still send `NOTIFY pgrst`
  after schema changes, as before this change). A tenant cannot choose which schema is served: `pre_config` sets the three
  managed keys itself, and a non-superuser cannot set `pgrst.*` role settings on the pinned image
  (`permission denied to set parameter`, observed, not read from the PostgreSQL source). What it
  can do is freeze reloads:
  - A session value in a pooled REST connection, a PostgREST key or another search path, makes
    every reload on that connection fail, and PostgREST stays on what it had or falls back to the
    container's empty schema (404 to every table, until the connection is replaced). A settings save
    leaves the pool alone, so it does not clear this; restarting the REST container does (the
    session state goes with the connection), and so does a Sbarbase start, which ends the login's
    connections before it pins. The console meanwhile shows the new settings as applied and served: those columns
    record what the database holds, read from `sbarbase_rest.settings()`, not what PostgREST
    confirmed.
  - Acting as the REST login (`RESET ROLE` in such a function) it can set that login's role
    settings: its search path, role, deadlines, anything not `pgrst.*`. Every start, resume,
    in-place restore and settings write leaves it exactly search_path=pg_catalog and the two
    deadlines again, and clears its settings in other databases and globally. A start can race a
    looping function. If PostgREST itself starts while such a value is set, it falls back to the
    container's empty schema; every path that starts or recreates REST pins first (start and resume,
    the in-place restore, a signing rotation, the independent restore target), and the in-place
    restore does not start REST when it cannot. A start ends the login's sessions before it pins,
    since REST is not serving then: a resume stops a REST container that is still running before
    it pins, and a signing rotation, which recreates REST right after, ends the sessions too.
    `pre_config` names every type in `pg_catalog` and has no record variable, so a temporary
    table named like a type (found in review: `record`) cannot take its place; the live check
    runs the old form as a control. A temporary object that makes it fail is the same freeze.
  - **Temporary objects searched before `pg_catalog`.** With the pin `search_path = pg_catalog`,
    PostgreSQL still searches the session's temporary schema first for relations and types, and the
    developer login has TEMPORARY. A temporary type in a pooled REST session can run a tenant
    function inside PostgREST's own configuration query (observed in review with a domain named
    `regrole`). It runs before `pre_config`, which overwrites anything it set, so the effect is the
    same freeze as above. Pinning `pg_catalog, pg_temp` would search temporary objects last; not done.
  - It can send `NOTIFY pgrst` with no privilege, to force reloads of its own PostgREST.
  - It can write role settings for itself in other environments' databases (`ALTER ROLE ... IN
    DATABASE other`). They only affect its own login, the next pin clears them, and
    `settings_query` keeps them out of the other environment's export.
  - A session `role` value, or any other session value that changes how `pre_config` or the
    configuration query runs, left in a pooled connection freezes reloads like the search path.
    It can also end the login's other backends, PostgREST's listener included, which makes
    PostgREST reconnect and reload.
  - A plain restart of the REST container does not undo a role setting the tenant changed: the
    load is refused again, and the API answers 404 to every table. Only a Sbarbase start, which
    pins the login and ends its connections, clears it; a settings save pins the login for new
    connections but leaves the open ones as they are.
  - **A signing rotation has a window.** The old REST keeps serving between the pin and its
    recreation, because Auth is recreated and waited for first. A looping tenant function can set
    the search path again then, and the new PostgREST's load is refused: it falls back to the
    container's empty schema and answers 404 to every table until the next Sbarbase start
    corrects it. One environment, its own developer's code, and
    recoverable by the platform.
  - **Waiting platform statements.** A transaction a tenant leaves open on its login's role-setting
    rows, a session holding the advisory lock, or a transaction holding the catalog rows of `sbarbase_rest` and
    its functions (a `GRANT` left open on them) makes platform statements wait. Each is bounded:
    the pin 5 s for a lock and 30 s per statement, the guard installation 10 s and 60 s, a settings
    write returns at once when it cannot take the lock (`BUSY`), and a start makes one try and then up to
    two claims of 3 s each. A console change runs these statements under its environment's own lock, not the
    installation's operation lock, and has the supervisor restart a running Studio, whose grants
    wait at most 10 s for a lock and which never holds the operation lock (found in review: under the operation lock a tenant could delay
    every other environment's change and start by about 30 s per save, and before the Studio bound,
    for as long as its transaction stayed open). The wait stays in the environment that causes it:
    there a console change fails after the bound (the guard is installed on that path), and can be
    saved again once the holder ends. A start still runs them under the operation lock, but it ends
    the login's sessions before it pins and claims the schema lock, so an open transaction does not
    hold it up; what remains is a transaction left open on the platform's own schema, which a start
    meets only when the guard is missing (a restored archive from before it), bounded by the guard
    installation's 10 s.
  - **The notification queue is shared by the whole cluster**, and a login that keeps a `LISTEN`
    open in a transaction can fill it (about 8 GB must be written at the default size; observed
    only with a queue shrunk to 64 pages on the pinned image). Any `NOTIFY` then fails at commit,
    for every environment. The Data API therefore does not use it: the settings are committed first,
    and PostgREST is told by signalling the environment's own container. So a full queue stops
    neither a settings write nor a start of another environment (found in review: it used to roll
    the write back, and a failed notification left the catalog and the database disagreeing).
    If the signal cannot be sent, the settings stay written and are recorded as applied with a
    report that PostgREST was not told; it takes them at its next reload or start. A tenant's own
    `NOTIFY pgrst` still reaches only its own PostgREST. A full queue still stops every other user
    of `NOTIFY` in the cluster, which is not new in this change. Nothing ends a stalled listener,
    because the developer and Studio logins have no idle or transaction timeout.
- **The REST login's password.** Acting as that login, tenant code can change its password.
  PostgREST then cannot open new connections, once its pool recycles or at a restart, and the
  environment's Data API is down until the next Sbarbase start. Every path that starts REST gives the
  login its generated password back from the runtime file (`rest_password_sql`, after the pin, bounded
  like it), so the change cannot outlast a start, and cannot make a start fail: found in review, it
  used to leave PostgREST unable to connect, its readiness wait to fail, and with it the start of every
  environment after it (a resume does not isolate environments). The write ends the login's sessions
  first, in the same transaction: a request whose function changes the password and runs on holds the
  login's row, and found in review, that made a signing rotation, whose old REST still serves, fail
  after the lock bound while it held the operation lock. A settings save leaves the password
  alone, since REST is serving. The independent restore target and its check script restore it from the
  archive's credentials.
- A restore accepts only `search_path`, `statement_timeout` and `transaction_timeout` as role
  settings of the environment's logins, and no role-level setting at all. The recovery export pins
  the REST login first, as every start does, so what a tenant gave it is not carried; on the Auth
  and Storage logins a setting a tenant gave them is carried and makes the restore refuse, until
  it is removed. Another environment's recovery export leaves other environments' logins'
  rows out (`settings_query` in `lab/recovery_boundaries.py`): any login may set its own settings
  in any database, so they would otherwise make that environment's restore refuse.
- A tenant's `pg_dump` of every schema includes `sbarbase_rest` and its grants to the REST
  login, as it includes the guard functions; a migration made from it names platform
  objects.
- The first start after this change recreates every retained REST container once.
- A running Studio restarts when the setting changes; open Studio tabs reload.
- An in-place restore whose Data API setup (the settings function and the pin) fails starts neither
  Auth nor REST. If only writing the applied settings fails, it still starts both and reads back
  what the restored database serves for the console when it can.
- Studio reads the served settings once, when it starts. An in-place restore does not restart a
  running Studio, and when PostgREST cannot be signalled the data-api command has already asked
  for a Studio restart with settings PostgREST has not loaded yet: until the next Studio start the
  two can show different schemas. A Studio that is starting while a change is applied may read the
  earlier settings and is not restarted; opening it again shows the new ones.
- A console change whose outcome the catalog refuses to record, after three tries a second apart,
  has still been written, and PostgREST takes it at its next reload (it was signalled, unless that
  failed too). The row says so (exit 4, `failed` with that reason), but
  its served list is the one before, a running Studio is not restarted, and the next start writes the
  settings applied before again. Saving again records it. This needs the catalog itself to fail, not
  anything a tenant can do.
- An in-place restore or a write pause (`backup.start_services`, which both run) pins the REST
  login and installs the guard before it tries the environment's lock, and a signing rotation
  pins it under the operation lock alone, so either can run at the same time as a console
  change's guard or pin. Writing the same catalog rows, one may fail (for example "tuple
  concurrently updated"): a restore or pause then leaves Auth and REST stopped until the next
  start, a rotation fails and is asked for again, and a console change is recorded as failed and
  can be saved again. One environment, started by an operator or an owner, never by tenant code
  alone.
- Not part of the Data API, and older than it: turning database access on runs the developer
  grants on `public` under the operation lock, in the supervisor's one database toggle queue,
  with REST serving. A session acting through the API (a no-op `GRANT` left open) can make them
  wait. This change bounds that wait at 10 s, where it had none; removing it from under the
  operation lock is follow-up work.
- A plain in-place restore (`lab/backup.py`, which holds only the backup lock) can meet a console
  change that is recording its outcome: the change reads the settings back from the database the
  restore then replaces, the restore finds the environment's lock held and skips its write, and the
  change records its settings as applied and served while REST serves the archive's settings. One
  environment, an operator's restore, and the next start writes the applied settings again.
- On a catalog file too old to hold the settings table, the start of an environment that
  has lost `public` cannot record the defaults. If `public` comes back before the console
  has opened that catalog, the next start reads the empty schema as settings the database
  brought and keeps the Data API off; saving the settings in the console serves them.
- **Rolling back to a release before this one publishes `public` in every environment.** The
  way-back recreates each REST container with the old configuration (`PGRST_DB_SCHEMAS=public`,
  no settings function), so an environment that exposes another schema answers `PGRST106`, and one
  that is Off serves `public`, until the newer release runs again. The catalog schema version did not
  change, so nothing refuses the rollback. The in-place restore and the restore target refuse to start
  a REST container that lacks the new environment (`carries_container_env`).
- **A project import can put a superuser-owned extension into a platform schema.** It runs as the
  superuser and creates the schema its source named, so a source with an extension in
  `sbarbase_empty` or `sbarbase_rest` lands it there. The emptiness checks of the guard run before any
  command filter, so from then on it refuses **every** `ALTER SCHEMA`, `DROP SCHEMA`, `DROP OWNED` and
  `CREATE`, `ALTER` and `DROP EXTENSION` the developer or Studio login runs in that environment (it cannot
  rename or drop a schema, or create `pgcrypto` in `extensions`), until an operator moves the extension
  as the superuser. The effect stays inside that environment, gives the tenant nothing (the code is the
  extension's, not the tenant's) and is recoverable that way. The import is not part of this change.
