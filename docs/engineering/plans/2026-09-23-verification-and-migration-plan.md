# Verification, hierarchy follow-ups and migration into Sbarbase

Written 2026-09-23 after the hierarchy change (installation > organization > project > environment) and the documentation restructuring. It answers three questions: what is verified today, what the hierarchy change still leaves open, and what exactly to build so that moving from Supabase Cloud or another self-hosted setup into Sbarbase is easy and checked. It complements the [roadmap](2026-09-23-roadmap.md) and does not replace its milestone order; section 5 says where each step fits.

Nothing here claims production readiness. Every step ends in a check that can fail.

## 1. Verification run, 2026-09-23

Run in a clean cloud container (not the development workstation): no Docker daemon, running as root, `/usr/bin/python3` 3.11, so the suite was also run with a Python 3.14 interpreter that has `cryptography`.

| Suite | Result |
|---|---|
| `bun test` | 93 pass, 0 fail, 19 files |
| `bun run typecheck:ui` | passes |
| Website build and tests | build OK, 2 pass |
| Python suite, 3.14 | 628 tests; 5 failures left, all explained by this host (below) |
| CI on `main` (run for f1eda8c) | **red**: one Python failure, fixed in this change |

Defects found and fixed in this change:

1. **CI red on `main`.** `test_io_device_takes_the_device_from_a_subvolume_mount_source` expected the device `findmnt` reports. Since the partition fix, `io_device` returns the whole disk (`/dev/sda` for `/dev/sda1`), which is correct for `io.max`. The workstation mounts `/` from device-mapper, a whole device, so the test only failed on a partitioned host such as the GitHub runner. The test now expects `whole_disk(device)`.
2. **Five unit tests were not hermetic.** The restart check added by `3a046ee` (`resource_policy.restart_fits` with `owned_usage_bytes()`) calls `docker ps` and `docker stats` from inside `Runtime.provision`. Tests that mock every other admission gate therefore failed with "Resource measurement unavailable" on any host without a running daemon. They now mock that measurement too: `test_resource_admission`, `test_pressure_admission`, `test_connection_budget` and two in `test_native_stages`.

The five remaining failures on this container are environmental, not defects: tests that check a permission refusal cannot see one as root (`test_supervisor_unit` dry run, the world-readable bootstrap file), and `test_server_acceptance` shells out to the host's `/usr/bin/python3`, which is 3.11 here. On CI (Ubuntu 26.04, non-root, Docker present) they pass.

**Step 1.1 (open).** Make these tests state their host needs instead of failing: skip with a reason when `os.geteuid()==0` for permission-refusal assertions, and when `/usr/bin/python3` is older than 3.14 for the acceptance script tests. Check: the suite reports `OK (skipped=N)` in this container and `OK` with no skips on CI.

**Step 1.2 (open).** Add a CI job that runs the Python suite with no Docker socket (`DOCKER_HOST=unix:///nonexistent`) so a new unit test that reaches the daemon fails in review, not on the next machine.

## 2. What the hierarchy change still leaves open

An audit of `src/control`, `src/gateway`, `lab/`, `ui/`, tests and docs against the four-level model. Correct today: foreign keys run organization > project > environment; roles live on the organization and every read walks environment > project > organization; an unknown id and a forbidden id both return 403, so cross-organization access by id to projects, environments, provisioning, keys, connection details and mail is refused; databases, logins, Storage tenants and containers are named only by the opaque runtime id, so a project move needs no renames.

Open items, ordered. Each carries the test that proves it.

