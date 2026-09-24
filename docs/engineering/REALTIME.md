# Realtime per environment

Status: 2026-09-24. Implemented; checked by `lab/realtime-check.ts` in the CI Docker job. Operator guide: [Realtime](../guides/realtime.md).

## Shape

One upstream Realtime container per environment that turns it on (`lab/realtime-image.lock.json`, tier `production.realtime`, 320 MiB, 0.25 CPU), never one shared Realtime. The pinned Realtime names the replication slot for database broadcasts `supabase_realtime_messages_replication_slot_<SLOT_NAME_SUFFIX>`, the same for every tenant of one process, and slot names are unique across a PostgreSQL cluster. Environments here share one cluster, so a shared Realtime would let only one environment hold that slot. A per-environment process gets its own suffix (the first 12 of the environment's hex digits, so the longest slot name stays within PostgreSQL's 63 characters; a truncated name crashes the pinned Realtime's replication), its own metadata schema `_realtime` inside the environment's own database, and no cluster of Realtime nodes to join.

The start placement counts one `production.realtime` row per environment with Realtime on (`resource_policy.start_placement(n, realtime)`), so turning it on is refused with the same restart-headroom rule as a new environment.

## Login

`<runtime>_realtime`: LOGIN, REPLICATION, NOSUPERUSER, no CREATEDB, CREATEROLE or BYPASSRLS, a connection limit of `connection_budget.REALTIME_CONNECTIONS` (sixteen: its ten tenant pools, two metadata connections and a probe, with room), checked against the cluster's spare connections before it turns on, and one HBA rule to its own database. It holds CONNECT, CREATE and TEMPORARY on that database, membership in `supabase_realtime_admin` (which owns Realtime's objects, as upstream arranges), and `anon`, `authenticated` and `service_role` with `INHERIT FALSE, SET TRUE`, so it can take a visitor's role to apply row level security but never gains their privileges. It may also SET `log_min_messages`, which Realtime's change poller lowers for its own session; no other superuser setting is granted. The environment database limit grows by the same amount while Realtime is on (`connection_budget.database_limit`).

Realtime's own migrations, in `_realtime` and in the tenant's `realtime` schema, include `ALTER DEFAULT PRIVILEGES FOR ROLE supabase_admin` and ownership changes that only a superuser can make, and upstream runs them as the cluster administrator. Here the login is made SUPERUSER only for those migrations: before the container starts on first enable, or when the pinned image changed since the last migration (`endpoints.json` records the image each environment migrated with). Once the tenant is created, the administrator grants the login rights on every object in `realtime` and `_realtime` (the migrations leave some owned by other roles, such as `realtime.schema_migrations`), then the login is made NOSUPERUSER again and every session it opened is terminated, in a `finally`, so a failed start also ends the window. The administrator password itself never reaches Realtime.

## Tenant

The tenant's external id is the 24 hex digits: Realtime reads the tenant from the first label of the Host header, and the gateway sends `<hex>.realtime`. Its `postgres_cdc_rls` extension reads the publication `supabase_realtime`, created `FOR TABLES IN SCHEMA public`, so every public table, present and future, is published and row level security decides delivery.

## Gateway

Bun's `node:http` does not deliver bytes written to an upgraded socket (checked on Bun 1.3.11 and 1.3.14), so the console listener gained a small TCP front (`src/http/local-server.ts`): it reads the first request head of each connection, hands ordinary connections byte for byte to the HTTP server on a private loopback port, and pipes accepted Realtime sockets to the environment's Realtime. `src/gateway/realtime.ts` decides first: a live publishable key of that environment, not paused, Realtime on. The publishable key is swapped for a ten-year anon JWT Realtime accepts, as upstream's Kong does, and cookies and credentials are not forwarded. `POST /realtime/v1/api/broadcast` goes through the ordinary gateway; the rest of Realtime's API, including tenant management, is not routed. The reference TLS proxy bridges the socket across TLS.

## Not covered

Backups include `_realtime` and `realtime`; a restore stops and restarts the environment's Realtime with its Auth and REST. Realtime's own dashboards and metrics endpoints are not exposed. Sustained load and memory under many subscribers are not measured.
