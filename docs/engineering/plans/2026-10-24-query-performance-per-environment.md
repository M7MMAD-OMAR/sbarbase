# Query Performance per environment: study

Recorded 2026-10-24. Status: study and proposal, nothing built. It asks whether Studio's Observability > Query Performance page can work for an environment on the shared PostgreSQL cluster without one environment seeing another's activity, and what the platform would have to own so that a tenant cannot undo it. Claims about this repository cite a file and line as of commit `6d41636` (with the working tree changes listed in the session's git status, none of which touch the files cited here). Claims about Studio cite the pinned Studio source, claims about PostgreSQL cite the 17.6 source. Everything else is marked **inferred** or **unknown**, and the open questions at the end list what a live check has to settle.

## The answer in short

Yes, there is a platform side solution, and it is the one shared hosts already use: the platform installs `pg_stat_statements` in each environment database **in a private schema the tenant cannot use**, and gives the tenant only a **SECURITY DEFINER function that filters to its own database and its own roles**, wrapped in a view named `extensions.pg_stat_statements` so Studio's unqualified query finds it. A scoped `extensions.pg_stat_statements_reset()` replaces the global reset. The filter runs inside the function, as a role the tenant cannot become, so nothing the tenant writes in its query can see rows before the filter.

Two things make this more than "create a view":

1. **Every Query Performance query also joins `pg_authid`** (`inner join pg_authid as auth on statements.userid = auth.oid`). `pg_authid` is in `pg_catalog`, which PostgreSQL searches before `public, extensions` when it is not named in the path, so it cannot be shadowed. The environment's logins cannot read it today. The page therefore also needs a **column grant `SELECT (oid, rolname) ON pg_catalog.pg_authid`** to the environment's Studio and developer logins in that database only. Those two columns are already public through `pg_roles`; `rolpassword` stays unreadable. This is inferred to work and must be checked live (open question 1).
2. **The raw extension view is worse than "counts of other tenants"**: `anon`, `authenticated` and `service_role` are cluster-wide roles, and pg_stat_statements shows full query text to the role that ran the statement. A session acting as `anon` in any environment would read the text of every environment's `anon` statements. So the raw view must never be reachable by any environment role, and **an import from Supabase can already create it today** (below, "A gap that exists today").

What stays shared even with the design: the 5000 entry table (`pg_stat_statements.max`) and its eviction, the query text file, the cost of reading it, and the cluster-wide session metadata in `pg_stat_activity` that every login already sees. None of these show another environment's statements, times or counts; they are listed under "What remains shared".

## What Studio does, in the pinned version

Studio is pinned at `public.ecr.aws/supabase/studio:2026.10.12-sha-3699278` (`lab/studio-image.lock.json:3`). The short sha resolves to supabase/supabase commit `369927892fde63d07b199d4e80fe05ae780573d5` (2026-10-12T03:28:12Z, found through the GitHub API). Paths below are inside that commit.

### Query Performance page

- **Detection is by error text, not by `pg_extension`.** `apps/studio/components/interfaces/QueryPerformance/WithStatements/WithStatements.tsx:174-188` shows the warning "pg_stat_statements extension is not enabled" when the error message contains both `pg_stat_statements` and `does not exist`. Any other error is shown verbatim as "Error loading query performance data" (`:189-197`). So the page works as soon as `pg_stat_statements` resolves to a readable relation with the expected columns. VERIFIED.
- **The main query** is the `unified` preset, `apps/studio/components/interfaces/Reports/Reports.constants.ts:758-852` (the hook is `QueryPerformance/useQueryPerformanceQuery.ts:55`, `PRESET_CONFIG[Presets.QUERY_PERFORMANCE]`). It runs `set search_path to public, extensions;` then reads unqualified `pg_stat_statements as statements inner join pg_authid as auth on statements.userid = auth.oid`, `WHERE statements.calls > 0`, ordered and paged. Columns used: `query`, `calls`, `total_exec_time`, `total_plan_time`, `min_exec_time`, `min_plan_time`, `max_exec_time`, `max_plan_time`, `mean_exec_time`, `mean_plan_time`, `rows`, `shared_blks_hit`, `shared_blks_read`, `userid`, and `auth.rolname`. Optional `index_advisor(base.query)` per row for `select%` and `with pgrst%` statements. VERIFIED.
- **Older presets** `mostFrequentlyInvoked`, `mostTimeConsuming`, `slowestExecutionTime` (`Reports.constants.ts:574-744`) use the same `pg_stat_statements` plus `pg_authid` join. VERIFIED.
- **`queryMetrics`** (`:866-896`) reads `pg_stat_statements` without the `pg_authid` join; **`queryHitRate`** (`:745-757`) reads `pg_statio_user_indexes` and `pg_statio_user_tables` only, which already work. VERIFIED.
- **Reset**: `WithStatements.tsx:316-322` runs `SELECT pg_stat_statements_reset();` with no `set search_path` prefix, so it resolves through the login's own default path. The confirmation text says it resets "the pg_stat_statements table in the extensions schema" (`:330`). VERIFIED. In Sbarbase that path is `public, extensions` for both the Studio login (`lab/studio.py:124`) and the developer login (`lab/durable_runtime.py:1589`).

### Other pages that depend on the same data

| Page or feature | SQL | Depends on | Source |
|---|---|---|---|
| Observability overview, slow query count | `SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'`, then unqualified `pg_stat_statements` | a `pg_extension` row **and** a readable relation; returns 0 silently otherwise | `apps/studio/components/interfaces/Observability/useSlowQueriesCount.ts:6-23` |
| `slowQueriesCount` preset | unqualified `pg_stat_statements` | readable relation | `Reports.constants.ts:853-865` |
| Table index advisor | `pg_stat_statements` joined to `pg_authid`, then `index_advisor()` | relation, `pg_authid`, the `index_advisor` extension | `packages/pg-meta/src/sql/studio/advisor/index-advisor.ts:20-59` |
| Advisors (lints) | `set pg_stat_statements.track = none` inside an exception block, so a failure is ignored | nothing (a failed SET is swallowed) | `packages/pg-meta/src/sql/studio/advisor/lints.ts:4-11` |
| Database Connections | `pg_catalog.pg_stat_activity` filtered to `datname = current_database()` | `pg_stat_activity` visibility rules | `packages/pg-meta/src/sql/studio/database/activity.ts:4-23` |
| Query Insights | Logflare `supamonitor_logs` | the platform's log pipeline, not this database | `apps/studio/components/interfaces/QueryInsights/QueryInsights.constants.ts:26-30` |

`pg_stat_activity` is always `pg_catalog.pg_stat_activity` (implicit pg_catalog first), so no view can stand in for it; the Database Connections page keeps today's behaviour (below). Query Insights is a hosted feature and stays empty. Sbarbase's own `/v1` performance advisors answer an empty list (`src/control/supabase-api.ts:370`) and are not affected.

### Which login runs it

Studio's postgres-meta connects as `<e>_studio` to database `<e>` (`docs/engineering/STUDIO-INTEGRATION.md:185`, `:212-213`). When a developer login exists, `<e>_studio` takes it on connecting: `ALTER ROLE <e>_studio IN DATABASE <e> SET role = <e>_developer` (`lab/durable_runtime.py:642-643`, in `studio_identity_sql`). So the queries run with `current_user = <e>_developer`, `session_user = <e>_studio`; without a developer login they run as `<e>_studio`, which is `NOINHERIT` (`lab/studio.py:119-122`). Neither is a superuser, and neither is a member of `pg_read_all_stats` or `pg_monitor` (their memberships: `lab/durable_runtime.py:1583-1592`; no repository grant of either role, searched `lab/` and `src/`). VERIFIED from source.

## PostgreSQL facts for the pinned server

- **Server**: `public.ecr.aws/supabase/postgres:17.6.1.166` (`lab/distro-image.lock.json:2`), server 17.6 with `pg_stat_statements` 1.11 available (`docs/evidence/distro-checks.json:52-54`). VERIFIED.
- **Already loaded**: the image's configuration has `shared_preload_libraries = 'pg_stat_statements, pgaudit, ...'` ([supabase/postgres 17.6.1.166 postgresql.conf.j2, line 690](https://github.com/supabase/postgres/blob/17.6.1.166/ansible/files/postgresql_config/postgresql.conf.j2)), Sbarbase starts it with that file (`lab/durable_runtime.py:825`), and the restore classifier expects exactly that preload set for the pinned image (`lab/restore_operation.py:239-243`). No `pg_stat_statements.*` setting is made there, so the defaults apply: `max` 5000 (PGC_POSTMASTER), `track` top (PGC_SUSET), `track_utility` on, `track_planning` off, `save` on ([pg_stat_statements.c:400-455 at REL_17_6](https://github.com/postgres/postgres/blob/REL_17_6/contrib/pg_stat_statements/pg_stat_statements.c)). VERIFIED from source; the running value was not read (no database connection in this study). Consequence: **statistics for every environment are already being collected** in shared memory today; only the SQL interface to read them is absent from environment databases.
- **Visibility**: `pg_stat_statements_internal` computes `is_allowed_role = has_privs_of_role(GetUserId(), ROLE_PG_READ_ALL_STATS)` (pgss.c:1650). For each entry it returns `userid`, `dbid`, `toplevel` and **every counter** to any caller; `queryid` and `query` only when `is_allowed_role || entry->key.userid == userid`, otherwise `queryid` NULL and `query = '<insufficient privilege>'` (pgss.c:1783-1840). `GetUserId()` is the current role, so after `SET ROLE anon` the caller sees the text of every `anon` entry in every database. The extension script grants the view to PUBLIC (`pg_stat_statements--1.10--1.11.sql`, "GRANT SELECT ON pg_stat_statements TO PUBLIC"). The [documentation](https://www.postgresql.org/docs/17/pgstatstatements.html) says the same: other users "can see the statistics, however, if the view has been installed in their database". VERIFIED.
- **Keys**: an entry is keyed by `(userid, dbid, queryid, toplevel)`, where `userid` is the role that executed the statement (the current role, so `anon` or `authenticated` for Data API traffic, not `<e>_rest`). VERIFIED (pgss.c key struct and `pgss_store`).
- **Reset**: 1.11 has `pg_stat_statements_reset(userid oid DEFAULT 0, dbid oid DEFAULT 0, queryid bigint DEFAULT 0, minmax_only boolean DEFAULT false) RETURNS timestamptz`, revoked from PUBLIC by the script, with no superuser check in C (pgss.c:1510-1523), so EXECUTE is the only gate. With only `dbid` set it removes the entries of that database and **leaves the global `dealloc` and `stats_reset` alone** (those are reset only when every entry was removed, pgss.c:2697-2735). It takes `pgss->lock` exclusively (pgss.c:2669). VERIFIED.
- **Sharing**: `max` is one hash table for the cluster. When full, `entry_dealloc` frees the least used 5 percent, at least 10 entries (pgss.c:100, :2096-2160). Query texts live in one external file that every read loads in full (`qtext_load_file`, pgss.c:2283). `pg_stat_statements_info` (`dealloc`, `stats_reset`) is cluster-wide. VERIFIED.
- **`pg_stat_activity`**: datid, pid, usesysid, application_name, backend_xid and backend_xmin are returned to every caller for every backend in the cluster; the rest only with `has_privs_of_role(GetUserId(), pg_read_all_stats)` or the privileges of the session's role, otherwise `query = '<insufficient privilege>'` and NULLs ([pgstatfuncs.c:37, :333-360, :617-650 at REL_17_6](https://github.com/postgres/postgres/blob/REL_17_6/src/backend/utils/adt/pgstatfuncs.c)). VERIFIED. This exists today, independently of this study.
- **pg_authid**: not publicly readable because it holds password hashes; `pg_roles` exposes `oid` and `rolname` to everyone ([pg_authid](https://www.postgresql.org/docs/17/catalog-pg-authid.html), [pg_roles](https://www.postgresql.org/docs/17/view-pg-roles.html)). A GRANT on a system catalog changes that database's `pg_class` (and for column grants `pg_attribute`) copy, so its effect is local to the database even for a shared catalog ([Tom Lane, pgsql-hackers, 2003](https://www.postgresql.org/message-id/26397.1063124649%40sss.pgh.pa.us)). INFERRED for 17.6, the source is old; open question 1.

## Sbarbase today

- `pg_stat_statements` is in `RESERVED_EXTENSIONS` (`lab/durable_runtime.py:602-605`), enforced by the event trigger `sbarbase_guard.reserved_schemas()` on `ddl_command_end` for `CREATE SCHEMA`, `ALTER SCHEMA`, `CREATE EXTENSION`, `ALTER EXTENSION` (`lab/durable_runtime.py:1611-1641`). It returns early when `session_user` is a superuser (`:1615-1617`). It does not fire on `DROP EXTENSION`. Documented in `docs/guides/database-access.md:100` and `CHANGELOG.md:107`. VERIFIED.
- supautils 3.4.0 ([supautils.nix](https://github.com/supabase/postgres/blob/17.6.1.166/nix/ext/supautils.nix)) lists `pg_stat_statements` among `privileged_extensions` and `pg_stat_statements.*` among `privileged_role_allowed_configs` ([supautils.conf.j2](https://github.com/supabase/postgres/blob/17.6.1.166/ansible/files/postgresql_config/supautils.conf.j2)). Its README says delegation to the superuser "also works for updating and dropping privileged extensions" ([supautils README](https://github.com/supabase/supautils#privileged-extensions)). So **a tenant can `DROP EXTENSION` a privileged extension in its database through supautils**, and the current guard would not stop it. VERIFIED from documentation; not exercised.
- The platform's schemas `sbarbase_rest`, `sbarbase_guard`, `sbarbase_empty` must belong to a superuser, checked by `OWNED_BY_OTHERS_SQL` (`lab/data_api.py:378-384`); `sbarbase_guard` may hold no extension (`lab/data_api.py:485-486`); names starting with `sbarbase` are refused to tenants (`lab/durable_runtime.py:1626`). The `sql_drop` guard pattern exists already (`lab/data_api.py:509-539`). VERIFIED.

### A gap that exists today

`lab/import_project.py:361-371` (`run_import`) reads every extension of the source and runs `CREATE SCHEMA IF NOT EXISTS <schema>; CREATE EXTENSION IF NOT EXISTS <name> WITH SCHEMA <schema>` through `target_sql`, which connects as `supabase_admin` (`lab/import_project.py:182-190`) with no `SET ROLE`. The reserved guard returns early for a superuser session, and only `import_inspect.INACTIVE_EXTENSIONS` (pg_cron, pg_net, pg_graphql, wrappers; `lab/import_inspect.py:40-45`) is skipped. Supabase creates `pg_stat_statements` in `extensions` in its projects ([Supabase docs, pg_stat_statements](https://github.com/supabase/supabase/blob/369927892fde63d07b199d4e80fe05ae780573d5/apps/docs/content/guides/database/extensions/pg_stat_statements.mdx), "create extension pg_stat_statements with schema extensions"). So **an environment imported from Supabase gets the raw, PUBLIC-readable `extensions.pg_stat_statements`**, with every environment's counters and, through the shared API roles, their `anon`/`authenticated`/`service_role` query text. The same path would create `dblink`, `http`, `postgres_fdw`, `pgsodium`, `pg_tle` or `pgroonga` if the source has them. VERIFIED from source; not reproduced live. This should be fixed on its own, before or independently of the proposal: skip `durable_runtime.RESERVED_EXTENSIONS` in that loop and report them as warnings in `import_inspect.assess`, and add a startup check that moves or refuses a `pg_stat_statements` extension found outside the platform's schema (the install below does this).

`lab/restore_sql.py:399-400` also lists `pg_stat_statements` among the extensions an archive may create. Whether that path can bring it into an environment database outside the platform's schema needs reading in context (open question 6).

## Options compared

| Option | Isolation | Tenant can undo | Studio works unmodified | Cost |
|---|---|---|---|---|
| A. Keep refusing (today) | full | n/a | no, warning | none |
| B. Create the extension normally, rely on the text rule | none: every counter and dbid of every environment; full text of shared API roles | n/a | yes | unacceptable |
| C. Raw extension plus a tenant side `WHERE dbid = ...` (what Heroku advises its Essential users) | none: the filter is advice, the raw view stays readable | yes | yes | unacceptable as enforcement |
| D. **Private extension, filtered SECURITY DEFINER function, view in `extensions`, scoped reset** | rows of this database and this environment's roles only | no (attack list) | yes, plus the `pg_authid` column grant | small: one schema, three functions, one view, one cluster role |
| E. Platform collector that copies per-database rows into a table in each environment | same as D at copy time | no | yes, if the table is named `pg_stat_statements` | a scheduler, staleness, a writer in every database, deltas on reset; more moving parts for the same result |
| F. `pg_stat_monitor` | same text rule as pg_stat_statements, cluster-wide buckets ([Percona docs](https://docs.percona.com/pg-stat-monitor/views.html)) | n/a | no, different view name | needs a restart and a second preload; no gain |
| G. Independent PostgreSQL per environment | full | n/a | yes, with a plain extension | the existing fallback; not the shared design |

D is the recommendation. E would only be worth it if the read cost of D (every call loads the whole text file) turned out to matter, and it can be added later behind the same view without changing what the tenant sees.

## Recommended design

### Objects

Per installation, in database `postgres`, by the superuser:

- `sbarbase_stats_reader`: `NOLOGIN`, `INHERIT`, `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`, `CONNECTION LIMIT 0`, member of `pg_read_all_stats` and of nothing else. It owns the filtered functions, so a mistake in them yields at most `pg_read_all_stats`, not the superuser. Nobody is a member of it.

Per environment database `<e>`, by the superuser:

- Schema `sbarbase_stats`, owned by the superuser, `REVOKE ALL ... FROM PUBLIC`, `USAGE` for `sbarbase_stats_reader` only.
- `CREATE EXTENSION pg_stat_statements WITH SCHEMA sbarbase_stats VERSION '1.11'`. Its view, `pg_stat_statements_info` and its functions are revoked from PUBLIC; `sbarbase_stats_reader` gets EXECUTE on `pg_stat_statements(boolean)` and `pg_stat_statements_reset(oid, oid, bigint, boolean)`. Using the extension (rather than a bare `LANGUAGE C` function) gives Studio's `pg_extension` check its row, a version the platform can update, and the restore classifier's existing allowance.
- `sbarbase_stats.environment_statements()`: SECURITY DEFINER, owner `sbarbase_stats_reader`, `SET search_path = pg_catalog, pg_temp`, no arguments, string body (not `BEGIN ATOMIC`, so no dependency on the extension's objects is recorded and `ALTER EXTENSION ... UPDATE` can drop and recreate them), explicit `RETURNS TABLE` with the 1.11 columns. Filter: `dbid` of `current_database()` and `userid` in this environment's roles (`<e>_*`) plus `anon`, `authenticated`, `service_role`.
- `extensions.pg_stat_statements`: a view over that function, owner superuser, SELECT for `<e>_developer` and `<e>_studio` only.
- `extensions.pg_stat_statements_reset(...)`: SECURITY DEFINER, owner `sbarbase_stats_reader`, same signature as 1.11, always resets `dbid = this database`, refuses another `dbid` or a `userid` outside the environment, and allows one reset per minute per database.
- `GRANT SELECT (oid, rolname) ON pg_catalog.pg_authid TO <e>_developer, <e>_studio`, in this database.
- A `sql_drop` event trigger refusing, for a non-superuser session, the drop of extension `pg_stat_statements` or of any object in `sbarbase_stats` or the two objects above.

### SQL sketch

Illustrative, not final; `{e}` is the runtime id checked against `e_[a-f0-9]{24}` first, `{su}` the superuser name, and the script starts with `data_api.SAFE` like the other platform scripts.

```sql
-- Once, database postgres.
SET search_path TO pg_catalog, pg_temp;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'sbarbase_stats_reader') THEN
    CREATE ROLE sbarbase_stats_reader NOLOGIN;
  END IF;
END $$;
ALTER ROLE sbarbase_stats_reader NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 0 PASSWORD NULL;
GRANT pg_read_all_stats TO sbarbase_stats_reader;
```

```sql
-- Each environment database {e}, as {su}.
SET search_path TO pg_catalog, pg_temp;
SET lock_timeout = '10s';
DO $$ BEGIN
  -- Squatting: a tenant object under a name this script creates is never replaced or called.
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_roles r ON r.oid = c.relowner
             WHERE c.oid = pg_catalog.to_regclass('extensions.pg_stat_statements') AND NOT r.rolsuper)
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
                JOIN pg_catalog.pg_roles r ON r.oid = p.proowner
                WHERE n.nspname = 'extensions' AND p.proname = 'pg_stat_statements_reset'
                  AND r.rolname NOT IN ('sbarbase_stats_reader') AND NOT r.rolsuper) THEN
    RAISE EXCEPTION 'extensions.pg_stat_statements or pg_stat_statements_reset belongs to the environment; query statistics stay off'
      USING ERRCODE = '42501';
  END IF;
END $$;
CREATE SCHEMA IF NOT EXISTS sbarbase_stats AUTHORIZATION {su};
REVOKE ALL ON SCHEMA sbarbase_stats FROM PUBLIC;
GRANT USAGE ON SCHEMA sbarbase_stats TO sbarbase_stats_reader;
-- An extension an import created elsewhere (lab/import_project.py:361-371) moves here.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_extension e JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace
             WHERE e.extname = 'pg_stat_statements' AND n.nspname <> 'sbarbase_stats') THEN
    ALTER EXTENSION pg_stat_statements SET SCHEMA sbarbase_stats;
  END IF;
END $$;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements WITH SCHEMA sbarbase_stats VERSION '1.11';
REVOKE ALL ON sbarbase_stats.pg_stat_statements, sbarbase_stats.pg_stat_statements_info FROM PUBLIC;
REVOKE ALL ON FUNCTION sbarbase_stats.pg_stat_statements(boolean), sbarbase_stats.pg_stat_statements_info(),
  sbarbase_stats.pg_stat_statements_reset(oid, oid, bigint, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION sbarbase_stats.pg_stat_statements(boolean),
  sbarbase_stats.pg_stat_statements_reset(oid, oid, bigint, boolean) TO sbarbase_stats_reader;

CREATE TABLE IF NOT EXISTS sbarbase_stats.last_reset (at timestamptz NOT NULL);
REVOKE ALL ON sbarbase_stats.last_reset FROM PUBLIC;
GRANT SELECT, INSERT, DELETE ON sbarbase_stats.last_reset TO sbarbase_stats_reader;

CREATE OR REPLACE FUNCTION sbarbase_stats.environment_statements()
  RETURNS TABLE (userid oid, dbid oid, toplevel boolean, queryid bigint, query text,
    plans bigint, total_plan_time double precision, min_plan_time double precision,
    max_plan_time double precision, mean_plan_time double precision, stddev_plan_time double precision,
    calls bigint, total_exec_time double precision, min_exec_time double precision,
    max_exec_time double precision, mean_exec_time double precision, stddev_exec_time double precision,
    rows bigint, shared_blks_hit bigint, shared_blks_read bigint, shared_blks_dirtied bigint,
    shared_blks_written bigint, local_blks_hit bigint, local_blks_read bigint, local_blks_dirtied bigint,
    local_blks_written bigint, temp_blks_read bigint, temp_blks_written bigint,
    shared_blk_read_time double precision, shared_blk_write_time double precision,
    local_blk_read_time double precision, local_blk_write_time double precision,
    temp_blk_read_time double precision, temp_blk_write_time double precision,
    wal_records bigint, wal_fpi bigint, wal_bytes numeric,
    jit_functions bigint, jit_generation_time double precision, jit_inlining_count bigint,
    jit_inlining_time double precision, jit_optimization_count bigint, jit_optimization_time double precision,
    jit_emission_count bigint, jit_emission_time double precision, jit_deform_count bigint,
    jit_deform_time double precision, stats_since timestamptz, minmax_stats_since timestamptz)
  LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $body$
  SELECT s.* FROM sbarbase_stats.pg_stat_statements(true) AS s
  WHERE s.dbid = (SELECT d.oid FROM pg_catalog.pg_database d WHERE d.datname = pg_catalog.current_database())
    AND s.userid IN (SELECT r.oid FROM pg_catalog.pg_roles r
                     WHERE pg_catalog.starts_with(r.rolname, '{e}_')
                        OR r.rolname IN ('anon', 'authenticated', 'service_role'))
$body$;
ALTER FUNCTION sbarbase_stats.environment_statements() OWNER TO sbarbase_stats_reader;
REVOKE ALL ON FUNCTION sbarbase_stats.environment_statements() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION sbarbase_stats.environment_statements() TO {e}_developer, {e}_studio;

CREATE OR REPLACE VIEW extensions.pg_stat_statements WITH (security_barrier) AS
  SELECT * FROM sbarbase_stats.environment_statements();
ALTER VIEW extensions.pg_stat_statements OWNER TO {su};
REVOKE ALL ON extensions.pg_stat_statements FROM PUBLIC;
GRANT SELECT ON extensions.pg_stat_statements TO {e}_developer, {e}_studio;

CREATE OR REPLACE FUNCTION extensions.pg_stat_statements_reset(userid oid DEFAULT 0, dbid oid DEFAULT 0,
    queryid bigint DEFAULT 0, minmax_only boolean DEFAULT false)
  RETURNS timestamptz LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE here oid := (SELECT d.oid FROM pg_catalog.pg_database d WHERE d.datname = pg_catalog.current_database());
BEGIN
  IF dbid OPERATOR(pg_catalog.<>) 0 AND dbid OPERATOR(pg_catalog.<>) here THEN
    RAISE EXCEPTION 'Only this environment''s statistics can be reset' USING ERRCODE = '42501';
  END IF;
  IF userid OPERATOR(pg_catalog.<>) 0 AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.oid = userid
      AND (pg_catalog.starts_with(r.rolname, '{e}_') OR r.rolname IN ('anon', 'authenticated', 'service_role'))) THEN
    RAISE EXCEPTION 'Only this environment''s roles can be reset' USING ERRCODE = '42501';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(<platform key>);
  IF EXISTS (SELECT 1 FROM sbarbase_stats.last_reset WHERE at > pg_catalog.now() - interval '1 minute') THEN
    RAISE EXCEPTION 'Statistics were reset less than a minute ago' USING ERRCODE = '53400';
  END IF;
  DELETE FROM sbarbase_stats.last_reset;
  INSERT INTO sbarbase_stats.last_reset VALUES (pg_catalog.now());
  RETURN sbarbase_stats.pg_stat_statements_reset(userid, here, queryid, minmax_only);
END $body$;
ALTER FUNCTION extensions.pg_stat_statements_reset(oid, oid, bigint, boolean) OWNER TO sbarbase_stats_reader;
REVOKE ALL ON FUNCTION extensions.pg_stat_statements_reset(oid, oid, bigint, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION extensions.pg_stat_statements_reset(oid, oid, bigint, boolean) TO {e}_developer, {e}_studio;

-- Only the two columns pg_roles already shows to everyone; rolpassword stays unreadable.
GRANT SELECT (oid, rolname) ON pg_catalog.pg_authid TO {e}_developer, {e}_studio;
```

The `sql_drop` guard follows `sbarbase_guard.exposed_schemas_dropped()` (`lab/data_api.py:509-539`): return for a superuser `session_user`, otherwise raise when `pg_event_trigger_dropped_objects()` contains `object_type = 'extension' AND object_name = 'pg_stat_statements'`, or any object whose `schema_name = 'sbarbase_stats'`, or `extensions.pg_stat_statements` / `extensions.pg_stat_statements_reset`. A tenant cannot drop those last ones directly anyway (not owner); the row matters for `DROP EXTENSION ... CASCADE` through supautils.

The comparisons in the reset function use `OPERATOR(pg_catalog.<>)` because `oid <> integer` would otherwise resolve operators through the pinned path only, which is already `pg_catalog, pg_temp`; the explicit form is belt and braces against a `pg_temp` operator, following the care `lab/data_api.py:416-419` takes with types. Whether `pg_temp` can still contribute an operator when listed last is a detail to confirm (open question 7).

## Why a tenant cannot tamper with it

The tenant controls `<e>_developer` (direct access, agents, `/v1` SQL) and through it `<e>_studio`'s session; it can create objects in `public` and `extensions`, can own extensions that supautils lets it create, and can set role to `anon`, `authenticated`, `service_role`, `<e>_auth`, `<e>_storage` (`lab/durable_runtime.py:1583-1592`).

| Attack | What stops it |
|---|---|
| Read `sbarbase_stats.pg_stat_statements` or call `sbarbase_stats.pg_stat_statements(true)` | no USAGE on the schema and no EXECUTE or SELECT on the objects (revoked from PUBLIC, granted only to the reader) |
| Reach the C function by OID (`'...'::regprocedure`, `pg_catalog` reflection) | name lookup needs schema USAGE; EXECUTE is checked at call time anyway and is not granted |
| Create its own `pg_stat_statements` extension or function, or `ALTER EXTENSION pg_stat_statements SET SCHEMA public` | reserved guard on `CREATE/ALTER EXTENSION` (`lab/durable_runtime.py:1631-1641`); `LANGUAGE C` needs a superuser |
| `DROP EXTENSION pg_stat_statements CASCADE` through supautils delegation, then recreate it | the new `sql_drop` guard; even without it, recreation is refused, so the effect would be losing its own page, and the start check reinstalls |
| `DROP`, `CREATE OR REPLACE`, `ALTER ... OWNER`, `ALTER FUNCTION ... SET search_path` on the view or wrappers | ownership: only the owner (superuser or the reader, which has no members) may |
| Pre-create `extensions.pg_stat_statements` or `extensions.pg_stat_statements_reset(...)` before the install (squatting) | the install refuses and never calls or replaces a tenant object; the page stays off for that environment, which is the safe failure |
| Pre-create schema `sbarbase_stats` | `sbarbase%` is reserved (`lab/durable_runtime.py:1626`); the install also checks the owner, as `OWNED_BY_OTHERS_SQL` does |
| Shadow names the definer bodies use (a `public.pg_database`, a temporary table, a function better matching `starts_with`) | `SET search_path = pg_catalog, pg_temp` on every function, every name qualified, operators qualified |
| A leaky function in its own `WHERE` (RAISE NOTICE of each row) to see rows before the filter | the filter is inside a SECURITY DEFINER SQL function, which the planner never inlines, so no outer qual reaches unfiltered rows |
| Pass arguments to choose another database | the read function takes none; the reset ignores `dbid` except to refuse another one |
| Spoof `current_database()` | it is the database of the connection; HBA and `CONNECT` keep `<e>` logins out of other databases |
| Reset other environments' statistics or the global `dealloc`/`stats_reset` | the wrapper always passes this `dbid`; a partial reset leaves the global counters alone (pgss.c:2697-2735) |
| Reset in a loop to contend `pgss->lock` for the whole cluster | one reset per minute per database, under an advisory lock |
| Read the text of other environments' `anon`/`authenticated` statements by `SET ROLE anon` | the raw view is unreachable; the filtered function keeps only this `dbid` |
| Become `sbarbase_stats_reader` or grant itself `pg_read_all_stats` | `NOCREATEROLE`, no ADMIN option anywhere, reader is `NOLOGIN` |
| Pass the grants on (to `anon`, to PUBLIC) | no grant option is given |
| Read `rolpassword` with the new `pg_authid` grant | the grant is column level, `oid` and `rolname` only |
| Use the `pg_authid` grant from another database | the grant is recorded in this database's catalog copy (inferred, open question 1) |
| Read the platform's own statements in its database (the superuser running guard and grant scripts, backups) | the `userid` allowlist drops every superuser and non-environment role |
| Set `pg_stat_statements.track = none` | per-session only, affects only its own sessions (the setting is SUSET; supautils allows it only for `supabase_privileged_role`) |

## What remains shared or leaked

- **The entry table is shared.** 5000 entries for the cluster, 5 percent least used evicted when full. One environment that issues many distinct statements (generated SQL, many tables, utility statements with distinct names) evicts the statistics of the others. An environment can also notice its own entries disappearing, which says, coarsely, that someone else is busy. This leaks no statement, time or count of another environment. Raising `pg_stat_statements.max` needs a restart and more shared memory; a per-environment cap does not exist in core. Inferred acceptable; record it in the guide.
- **The query text file is shared**, and each read of the view loads all of it (`qtext_load_file`). An environment polling the view costs I/O and CPU for everyone. Bounded by the Studio login's statement timeout (`lab/studio.py:125`), the connection budgets, and the reset rate; option E would remove it if measured to matter.
- **`stats_since` and `minmax_stats_since`** of its own entries only. No cross-environment timing is visible.
- **`queryid`** values are for its own entries only. Equal queries in two environments hash differently because relation OIDs differ; in any case the other side is never shown.
- **`pg_stat_activity`** is unchanged by this design and already shows every login the existence, database id, user id, application name and transaction ids of every other session on the server (pgstatfuncs.c, above). Heroku documents the same for its shared Essential plans ([Heroku help](https://help.heroku.com/NYMRTXEG/why-am-i-seeing-information-about-other-postgresql-processes-or-databases-that-aren-t-related-to-my-heroku-postgres-essential-database)). Studio's Database Connections page already filters to the current database; nothing here can make it show more, since it reads `pg_catalog.pg_stat_activity` directly.
- **Platform statements** in the environment's database are hidden by the role filter, not by normalization. Most password-bearing role statements are issued in database `postgres` (`Runtime.sql` defaults to it, `lab/durable_runtime.py:678-679`, used by `:1741`), but the filter must not depend on that.

## Interaction with what exists

- **RESERVED_EXTENSIONS** stays as it is: the tenant still may not create, alter or (new) drop the extension. Only the platform's own install passes, as a superuser session.
- **Developer schema guard**: unchanged; the new schema name is already reserved by prefix. The `sql_drop` guard is new.
- **`OWNED_BY_OTHERS_SQL`** (`lab/data_api.py:378-384`): extend with `sbarbase_stats`, accepting `sbarbase_stats_reader` as the only non-superuser owner of its functions and of the reset wrapper.
- **Studio start** (`lab/studio.py`, `role_sql`): the `pg_authid` grant and the view grants for `<e>_studio` belong here, re-applied at each start like the others; if the install is missing or refused, Studio still starts and the page shows the upstream warning.
- **Backups and restore** (`lab/backup.py:446`, full `pg_dump -Fc` of `<e>`): the dump will carry the schema, the extension, the functions with their owner, the view, the grants, and very likely the `pg_authid` column ACL (pg_dump dumps privilege changes on catalog objects relative to `pg_init_privs`; inferred). After restore the platform already re-runs its guard (`lab/backup.py:882`); it must also re-run the stats install and its present check, never trusting the archive's copy of a SECURITY DEFINER body. The reader role must exist before `pg_restore` reaches `ALTER FUNCTION ... OWNER TO sbarbase_stats_reader`, or that statement fails.
- **Recovery export** (`lab/recovery-export.py:165`) exports the environment for use elsewhere. Those objects mean nothing outside Sbarbase and reference a role that does not exist there. Either exclude them (`--exclude-schema=sbarbase_stats`, `--exclude-table=extensions.pg_stat_statements`, and whether `--exclude-extension` exists in the pinned `pg_dump` 17 and what `--exclude-schema` does to an extension in it are unknown; open question 5), or document that the target ignores their errors. The existing `sbarbase_guard` objects are in the same position today.
- **Import** (`lab/import_project.py:325-331`, `ensure_developer`): run the install right after `guard_sql` and `developer_grants_sql`; the extension is then "present" and the loop at `:361-371` skips it. The reserved-extension skip in that loop is a separate fix that should land regardless.
- **migrate-generation** (`lab/migrate-generation.py`) replaces the database container, possibly with a new major. The extension version moves with `ALTER EXTENSION pg_stat_statements UPDATE`, run by the platform; the string bodies keep that from failing on dependencies, and the install then rewrites the `RETURNS TABLE` for the new column set. The install should know the column list per extension version and refuse an unknown version, so the page fails closed instead of showing something unchecked.
- **Independent PostgreSQL fallback**: an environment on its own cluster could create the extension plainly. Using the same install there keeps one code path and hides the platform's own statements the same way; the `dbid` filter is then just redundant. Inferred; it depends on what else that cluster holds.

## What changes where

| File | Change |
|---|---|
| new `lab/query_stats.py` | `READER`, `reader_sql()` (cluster role, database `postgres`), `install_sql(e)`, `present_sql(e)` (owners, `prosecdef`, `proconfig @> search_path=pg_catalog, pg_temp`, no PUBLIC or API role privileges, extension in `sbarbase_stats` at a known version, the `sql_drop` trigger enabled `A`), and the per-version column list |
| `lab/durable_runtime.py` | create the reader once during runtime start; call install when `present_sql` is false next to `ensure_guard` (`:964-970`); `developer_grants_sql` (`:1601-1641`) gains the drop guard or calls into `query_stats`; comment at `:599-605` explains the platform's own copy |
| `lab/data_api.py` | `OWNED_BY_OTHERS_SQL` covers `sbarbase_stats` |
| `lab/studio.py` | `role_sql` grants for `<e>_studio` (view, reset, `pg_authid` columns) |
| `lab/import_project.py` | skip `RESERVED_EXTENSIONS` at `:361-371` and warn; install after `ensure_developer` |
| `lab/import_inspect.py` | report reserved source extensions as warnings |
| `lab/backup.py` | after restore (`:882`) also install and check |
| `lab/recovery-export.py` | exclusion flags, after open question 5 |
| `lab/rolling.py` | include the stats present check where `guard_present_sql` is used (`:653`) if rolling restarts are to repair it |
| `docs/guides/database-access.md` and `.ar.md` (`:100`) | `pg_stat_statements` is provided by the platform, filtered to the environment; the shared table and its eviction |
| `docs/guides/studio.md` and `.ar.md` | Query Performance works, its reset is per environment and at most once a minute |
| `docs/engineering/STUDIO-INTEGRATION.md:525` | advisors row and a Query Performance row |
| `CHANGELOG.md` | the change and the import fix, without claiming more than was measured |

## Tests that would prove it

Unit, `lab/test_query_stats.py` (no containers):

- `install_sql` refuses an invalid runtime id; every function in it has `SECURITY DEFINER` and `SET search_path = pg_catalog, pg_temp`; no statement grants to `PUBLIC`, `anon`, `authenticated` or `service_role`; no grant has `WITH GRANT OPTION`; the read function takes no arguments; the reset always passes this database; the `pg_authid` grant names exactly `(oid, rolname)`.
- `present_sql` fails on each single missing piece (a string level check like `lab/data-api-check.ts` does live).
- `durable_runtime.RESERVED_EXTENSIONS` still contains `pg_stat_statements` (`lab/test_database_access.py:111`).
- `import_project` skips every reserved extension and `import_inspect` warns about it.

Live, a new `lab/query-stats-check.py` on a disposable cluster with two environments `a` and `b` (needs the container headroom described in `lab/README.md`):

1. `b` runs marked statements as `b_developer` and through its Data API as `anon`. As `a_developer` and `a_studio`, `extensions.pg_stat_statements` returns no row with `dbid` other than `a`'s, no row of `b`'s roles and no `b` marker; the same as `anon` and `service_role` inside `a` returns permission denied.
2. `a`'s own `anon` statements appear with text, which the raw rule would hide from `a_developer`.
3. Each row of the attack table, executed and refused (or shown harmless), including a leaky `RAISE NOTICE` function in `WHERE`, a temporary table and a `public` function named like the ones the bodies call, a squatted `extensions.pg_stat_statements` before install, and `DROP EXTENSION pg_stat_statements CASCADE` through supautils.
4. Reset in `a` leaves `b`'s entries and `pg_stat_statements_info` intact; a second reset within the minute is refused; `dbid` of `b` is refused.
5. `SELECT rolpassword FROM pg_authid` is denied for `a_developer`; `SELECT oid, rolname FROM pg_authid` works in `a` and is denied for `b_developer` in `b` when only `a` has the grant (proves the per-database ACL).
6. Studio for `a`: Observability > Query Performance loads, sorting and the index advisor column work, reset works, the overview's slow query count is computed (a Studio window opened and checked as the repository's application tests require).
7. Backup and restore of `a`, and an import from a source that has `pg_stat_statements` in `extensions`: afterwards the extension is in `sbarbase_stats`, `present_sql` is true, and step 1 still holds.
8. The platform's password statements: run the paths that issue `ALTER ROLE ... PASSWORD` and assert no row visible to `a`'s logins contains the password; record, for the notebook, whether the raw view would have.
9. Eviction, to document the residual: fill the table from `b` with more than 5000 distinct statements and record how many of `a`'s entries survive. Written to `docs/evidence/query-stats-checks.json` with its scope.

## Open questions

1. Does `GRANT SELECT (oid, rolname) ON pg_catalog.pg_authid` work on 17.6 for a superuser, stay local to the database, and let Studio's join run? If it does not, the page cannot work without changing Studio, and option D shrinks to `queryMetrics`, the slow query count and SQL editor use.
2. How does hosted Supabase let Studio join `pg_authid`? `postgres` is a member of `pg_read_all_data` in this image (`docs/evidence/distro-checks.json:186`), which probably covers it; unknown, and irrelevant to the design except as context.
3. Does PostgREST's role switching record Data API statements under `anon`/`authenticated` as expected, and do `<e>_rest` sessions appear at all? Expected yes from the key definition; measure in test 1.
4. Constant normalization of utility statements in 17 (passwords in `ALTER ROLE`) is not relied on, but test 8 should record it.
5. `pg_dump` 17 options for leaving the stats objects out of a recovery export, and what `pg_restore` does with the catalog ACL line on a target without the roles.
6. What `lab/restore_sql.py:399-400` restores into, and whether an archive could bring `pg_stat_statements` outside `sbarbase_stats`; the install's move step covers it after the fact, but the planner should know.
7. Whether `pg_temp` listed last in a function's `search_path` can still supply an operator or function; PostgreSQL's guidance is to list it last ([CREATE FUNCTION, writing SECURITY DEFINER functions safely](https://www.postgresql.org/docs/17/sql-createfunction.html#SQL-CREATEFUNCTION-SECURITY)); qualified operators make the question moot.
8. Is one minute the right reset bound, and should reset be offered to agents (`/v1`, MCP) at all?
9. Is the `extensions` schema excluded by `supabase db dump` and `supabase db diff` against an Sbarbase environment, so the view does not appear in a tenant's migrations? Unknown.
10. Does the reader need `pg_read_all_stats` only, or would a per-environment reader be worth the extra roles? One shared reader is simpler and gives nothing to a tenant; a per-environment reader limits a bug to one environment's privileges only if pg_stat_statements had a per-database privilege, which it does not, so it buys nothing. Inferred.

## How others do it

- **Heroku Postgres Essential** runs many customer databases on one cluster. It documents that session metadata is visible across tenants and advises filtering by `dbid` ([Heroku help](https://help.heroku.com/NYMRTXEG/why-am-i-seeing-information-about-other-postgresql-processes-or-databases-that-aren-t-related-to-my-heroku-postgres-essential-database)), offers a platform wrapper `_heroku.pg_stat_statements_reset()` instead of the global reset ([Heroku help](https://help.heroku.com/NF3K4865/how-to-i-reset-the-statistics-for-a-heroku-postgres-database)), and does not offer its Expensive Queries feature on those plans ([Heroku plans](https://devcenter.heroku.com/articles/heroku-postgres-plans)). The closest precedent: a platform wrapper for the dangerous function, filtering left to the tenant. This design moves the filter into the wrapper too.
- **pganalyze** has monitoring users read statistics through a SECURITY DEFINER helper, `pganalyze.get_stat_statements(showtext)`, created by a superuser, and on providers without `pg_monitor` (Aiven) through per-role helper functions ([pganalyze, Aiven setup](https://pganalyze.com/docs/install/aiven/03_create_pg_stat_statements_helpers)). Same mechanism, for a trusted reader rather than a tenant.
- **Supabase, Neon, Aiven, Crunchy Bridge, RDS** give each project or service its own PostgreSQL, so pg_stat_statements is not shared between customers there; Supabase's guide simply creates it in `extensions` ([Supabase docs](https://github.com/supabase/supabase/blob/369927892fde63d07b199d4e80fe05ae780573d5/apps/docs/content/guides/database/extensions/pg_stat_statements.mdx)). Inferred from their published architecture; nothing to copy for the shared case.
- **Citus** solved tenant level statistics inside one database with `citus_stat_tenants`, granted to `pg_monitor`, i.e. an operator view, not a tenant one ([Citus blog](https://www.citusdata.com/blog/2023/05/12/tenant-monitoring-in-citus-and-postgres-with-citus-stat-tenants/), [Azure docs](https://learn.microsoft.com/en-us/azure/cosmos-db/postgresql/concepts-multi-tenant-monitoring)).
- **pg_stat_monitor** applies the same text rule as pg_stat_statements and shares its buckets cluster-wide ([Percona docs](https://docs.percona.com/pg-stat-monitor/views.html)); it does not change the isolation question.

## Sources

- Studio at commit `369927892fde63d07b199d4e80fe05ae780573d5`: [WithStatements.tsx](https://github.com/supabase/supabase/blob/369927892fde63d07b199d4e80fe05ae780573d5/apps/studio/components/interfaces/QueryPerformance/WithStatements/WithStatements.tsx), [Reports.constants.ts](https://github.com/supabase/supabase/blob/369927892fde63d07b199d4e80fe05ae780573d5/apps/studio/components/interfaces/Reports/Reports.constants.ts), [useSlowQueriesCount.ts](https://github.com/supabase/supabase/blob/369927892fde63d07b199d4e80fe05ae780573d5/apps/studio/components/interfaces/Observability/useSlowQueriesCount.ts), [index-advisor.ts](https://github.com/supabase/supabase/blob/369927892fde63d07b199d4e80fe05ae780573d5/packages/pg-meta/src/sql/studio/advisor/index-advisor.ts), [lints.ts](https://github.com/supabase/supabase/blob/369927892fde63d07b199d4e80fe05ae780573d5/packages/pg-meta/src/sql/studio/advisor/lints.ts), [activity.ts](https://github.com/supabase/supabase/blob/369927892fde63d07b199d4e80fe05ae780573d5/packages/pg-meta/src/sql/studio/database/activity.ts)
- PostgreSQL 17.6: [pg_stat_statements.c](https://github.com/postgres/postgres/blob/REL_17_6/contrib/pg_stat_statements/pg_stat_statements.c), [pg_stat_statements--1.10--1.11.sql](https://github.com/postgres/postgres/blob/REL_17_6/contrib/pg_stat_statements/pg_stat_statements--1.10--1.11.sql), [pgstatfuncs.c](https://github.com/postgres/postgres/blob/REL_17_6/src/backend/utils/adt/pgstatfuncs.c), [pg_stat_statements docs](https://www.postgresql.org/docs/17/pgstatstatements.html)
- supabase/postgres 17.6.1.166: [postgresql.conf.j2](https://github.com/supabase/postgres/blob/17.6.1.166/ansible/files/postgresql_config/postgresql.conf.j2), [supautils.conf.j2](https://github.com/supabase/postgres/blob/17.6.1.166/ansible/files/postgresql_config/supautils.conf.j2), [supautils README](https://github.com/supabase/supautils#privileged-extensions)
