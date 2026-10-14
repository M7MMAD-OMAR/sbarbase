[العربية](2026-10-14-select-2026-landscape.ar.md)

# Upstream review: Supabase Select 2026 and current drift

Date: 2026-10-14. Scope: the announcements of Supabase Select 2026 (2 October 2026) and the drift of every pinned component on this date. This is a landscape review, not a release adoption: no pin changes here. Each adoption gets its own entry written by `lab/pin_update.py stage`, as the [update policy](../engineering/UPSTREAM-UPDATE-POLICY.md) requires. The ordered work is in the [upstream currency plan](../engineering/plans/2026-10-14-upstream-currency.md).

## What changed upstream

Sources read: the official recap and its three theme posts, the Turso announcement, the self-hosting changelog, the release lists of each pinned repository and the official self-hosting compose file. Figures that appear only in the event video description (a "Supabase Spark" instance type, "2x" for OrioleDB, "1,000 failovers", "9x" cold start, "90%" less idle memory, an Apache 2.0 licence for Multigres) were not found in any written source on this date. The written figure for OrioleDB is "up to 1.8x" on TPC-C derived benchmarks. These claims are treated as unverified, not as false.

| Announcement | Upstream status | Reaches a self-hosted installation |
|---|---|---|
| Native local runtime in the CLI (no Docker) | alpha, off by default | developer tool only |
| Declarative Schemas 2.0 with the `pg-delta` diff engine | CLI default for new projects | developer tool; usable against our direct database port |
| Supabase Compute (long running services) | private alpha | hosted only |
| An MCP server for your own app, as an Edge Function | published | needs the Auth OAuth 2.1 server and asymmetric signing keys |
| `@supabase/server`, the new Supabase Middleware and a headless app template | published | libraries for application code that talk to the Supabase APIs |
| Config in code: `config.toml` with `supabase config pull` and `supabase pull` | CLI | syncs with the hosted dashboard; our environments are configured through the console and management API |
| Scoped personal access tokens | generally available | hosted Management API only |
| Enterprise-Managed Auth for MCP (Okta first) | generally available on Team and Enterprise plans | hosted MCP server only |
| MCP elicitations before destructive actions | on by default in Supabase's MCP server | hosted MCP server; "not a guarantee" in Supabase's own words |
| `query_logs` MCP tool, Health Check Advisors | published | depend on the hosted log pipeline (Logflare), which we do not run |
| Explorer and Notebooks | rolling out in the dashboard | not stated for self-hosting |
| Database Connections viewer in Studio | published | yes, explicitly including self-hosted |
| Multigres (multi-node high availability) | private alpha on the platform; the project is open source | possible later, not mature |
| OrioleDB storage engine | public beta, chosen when a project is created | a separate image; B-tree indexes only |
| Agent prompts, the updated Supabase agent skill and the Supabase plugin | published | guidance for agents on the hosted platform; a reference for our agent access plan |
| dbarena, open database benchmarks | published with code and raw data | a reference, not a component |
| Turso acquisition | announced with a USD 150M round | no product yet; integration "in the coming months" |
| Pipelines to BigQuery, ClickHouse, DuckLake, Snowflake | public alpha on paid plans | not stated for self-hosting |

## Current drift, 2026-10-14

Output of `/usr/bin/python3 lab/upstream_watch.py` on this date (exit 3: drift without a review entry). "Compose" is the version the official self-hosting compose file pins, the set upstream tests together.

| Component | Pinned | Newest in line | Newest | Compose | Note |
|---|---|---|---|---|---|
| PostgreSQL distribution | 17.6.1.166 | 17.11.0.004 | 17.11.0.004 | 17.6.1.136 | five PostgreSQL minor releases behind; we are already ahead of compose |
| Auth | v2.196.0 | v2.197.0 | v2.197.0 | v2.196.0 | equal to compose |
| REST | v14.15 | v14.18 | v16.4 | v14.17 | upstream maintains the v14 line beside v16 |
| Storage | v1.73.1 | v1.82.1 | v1.82.1 | v1.74.0 | runs tenant migrations; one shared process |
| Realtime | v2.138.1 | v2.143.4 | v2.143.4 | v2.134.10 | ahead of compose |
| Edge Functions | v1.77.0 | v1.77.4 | v1.77.4 | v1.76.2 | ahead of compose |
| Studio | 2026.09.07-sha-7996410 | 2026.10.12-sha-3699278 | same | 2026.09.07-sha-7996410 | equal to compose; newer builds carry the Connections viewer |
| postgres-meta | v0.99.0 | v0.100.0 | v0.100.0 | v0.99.0 | equal to compose |
| Stock PostgreSQL fixture | 17-alpine | moving tag | 18-alpine | | the tag now resolves to a new digest (a rebuild); a newer major line exists |

