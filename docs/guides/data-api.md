[العربية](data-api.ar.md)

# Data API

The Data API is the environment's PostgREST: what `supabase.from()` and `supabase.rpc()` reach. By default it serves the `public` schema only. An application that keeps its tables in a schema of its own exposes that schema here, as in Supabase's Data API settings.

## Expose a schema

1. Create the schema and its tables first, with a migration or in Studio. A schema that does not exist yet cannot be exposed.
2. Give the API roles access to it, as on Supabase:

```sql
grant usage on schema app to anon, authenticated, service_role;
grant select, insert, update, delete on all tables in schema app to anon, authenticated, service_role;
grant usage, select on all sequences in schema app to anon, authenticated, service_role;
grant execute on all functions in schema app to anon, authenticated, service_role;
alter table app.items enable row level security;
```

3. Open the environment in the console, then **Database connection**, then **Data API**. Under **Exposed schemas** list the schemas in order, for example `public, app`, and press **Save Data API settings**. It shows **Applying…**, then **Applied**, usually within seconds. The API is not restarted.

The first schema in the list is the default. Reach the others from supabase-js:

```js
const { data } = await supabase.schema('app').from('items').select()
```

or with the `Accept-Profile: app` header (reads) and `Content-Profile: app` (writes).

Leave **Exposed schemas** empty to turn the Data API off: every table then answers 404, and the console says **Off**.

**Extra search path** is where PostgREST looks for types and functions that a query uses without a schema name, for example `public, extensions`. **Maximum rows per request** caps every answer; leave it empty for no limit.

## From the Supabase CLI or an agent

The same settings are at `GET` and `PATCH /v1/projects/{ref}/postgrest`, Supabase's Management API endpoint for the `[api]` section of `config.toml`. `GET` answers what the API serves now; a change appears there once it is applied:

```toml
[api]
schemas = ["public", "app"]
extra_search_path = ["public", "extensions"]
max_rows = 1000
```

A `PATCH` changes only the fields it names. The answer never contains the signing secret. It answers `200` as soon as the change is saved: if the database then refuses it, for example because a schema does not exist, `GET` keeps showing the previous schemas, and the reason is only in the console. An extra search path cannot be empty (`supabase config push` sends `""` for `[]`: list `public` instead). A read-only access token can read the settings and cannot change them. See [agents](agents.md).

## What is refused

- A schema that does not exist in the environment's database. The console shows which one and what is still served; nothing changes, not even after a restart.
- Platform schemas, whatever the grants say: `auth`, `storage`, `vault`, `realtime`, `_realtime`, `extensions`, `cron`, `net`, `graphql`, `pgmq`, `pgsodium`, `pgsodium_masks`, `pgtle`, `pgbouncer`, `etl`, `_analytics`, `_supavisor`, `supabase_functions`, `supabase_migrations`, `information_schema`, anything starting with `pg_`, `sbarbase` or `supabase`. Exposing one would publish users, files or secrets.
- Names that need quoting: use lowercase letters, digits and underscores, starting with a letter or underscore.
- `graphql_public`: GraphQL is not served. Supabase's `config.toml` lists it by default; remove it before `supabase config push`.
- More than 16 schemas, or the same schema twice.

While a schema is exposed, or named in the extra search path, the environment's database refuses to rename or drop it. PostgREST stops answering every request, not only that schema's, as soon as one exposed schema is gone. Remove it from **Exposed schemas** first, wait for **Applied**, then rename or drop it. A rename or drop of any schema runs at the read committed isolation level, PostgreSQL's default; inside a `repeatable read` or `serializable` transaction it is refused.

When the environment starts or is restored, Sbarbase writes the settings last applied again; a database session of your own that holds the platform's Data API lock at that moment is ended. If a schema goes missing anyway, for example after restoring an older backup, Sbarbase serves the remaining schemas when the environment starts and the section shows the one it left out. Create it again and restart Sbarbase or save the settings to serve it. If no exposed schema is left, the API answers 404 to every table rather than serving `public` in their place. An environment restored on another installation keeps the settings its database brings, when they are valid.

## Limits

- Exposing a schema grants nothing. Without the grants above, calls answer `permission denied`; without row level security, anyone with the publishable key reads the table.
- The security advisor checks tables in `public` only.
- Each environment's PostgREST keeps a pool of 3 database connections; `db_pool` cannot be changed.
- Code that runs as the environment's own API database login, for example a function that switches back to it, cannot choose which schema is served, but it can make a reload fail. The API then answers `404` for every table until Sbarbase restarts. This stays inside your environment and reaches nothing of another.
- Rolling back to a release before the Data API settings recreates each environment's API with the old configuration, which serves `public` only, including for an environment where you turned the Data API off, until the newer release runs again.
- GraphQL is not served.
- A Studio session that is open restarts when the settings change, so it shows the same schemas as the API.
- These settings are kept in the environment's own database, in the platform's `sbarbase_rest` schema, so another environment never sees them. The first start after upgrading recreates each environment's API container once.

How it works and what was measured: [Data API schemas](../engineering/DATA-API-SCHEMAS.md).