| # | Item | Evidence | Step | Test |
|---|---|---|---|---|
| H1 | **Done 2026-09-23.** `GET /management/v1/notifications` returns installation-wide events to an owner or admin of *any* organization, including other organizations' ids and failure reasons. Documented as intended (`docs/reference/api.md`), but it contradicts "clients do not see each other". | `src/control/http.ts:93-104`, `catalog.ts` `listNotifications` | Filtered by the organizations where the actor is owner or admin; an event with only a runtime or environment belongs to the organization that owns it; events with no organization go to owners and admins of the bootstrap organization. A dedicated installation-operator role remains a later option. | Owner of A never sees an event whose organization is B. |
| H2 | **Done 2026-09-23.** Moving a project cancels its queued job. This is intended (the requester's authority belonged to the old organization; `tests/provision.test.ts` asserts it), but the move neither says so nor updates `provision_jobs.organization`, so later events for a succeeded job name the old organization. | `catalog.ts` `transferProject`, `notificationScope` | Update `provision_jobs.organization` in the same transaction and return the cancelled environments to the caller. | After a move, a routing pause event names the new organization. |
| H3 | **Partly done 2026-09-23**: create organization (installation operators only), list members, retry, with console screens. Member changes, move, rename and delete stay internal until invitations and key revocation on move exist (milestone 5). No routes to create an organization, manage members, move, rename, delete or retry. | `http.ts:123-127` | Add them with the same role checks the catalog already enforces. Delete refuses while an environment is provisioned and revokes its keys. | Deleting an organization that still has projects is refused; a deleted environment's key returns 401. |
| H4 | **Done 2026-09-23.** Project names are not unique per organization; an environment name clash returns 500. | `catalog.ts:114-131`, `http.ts:146` | `UNIQUE(organization,name)`, map constraint errors to 409. | Duplicate names return 409 over HTTP. |
| H5 | **Done 2026-09-23** (version 2; a catalog that already holds a duplicate stays at 1 until renamed, and a newer version is refused). No catalog schema versioning, so H4 cannot reach an existing catalog. | `catalog.ts:187-191` | `PRAGMA user_version` migration ladder, run inside one transaction at open. | Opening a version-0 catalog file migrates it and keeps every row. |
| H6 | **API part done 2026-09-23** (`ENVIRONMENT_LIMIT`, 409 before queueing; lifting the number waits for the real server). The four-environment guard is per installation and checked only by the worker, after the catalog queued the request. | `lab/durable_runtime.py:341` | Refuse with 409 in the catalog before queueing; then measure and lift the guard on the real server (roadmap milestone 1, step 4). | The fifth environment is refused at the API and no job is queued. |
| H7 | **Recorded 2026-09-23** (`ownership` in the export payload, from `recovery_bundle.catalog_ownership`; re-linking on restore still open). Recovery exports record only the runtime id. | `lab/recovery-export.py:170` | Add organization, project and environment ids to the bundle manifest. | A restore on a second installation re-links the environment to its project. |
| H8 | **Done 2026-09-23** (`tests/hierarchy.test.ts`). A viewer calling `GET keys` or `DELETE keys/{id}` is untested; so is the mail route called by another organization's member. | `tests/key-http.test.ts` | Add the tests. | 403 in both cases, key stays active. |
| H9 | **Settled 2026-09-23**: the column keeps its name (renaming stored key data buys nothing) and `keys.ts` now says it holds the runtime id; "10 or 100 projects" stays because it quotes the question users ask. Naming: `environment` in `keys.ts` and the gateway means the runtime id; status and deployment docs state capacity in "projects" where the guard counts environments. | `keys.ts:15`, `docs/reference/status.md` | Rename to `runtime`; say "environments". | `test_docs_links.py` and `bun test` pass. |

The console (`ui/`) covers organization select > projects > environments > connection. Screens for H3 follow the routes.

## 3. Competitors, and what Sbarbase should own

| Option | Many projects on one host | Backups | Upgrades | Studio login | Import tooling |
|---|---|---|---|---|---|
| Supabase Cloud | yes, managed, billed per project | daily; PITR is a paid add-on | managed | platform login | CLI dump, restore to a new project |
| Self-hosted compose | no, one project per install ([#4907](https://github.com/orgs/supabase/discussions/4907)) | do it yourself | manual from the changelog | Kong basic auth | [restore from platform](https://supabase.com/docs/guides/self-hosting/restore-from-platform) guide |
| supabase-multitenant | yes, shared Postgres, shared service logins | not documented | not documented | one shared Studio behind Authelia | none |
| supafleet | yes, database per tenant | guide | Renovate pins | web UI | migration guide |
| Coolify, Dokploy, Easypanel templates | one full stack per template | dashboard backup fails on Supabase Postgres ([coolify#2977](https://github.com/coollabsio/coolify/issues/2977)) | breaks things | default passwords | none |
| Pigsty Supabase module | one stack per compose | pgBackRest PITR | infrastructure as code | compose default | none |
| Appwrite | yes | cloud only | scripts | own console | built-in import from Supabase, but converts to a different API |

Sharing one PostgreSQL across projects is not a differentiator. The five things to own, in this order:

1. **A checked import** from Supabase Cloud or a self-hosted compose into one environment, keeping users, password hashes, `storage.objects` rows and object bytes consistent, verified before traffic moves. Nobody offers this while keeping the Supabase API.
2. **Online per-environment backup and restore** to an off-host target, verified byte for byte (roadmap milestone 2).
3. **Upgrades that can be undone**, one environment first (milestone 4).
4. **Credentials scoped per environment**, which the shared-login projects lack (done; keep it in every new feature).
5. **Upstream Studio per environment behind real login** (milestone 3).

## 4. `sbarbase import`: the migration path

Pinned targets it must match: `supabase/postgres:17.6.1.166`, GoTrue `v2.196.0`, PostgREST `v14.15`, storage-api `v1.73.1` (`lab/distro-image.lock.json`, `lab/storage-image.lock.json`). The steps reuse `recovery_bundle.py`, the digest method in `recovery-export.py`, `recovery-restore-db.py`, `storage-files.cjs`, `source_fence.py` and the provisioning receipts. The source is only ever read.

**Inputs.** `--from cloud|compose|pgurl`, a source database URL (Cloud: direct or session pooler), a Storage source (Cloud S3-protocol keys or service key; compose volume path), optionally the legacy JWT secret, and a target `--organization/--project/--environment` that must be new and empty. `--dry-run` stops after phase 0.

| Phase | What happens | Refuses when |
|---|---|---|
| 0. Inspect (read only, writes a report) | Source major version; extensions; non-reserved roles; `max(version)` of `auth.schema_migrations` and `storage.migrations`; `supabase_migrations` history; publications; `vault.secrets` count; `cron.job`; `storage.objects` count and bytes; Edge Function list. | Source Auth or Storage migrations newer than the pinned GoTrue or storage-api; an extension absent from the target image; source major above 17. |
| 1. Dump | `supabase db dump --role-only`, schema, and `--data-only --use-copy` (excluding `storage.buckets_vectors`, `storage.vector_indexes`), plus `supabase_migrations`; sealed with `recovery_bundle.py`. | A dump step exits non-zero. |
| 2. Provision | A fresh environment through the normal worker path (receipts, HBA, `{e}_auth`, `{e}_rest`, `{e}_storage`, Storage tenant). The pinned Auth and Storage run their own migrations, then are fenced. | Any admission gate refuses. |
| 3. Rewrite and restore | Drop reserved roles; namespace custom roles as `{e}__name` because roles are cluster-wide in the shared cluster, and rewrite grants and policies; map `supabase_auth_admin` to `{e}_auth` and `supabase_storage_admin` to `{e}_storage`; strip `OWNER TO supabase_admin`. Restore in one transaction with `session_replication_role=replica`. Copy `auth` and `storage` rows only into columns the target has. | Dropping a source-only table would lose rows; any statement fails. |
| 4. Objects | Cloud: list and fetch through the S3 protocol or Storage API; compose: read the volume. Write with `storage-files.cjs restore` into the tenant path `bucket/name/version`, keeping content type, cache control and etag. Keep the dumped `storage.objects` rows so ids, owners and RLS survive. | The tenant path is not empty. |
| 5. Keys | With the legacy HS256 secret: reuse it, so existing access tokens and signed URLs stay valid. Without it (or on asymmetric signing keys, whose private key cannot be exported): new secret, users sign in again. `sb_publishable_`/`sb_secret_` keys cannot move; issue gateway keys and print them. | none; the report states which case applied. |
| 6. Verify before routing | Per-table row counts and sha256 digests, source against target; `auth.users` and `auth.identities` counts; every `storage.objects` row has a file of matching size and etag; extensions and migration versions; supabase-js sign-in with a named imported user, an RLS-scoped REST read and a Storage download through the gateway (as `lab/first-project-check.ts` does). | Any mismatch. |
| 7. Route or roll back | Route only after phase 6 passes. Before routing, rollback drops the new database, tenant directory and roles under the receipt discipline. | not applicable |

Reported as manual follow-ups, never silently dropped: Vault/pgsodium root key (import it or refuse when `vault.secrets` is not empty), `pg_cron` jobs (disabled, since nothing runs them), Realtime publications (kept, inactive), Edge Functions (source listed only), OAuth providers, SMTP and redirect URLs.

Unconfirmed until a live probe: that refresh tokens still exchange after a JWT secret change. Do not document it as working before the probe passes.

## 5. Exact order of work

Each step names the check that closes it. Steps inside a block can run in parallel; blocks are sequential.

**Block A: green baseline (this week, no server needed)**
1. Merge this change; confirm CI `checks` is green on `main`.
2. Step 1.1 and 1.2 (hermetic tests, no-Docker CI job). Check: CI green, container run `OK`.
3. Decide H1. Then implement H1 and H2 with their tests. Check: `bun test` passes with the new tests.
4. Update [status](../../reference/status.md) test counts from the CI run.

**Block B: the real server (roadmap milestone 1)**
5. Run the milestone 1 steps as written. Add H6 before the seven-day soak so the soak also shows the API refusing the fifth environment.

**Block C: hierarchy completeness (can overlap block B)**
6. H5 (schema versioning), then H4, then H3 routes and console screens, then H8 and H9. Check per row of section 2.

**Block D: import, built on the backup work (roadmap milestone 2)**
7. Build milestone 2's online per-environment backup first; import phases 1, 3, 4 and 6 are the same machinery pointed at a foreign source.
8. `sbarbase import --dry-run` (phase 0 only) against a disposable Supabase Cloud free project and a vanilla compose stack. Commit the reports as evidence.
9. Full import from a vanilla compose stack in the VM rehearsal (`lab/vm-rehearsal.sh`), seeded with users, RLS tables and objects. Check: phase 6 passes and a user signs in with their old password.
10. Full import from a disposable Supabase Cloud project, with and without the legacy JWT secret. Check: the same, and the report states which key case applied.
11. Write `docs/guides/migrate-from-supabase.md` from the rehearsal, the way the quickstart was written from the VM run.

**Block E: then milestones 3 to 5** as the roadmap orders them, with H7 landing with milestone 2's manifest.

Acceptance for this plan as a whole: a stranger with a Supabase Cloud project follows the migration guide on a fresh server and reaches a supabase-js sign-in with an existing user, an RLS read and a file download, with the verification report committed.