## Affected surfaces

- **Studio and postgres-meta.** The Connections viewer can cancel queries and terminate connections. On the shared cluster this must be proven to stay inside one environment for the scoped `<env>_studio` login. No grant of `pg_read_all_stats` or `pg_signal_backend` was found in the source, which keeps a login from cancelling other sessions. Any login can still see in `pg_stat_activity` that other roles' sessions exist, without their query text, so the live check will likely find sessions of other environments visible, and the plan's F5 fallback then applies.
- **Auth, REST, Storage, Realtime, Functions.** Asymmetric signing keys change how every service verifies a token. Realtime supports tenants that carry only a JWKS from v2.140.4, so asymmetric keys depend on a Realtime update first. Today every environment signs with a shared secret (`GOTRUE_JWT_SECRET`, `PGRST_JWT_SECRET` in `lab/durable_runtime.py`) and nothing enables the Auth OAuth 2.1 server (`GOTRUE_OAUTH_SERVER_ENABLED`, documented by upstream as experimental for self-hosting).
- **Storage.** Releases since v1.73.1 include tenant migration handling (v1.80.0 treats an unrecognised tenant migration as ahead). An image rollback after a migration is not a schema rollback, so a backup taken before the change is part of the rollback.
- **PostgreSQL distribution.** Replacing it is a container generation change through `lab/migrate-generation.py` only, which today refuses an image change and needs the plan's prerequisite P1 first, with the bootstrap, HBA, extension, backup and restore checkpoints at risk. `COMPLIANCE.md` already records 17.11.0.003 as an unadopted candidate. Upstream's [17.11 breaking-changes notice](https://supabase.com/changelog/postgres-15-19-17-11-breaking-changes) adds four checks with detection queries: ltree indexes to rebuild, pgcrypto data encrypted with Blowfish or CAST5 to re-encrypt, btree_gist float indexes to rebuild, and custom operators with non-built-in estimators that cannot be recreated through dump and restore without a superuser.
- **REST v16.** Filters, orders and limits that name an embedded table instead of its alias are deprecated, and responses gain a `Vary` header. This needs a compatibility run with supabase-js before adoption.
- **Tooling.** The policy names the CLI as a pinned component, but no CLI version is pinned in this repository.

## Breaking changes and migrations

None are adopted by this entry. Known items to carry into the per-release entries: REST v16 target-name deprecation, Storage and Realtime schema migrations inside environment databases, the PostgreSQL generation change with the four checks of the 17.11 notice (ltree, pgcrypto, btree_gist, custom operators), and upstream self-hosting defaults that changed in 2026 (Analytics and Vector opt-in, Studio's default role moving to `postgres`). We already run without Analytics and Vector and give Studio its own scoped login, so these two defaults need a check but no migration.

## Decision

- **Adopt, one component at a time, in the plan's order:** the patch and minor updates within each line, Studio with the Connections viewer isolation check, the rebuilt stock PostgreSQL fixture (plan step A8), then the PostgreSQL distribution as a generation change, last.
- **Build:** continuous tracking (done locally: `lab/upstream_watch.py` and a weekly workflow), asymmetric signing keys, then an optional per-environment OAuth 2.1 server, then a compatibility test of Supabase's app MCP server pattern.
- **Defer:** REST v16 until a supabase-js compatibility run passes; PostgreSQL 18 (the stock fixture's `18-alpine` line) while we run the 17 line, recorded by a deferral entry with A8; OrioleDB until it leaves beta and keeps logical replication and our extensions working; Multigres until a beta with documented self-hosting exists and our single-server soak is done.
- **Adopt as tooling, plan item F7:** Declarative Schemas 2.0 and `pg-delta`, tested against the direct database port.
- **Test for compatibility, plan item F3:** `@supabase/server`, the Supabase Middleware and the headless app template, with the app MCP server block.
- **Reference for the agent access plan, no work here:** the agent prompts, the agent skill and the Supabase plugin.
- **Adopt as a pattern:** MCP elicitations, only as an extra prompt where the client supports it (F8 in the upstream currency plan, applied to the agent access MCP endpoint once its mutation stage exists); `query_logs` and Health Check Advisors through their self-hosted counterparts: health signals from gateway counters (F6 in the upstream currency plan) and the agent `get_logs` tool (agent access plan).
- **Check during A6:** Explorer and Notebooks, if a newer Studio build offers them without the hosted log pipeline.
- **Watch only, no work now (plan section 7):** Supabase Compute, Pipelines, the Turso products and the native CLI runtime.
- **Not applicable:** the hosted scoped tokens and Enterprise-Managed Auth for MCP, which serve the hosted Management API and MCP server; config in code, which syncs with the hosted dashboard; dbarena is a reference.
