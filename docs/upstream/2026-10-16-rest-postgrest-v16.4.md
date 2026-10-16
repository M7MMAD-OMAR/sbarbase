# Upstream review: rest public.ecr.aws/supabase/postgrest:v16.4

- Date: 2026-10-16
- Component: rest (lab/images.lock.json)
- Pin: unchanged, v14.18 after step A1

## What changed

PostgREST v16 ([v16.0](https://github.com/PostgREST/postgrest/releases/tag/v16.0) to [v16.4](https://github.com/PostgREST/postgrest/releases/tag/v16.4), and "Migration to v16" in the upstream CHANGELOG) is a major release:

- PostgreSQL 13 support is dropped.
- Startup fails when `db-schemas` names `pg_catalog` or `information_schema`.
- `Prefer: timezone` with lenient handling no longer accepts an invalid time zone.
- `jwt-role-claim-key` moves to RFC 9535 JSON Path; from v16.2 the old syntax still works with a warning.
- `url-use-legacy-target-names`: filters, orders and limits on an embedded table's name while an alias exists are deprecated, with a log warning and a `Warning` response header.
- A `Vary` header is added to responses.
- Graceful shutdown and `server-reuseport` change how a stop behaves.

## Affected surfaces

REST in every environment, the gateway in front of it (the `Vary` header reaches caches and the drain and pause leases meet the new shutdown), and client code that relies on the legacy target names.

## Breaking changes and migrations

The items above. No schema migrations.

## Decision

Defer. B1 in the [upstream currency plan](../engineering/plans/2026-10-14-upstream-currency.md) takes it after A1 and a passing supabase-js compatibility run; the compose file still pins v14.17. The v14 line keeps receiving the same JWT fixes (v14.18 carries the v16.3 ones).
