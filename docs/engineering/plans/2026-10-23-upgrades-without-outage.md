# Upgrades without an outage: study

Recorded 2026-10-23. Status: study, written before the work it proposes. Parts of phases 1 to 4 have since been built and released in v0.7.0 and v0.7.1, as defaults (kept containers, the graceful stop) or as features that are off in those releases and on by default from 0.8.0 (the handover, the rolling replacement of `rest`, the database window), each with an opt out; what the code does now is in the [upgrades guide](../../guides/upgrades.md#what-an-upgrade-does-now-by-release-type) and the gaps in [upgrades known limits](../upgrades-known-limits.md). The built parts are proven by unit tests and by the first real channel update (v0.7.1, which passed its health checks), and have not been measured under load or by fault injection on a real server. Every number below is a target or a measurement of the old behavior, not a result of the new. It asks whether the way a release installs today is the right one for an installation that serves client applications, and what would make it faster without giving up any of the safety the [update channel](../UPDATE-CHANNEL.md) already has. Every claim about the current code cites a file and line as of commit `b90cd93`; everything else is marked as measured, inferred or unknown.

## The answer in short

The upgrade path is careful about data: signed releases, a class computed from the diff, a backup and a control snapshot before the move, a start guard, a traffic hold and an automatic way back. Keep all of that.

What it does not do is keep applications running. **Every release, however small, stops every tenant's traffic**: the supervisor stops the Bun process that is the gateway, then `docker stop`s every owned container, the shared PostgreSQL included, exits 42, and the new version starts everything again from cold, environments one after another. The rehearsal VM measured about 20 to 23 seconds of outage with one empty environment and no Realtime or Edge Functions. With real environments the outage grows with their number.

Most of that outage is avoidable. Of the 19 release transitions from v0.1.0 to v0.5.13, **17 changed no image pin** (every release after v0.3.0). For those, no container had to be replaced for a new image; at most a container whose computed configuration changed (its environment variables, which `launch()` also compares) needs it, and the lock files alone do not show which. Those 17 also kept `CATALOG_SCHEMA_VERSION` at 4, though some added tables or columns to other control stores (below).

The proposal, in order of value: (1) stop touching containers a release does not change; (2) keep the HTTP listener alive across the process restart, so applications see a short delay instead of errors; (3) replace one environment's Auth and REST at a time, side by side, when their pins do change; (4) cheaper restore points and a held, short window for the rare changes that must restart PostgreSQL. Phases 1 and 2 together target, for a release that changes no pin and not the TLS proxy, about a second of added latency and no failed requests, instead of a 20 second outage for everyone. That target is a design goal to be measured, not a result. Three of the 17 releases changed the TLS proxy itself; those restart the edge for everyone and need their own handling (phase 2).

## What happens today

### Timeline, with tenant traffic per phase

| Phase | What runs | Tenant traffic | Source |
|---|---|---|---|
| A. Drain (up to 600 s) | Worker stops claiming jobs; the scheduler, Studio starts, sign-in applies and toggles wait | **Up** (scheduled jobs and outgoing HTTP paused) | `lab/dev.py:37`, `:409-451`, `:701-718` |
| B. Upgrade child | Plan, image pull, logical backup of every environment, `storage_metadata` and management (serial `pg_dump -Fc` plus a Storage tar), control snapshot, guard copy, checkout move, `bun install` | **Up** | `lab/upgrade.py:479-491`, `:570-572`, `:602-606`; `lab/backup.py:1355-1378`, `:443-455` |
| C. Supervisor stop | Children terminated, then the Bun server (every HTTP, WebSocket and direct database socket closed at once by `server.stop(true)`), then the worker; then `installation_runtime.py stop`, which is `docker stop` on every container labelled as owned, PostgreSQL included; exit 42 | **Down**: the TLS proxy answers 502, WebSockets close with 1011 | `lab/dev.py:791-810`, `:1080-1095`; `lab/durable_runtime.py:2087-2090`; `lab/upstream-server.ts:69-74`; `deploy/console-tls-proxy.ts:230-231`, `:295-297` |
| D. Service restart | `RestartSec=10`, then host preflight, guard, leftover runtime, install check | **Down** | `deploy/sbarbase.service:23-47` |
| E. Cold start | Control snapshot and hold marker, settle stage, console rebuild when a UI source is newer than the build, `installation_runtime.py up` (300 s cap): database, management Auth, Storage, then per environment Auth, REST, Storage, Realtime and Functions, serially; Studio reset | **Down** (nothing listens on the console port) | `lab/dev.py:1020-1034`, `:754-760`; `lab/durable_runtime.py:743-745`, `:1097-1108`; `lab/console_build_check.py:75-82` |
| F. Hold | Bun server up; health round every 2 s, deadline 120 s | **503** with `Retry-After: 5`; Realtime and direct database refused | `src/gateway/hold.ts:20-58`; `lab/upgrade_health.py:23-27` |
| G. Confirmed | Hold released, worker starts | **Up** | `lab/upgrade_health.py` |

Phases A and B are long but harmless: traffic flows. The outage is C to F.

### Measured, inferred, unknown

**Measured** (rehearsal VM, one environment, no Realtime or Functions, polled from outside every 0.1 s, `docs/evidence/vm-channel-checks.json`; the v0.9.x tags were throwaway releases signed inside the VM for the rehearsal, not published versions):

| Release | Console gone at | 503 seen at | Confirmed at | Outage |
|---|---|---|---|---|
| good (v0.9.1) | 16.1 s | 35.7 s | 36.1 s | about 20 s |
| automatic (v0.9.3) | 3.6 s | 26.5 s | 26.9 s | about 23 s |
| attended, Auth image changed (v0.9.4) | 44.6 s | 64.8 s | 65.3 s | about 21 s |

The hold itself, the 503 part, lasted about 0.4 to 0.5 s each time. Almost the whole outage is stopping and cold starting. On the real server, 0.2.0 passed its health checks "within 20 seconds" of starting (`docs/engineering/checkpoints.md:476`); later real upgrades recorded no timing. The backup in phase B served 13326 requests without a failure while it ran, on two small environments (`docs/evidence/vm-backup-traffic.json`).

**Release history** (computed for this study from the tags):

| Transition | Image pins changed | Catalog schema changed | Rebuild files changed |
|---|---|---|---|
| v0.1.0 to v0.2.0 | Functions, Realtime, Studio | yes | yes |
| v0.2.0 to v0.3.0 | Auth/REST, Storage, Realtime, Functions, Studio | yes | yes |
| v0.3.0 to v0.5.13 (17 releases) | none | `CATALOG_SCHEMA_VERSION` 4 throughout; other control stores gained tables and columns (below) | at least 6 (v0.5.3, v0.5.5, v0.5.7, v0.5.8, v0.5.10, v0.5.13) touched `compose.yaml`, `Dockerfile` or the TLS proxy; the TLS proxy in v0.5.3, v0.5.7 and v0.5.8. Files the `Dockerfile` copies were not diffed, so 6 is a lower bound |

Between v0.3.0 and v0.5.13 the key store gained `name` and `token` columns on `api_keys`, and new stores or tables appeared for access tokens (`access_tokens`), management token epochs and Studio activity, all additive and created with `IF NOT EXISTS` or `ADD COLUMN`. None of that is recorded in a version number the way the catalog's `user_version` is, so "the catalog schema did not change" does not mean "the control state did not change".

**Inferred, not measured:**

- The outage grows roughly linearly with the number of environments, since phase E starts them one after another and waits for each to answer.
- With eight environments, or a Realtime pin that changes (boot allowance 180 s, `lab/durable_runtime.py:323`, plus a restart after migrating), `runtime up` could exceed its 300 s cap (`lab/dev.py:1034`) and count as a failed start, which would take the way back.
- The backup in phase B grows with data, since `pg_dump` and `tar` are linear in size. It does not lengthen the outage, but it lengthens the upgrade and holds the drain.
- A release that changes UI sources rebuilds the console during the outage (the code comment estimates about a minute, `lab/dev.py:1026`), because `upgrade.py` runs `bun install` before the restart but not `build:ui`.

**Unknown:**

- Whether PostgreSQL always finishes its shutdown within Docker's default 10 seconds. The pinned image stops with `SIGINT` (fast shutdown: running transactions abort, then a shutdown checkpoint), and `docker stop` is called without `-t` (`lab/durable_runtime.py:2090`). A checkpoint longer than 10 s is cut by `SIGKILL`, and the next start then runs crash recovery. That is safe for data but slower, and it happens inside the outage.
- Any measurement with more than one environment, with Realtime or Functions on, or with real data.

### Discrepancies found

1. `docs/guides/upgrades.md:50` says that on restart "only the Auth, REST, Storage, Realtime and Edge Functions containers whose pinned image changed are replaced". Edge Functions containers are removed and created again on every start (`lab/durable_runtime.py:1702-1703`), and every container, changed or not, is stopped and started.
2. The same guide says "the console disconnects for a few minutes" (`:26`). The measured outage was about 20 seconds with one environment.
3. The September roadmap's Milestone 4 is marked done, and its item 3 reads "Upgrade one environment's Auth and REST first, run the first-project check, then the rest" (`docs/engineering/plans/2026-09-23-roadmap.md:102`). The code replaces all environments together; item 3 was not built.
4. `pauseManagedEnvironment` and `waitForDrain` (`src/gateway/managed.ts`, `src/gateway/concurrency.ts`) have no caller outside tests, and their state is in memory only (`docs/engineering/GATEWAY-DRAIN.md:9`). Nothing drains in-flight requests on shutdown: `server.stop(true)` closes them.

## What others do

Sources are listed at the end. The pattern is consistent:

- **Single-node self-hosted products accept a window.** Supabase's own self-hosting update is `pull`, `down`, `up`. Sentry self-hosted has no supported zero-downtime upgrade. GitLab's zero-downtime upgrade requires a multi-node install; a single node has downtime. Discourse swaps application code with little downtime but a base image rebuild has downtime.
- **Zero downtime appears where every component has two instances behind a proxy that checks health before switching.** On one host that means two containers or two processes per component, not two machines: Kamal 2 with kamal-proxy, Dokku, docker-rollout and Coolify all start the new instance beside the old one, wait for a health answer, switch the proxy, then retire the old one after a drain.
- **The listener can outlive the process.** nginx (`USR2`), HAProxy (listener handoff), Envoy (hot restart) and Cloudflare's tableflip all keep the listening socket open while the process behind it changes, so no connection is refused. `Bun.serve({ reusePort: true })` exists on Linux and lets an old and a new Bun process share one port. Taking an inherited socket from systemd does not work in released Bun yet (oven-sh/bun #22559, PRs #36491 and #45186 still open on 2026-10-22), so no design here may depend on it.
- **Restart only what must restart.** PostgREST reloads its schema cache and most configuration without a restart (`SIGUSR1`, `SIGUSR2` or `NOTIFY pgrst`). PostgreSQL reloads `pg_hba.conf` and every non-`postmaster` setting with `pg_reload_conf()`. A PostgreSQL minor version needs a restart, commonly said to take seconds (not verified for this study); a `CHECKPOINT` just before it shortens the shutdown.
- **Hold rather than refuse.** PgBouncer's `PAUSE` and `RESUME` exist for exactly the database restart case: clients wait instead of failing.
- **Rollback needs schema compatibility.** Expand and contract (add the new shape, move readers, remove the old shape in a later release), with `lock_timeout` and retries on DDL. The rule: version N must run on the schema of N+1, and N+1 on the schema of N, or there is no safe overlap and no safe way back.
- **Restore points that do not scale with data.** A filesystem snapshot (btrfs, ZFS, LVM) of the PostgreSQL volume after a `CHECKPOINT` is constant time, and WAL archiving with point-in-time recovery makes "before the upgrade" a timestamp. Neither can be assumed on every host.
- **Canary first.** Google's SRE workbook: deploy to a small part, compare its error rate and latency with the rest, one canary at a time, roll back the part that fails.

## The proposal

Each phase stands on its own and leaves the installation safer than before. The order follows value: phase 1 alone covers 17 of the last 19 releases.

### Targets per class

"No outage" is not honest for every kind of change on one host. These are the targets, to be proven by measurement:

| Change | Today | Target |
|---|---|---|
| Code only (Bun, Python, console), no pin, no control store schema change, TLS proxy unchanged | about 20 s outage for every tenant, longer with more environments | no failed request; added latency of about a second while the new process takes over |
| Code with a control store schema change | the same | a short held window (the hold's 503, measured at about half a second); the TLS proxy retries refused connections only, never a 503, so a client behind it sees a `503` with `Retry-After` for the window; management changes paused for the hold, as today |
| TLS proxy changed | the same, plus its own restart by hand | a short window at the edge for everyone, announced; its own design (see phase 2) |
| PostgREST pin | the same | per environment, side by side, no failed request |
| Auth, Storage or Realtime pin (`attended`) | the same, plus migrated data after a way back | per environment where upstream migrations allow it; otherwise a short held window per environment, never all at once |
| A rebuild (`compose.yaml`, `Dockerfile`, unit, TLS proxy) | manual rebuild and restart | the same short window as code only where the change allows; the TLS proxy itself is a separate, held restart |
| PostgreSQL image | attended move with paused writes | stays attended; a short held window measured in seconds of restart, announced ahead |

### Phase 1: leave unchanged containers running

Today the stop in phase C and the start in phase E run whether or not anything changed. Change it so the upgrade restart stops only the supervisor, the worker and the Bun process, and the next start adopts the running containers.

- **Decide what to stop from the plan, not by default.** `.lab/upstream/upgrade-intent.json` already names which pins the next start may replace. For an upgrade exit, `installation_runtime.py stop` gets a mode that stops only the components in the intent (none for a code-only release). An ordinary stop (`systemctl stop`, a reboot) keeps stopping everything as today.
- **Adopt, still fail-closed.** `launch()` already compares the image and environment of a retained container and refuses drift it was not told to expect (`lab/durable_runtime.py:626-678`). Adoption is that check against a running container instead of a stopped one: same image, same environment, same volumes and network. Anything that does not match is stopped and handled exactly as today.
- **Two existing guarantees block adoption and must be kept, not dropped.**
  1. The preflight refuses to start while owned containers run (`lab/install_server.py:271-272`, the unit's `ExecStartPre` at `deploy/sbarbase.service:38`); that is why `leftover_runtime.py` stops them first. The check, and the headroom arithmetic it makes for what the next start will run, must accept the containers a planned upgrade exit recorded as kept, and still refuse anything else.
  2. `resume()` stops a running REST container on purpose (`lab/durable_runtime.py:763-768`): tenant code can act as the REST login and change its own role settings, so the platform's statement deadlines are written again and that login's sessions ended before REST reconnects. Adoption must keep that guarantee another way, for example by rewriting the deadlines and ending the sessions while REST keeps running (PostgREST reconnects its pool by itself), or by replacing REST per environment side by side as in phase 3. Until then REST is the one container a code-only upgrade still restarts, one environment at a time rather than all at once.
- **Do not weaken `leftover_runtime.py`.** It stops containers a *killed* supervisor left, because a killed supervisor may have left an effect half done. Keep that for every exit except the planned upgrade exit, and mark the planned case explicitly (a record the supervisor writes after it released its locks, naming the commit it left and the containers it kept). The receipt and journal reconciliation stays in front of adoption: pending receipts or HBA journals still refuse, as they do now.
- **Functions** stop being recreated on every start when nothing about them changed.
- **Build the console before the restart**, in the upgrade child, so a UI change never costs a build inside the outage.
- **PostgreSQL**: when it must stop, issue a `CHECKPOINT` first and give `docker stop` an explicit timeout that fits a fast shutdown, so it is never cut by `SIGKILL`.
- **Health round unchanged.** It still checks every environment through the gateway before the hold lifts.

Expected result: the outage shrinks to the restart of the processes (service restart delay, preflight, Bun start, one health round), from about 20 s to a few seconds, and stops growing with the number of environments. The 10 s `RestartSec` is the largest fixed part left. systemd cannot set it per exit code, so whether to lower it for every restart, or to have the supervisor hand over without a service restart, is an open question.

This phase does not depend on anything else, works the same under systemd and Docker, and needs no new tool.

### Phase 2: keep the listener up across the restart

After phase 1 the remaining gap is the seconds between the old Bun process closing its port and the new one answering. Two ways to close it; both are needed because installations run with and without the TLS proxy, under systemd and under Docker.

1. **Bun overlap on the same port.** The new version's Bun server starts with `reusePort: true` beside the old one, opens the catalog, loads routes and answers `/health`; only then is the old one told to stop accepting, finish in-flight requests with a bounded wait, and exit. This needs:
   - the supervisor, not the service manager, to start the new Bun process before the old supervisor exits, or a short-lived handover where the new supervisor starts Bun while the old Bun still serves. The one-supervisor-per-daemon rule is kept: only the Bun process overlaps, never the supervisor or the worker;
   - overlap allowed only when no control store schema changed (the catalog's `user_version` and the key, token and other stores, which need a version of their own first); otherwise the hold applies as today;
   - the new process passes its health round on a private port before it binds the shared one. With `SO_REUSEPORT` the kernel spreads new connections across both listeners, so a new process that joined the port while still held would answer 503 for its share;
   - closing a reuseport listener resets the connections still queued on it. The old process stops accepting first and drains its queue; the TLS proxy retry (below) covers what remains, only on installations that run the proxy;
   - a graceful stop in `lab/upstream-server.ts`: stop accepting, wait for in-flight requests up to a deadline, then close. Realtime sockets and direct database connections are closed after the deadline; clients reconnect, which supabase-js and PostgreSQL drivers do;
   - the SQLite stores opened by two processes for a moment: WAL mode and a busy timeout, with management writes refused by the old process once it is told to stop.
2. **The TLS proxy retries instead of answering 502.** `deploy/console-tls-proxy.ts` is a separate unit that is not restarted by an upgrade. When the upstream refuses the connection, it waits and retries for a bounded window (for example up to 15 s for idempotent requests, and for any request that never reached the upstream), so a client sees delay, not an error. A Docker install without the TLS proxy gets the same behavior only from option 1, so option 1 is the main path and the proxy retry is the safety net.

A release that changes the TLS proxy itself (three of the last 17) restarts the edge, and then neither option helps. The proxy is small and changes rarely; it can take the same reuseport overlap as option 1, or such releases stay a short announced window.

Do not build on systemd socket activation or `Bun.serve({ fd })` until Bun ships it.

### Phase 3: one environment at a time, side by side

For a release that changes the Auth or PostgREST pin (and later Storage and Realtime), the restart does not replace these containers. The new supervisor, after confirmation of its own processes, rolls the environments:

1. Start `auth` and `rest` for one environment on the new image under a new name beside the old ones, inside the existing resource admission (memory is briefly doubled for that environment).
2. Wait for their health, then run the first-project check against them directly.
3. Switch that environment's upstream in the gateway atomically; the old containers stay running.
4. Watch error rate and latency for that environment for a short window against the others; on failure switch back to the old containers, which are still there.
5. Retire the old containers after a drain; go to the next environment. The first environment is a canary the operator can choose (an internal or staging environment); the rest go in small batches.

This is roadmap Milestone 4 item 3, which was never built. Conditions that must hold before it is used for a component:

- **PostgREST**: stateless; safe.
- **Auth (GoTrue)**: it migrates the `auth` schema when it starts, so the old container keeps serving on a schema the new one migrated. That is safe only when the upstream migrations in that range are additive. The pin review (`docs/upstream/`) must state it per release; when it cannot, the environment gets a short held switch (hold that environment's requests, stop old, start new, release) instead of side by side. Either way it is one environment at a time, never all.
- **Storage and Realtime** are shared by every environment and migrate their shared tenant store; they need their own design (two instances behind the gateway, or a short held switch). Until then they keep today's behavior, but only for releases that change their pin.

### Phase 4: restore points and the database

- **Keep the backup before every release.** A code-only release still touches environment databases: its start writes the REST deadlines and the Data API configuration again and puts scheduled jobs back (`lab/durable_runtime.py:768-774`). The backup runs while traffic flows, so it costs time, not availability. Keep it for every class.
- **Make it cheaper where the host allows.** When the PostgreSQL volume sits on btrfs, ZFS or LVM thin, a snapshot after a `CHECKPOINT` is a constant-time restore point; use it beside or instead of `pg_dump` there, and fall back to `pg_dump` everywhere else. It must stay optional: most hosts do not have it.
- **WAL archiving and point-in-time recovery** are the larger answer and already listed as absent in the product plan; they belong to backups, not to this plan.
- **PostgreSQL restarts** (a minor image change, a setting with `postmaster` context) stay attended. Make them short and quiet: reload instead of restart where `pg_settings.context` allows; when a restart is needed, `CHECKPOINT`, hold requests at the gateway (and connections at the direct database listener) for a bounded window instead of refusing them, restart, release. A connection pooler, when one exists, does the same with `PAUSE` and `RESUME`.

### Phase 5: compatibility rules for every release

The overlap in phases 2 and 3 and every way back depend on the old and new versions working on the same data. Write that into the release process:

- Control catalog changes are expand and contract: a release adds columns and tables the previous version ignores; removal comes in a later release. `CATALOG_SCHEMA_VERSION` stays a hard gate for overlap and rollback, as it is now.
- SQL Sbarbase applies to environment databases sets `lock_timeout` and retries.
- `lab/release.py prepare` records whether the release is overlap-safe (catalog unchanged or additive), and the class computation checks it from the diff the same way it checks pins, so a release cannot claim it.

## How each phase is accepted

The method already exists: `lab/upgrade-check.py` polls the installation from outside every 0.1 s. Each phase is accepted only by that probe, on the rehearsal VM and then on the real server, with:

- at least three environments with data, Realtime and Edge Functions on, and steady supabase-js traffic (REST reads and writes, Auth sign-in, a Storage upload, a Realtime subscription) through the TLS proxy for the whole upgrade;
- recorded: failed requests, the longest gap, p99 latency during the upgrade, Realtime reconnects, and time from request to confirmation;
- the broken-release cases the channel already has (a version that never becomes healthy, one that dies after migrating the catalog), which must still move back by themselves, with the same data checks;
- a kill of the supervisor during each new step.

Phase 1 is accepted when a code-only release shows no container restart and an outage under 5 s with three environments. Phase 2 when the same release shows zero failed requests through the proxy. Phase 3 when a PostgREST pin change shows zero failed requests and a deliberately broken image is switched back on the canary environment while the others never moved.

## Not covered

- High availability across servers. A host failure still stops every application; that is P3 in the [product plan](2026-10-03-product-and-portability-plan.md).
- The PostgreSQL major version path, which stays the attended move in the [upgrades guide](../../guides/upgrades.md).
- Correcting the guide's two statements listed under discrepancies; that change needs the English and Arabic pages together.

## Sources

- nginx control and binary upgrade: https://nginx.org/en/docs/control.html
- HAProxy hitless reloads: https://www.haproxy.com/blog/hitless-reloads-with-haproxy-howto
- Envoy hot restart: https://www.envoyproxy.io/docs/envoy/latest/intro/arch_overview/operations/hot_restart
- Cloudflare, graceful upgrades in Go: https://blog.cloudflare.com/graceful-upgrades-in-go/
- systemd file descriptor store: https://systemd.io/FILE_DESCRIPTOR_STORE/
- Bun `reusePort`: https://bun.com/reference/bun/Serve/HostnamePortServeOptions/reusePort ; socket activation still open: https://github.com/oven-sh/bun/pull/36491 , https://github.com/oven-sh/bun/pull/45186
- kamal-proxy: https://github.com/basecamp/kamal-proxy ; Dokku: https://dokku.com/docs/deployment/zero-downtime-deploys/ ; docker-rollout: https://github.com/wowu/docker-rollout ; Coolify: https://coolify.io/docs/applications/deployments/rolling-updates
- Docker live restore: https://docs.docker.com/engine/daemon/live-restore/
- Supabase self-hosting with Docker: https://supabase.com/docs/guides/self-hosting/docker ; platform upgrades: https://supabase.com/docs/guides/platform/upgrading
- GoTrue migrates on start: https://github.com/supabase/auth/blob/master/cmd/root_cmd.go
- PostgREST schema cache and configuration reload: https://docs.postgrest.org/en/stable/references/schema_cache.html , https://docs.postgrest.org/en/stable/references/configuration.html
- PostgreSQL settings context, `pg_hba.conf` reload, versioning, `pg_upgrade`, file system backups: https://www.postgresql.org/docs/current/view-pg-settings.html , https://www.postgresql.org/docs/current/auth-pg-hba-conf.html , https://www.postgresql.org/support/versioning/ , https://www.postgresql.org/docs/current/pgupgrade.html , https://www.postgresql.org/docs/current/backup-file.html
- PgBouncer `PAUSE` and `RESUME`: https://www.pgbouncer.org/usage.html
- AWS RDS blue/green: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/blue-green-deployments-overview.html
- Parallel change: https://martinfowler.com/bliki/ParallelChange.html ; GitLab, avoiding downtime in migrations: https://docs.gitlab.com/development/database/avoiding_downtime_in_migrations/ ; lock timeouts: https://postgres.ai/blog/20210923-zero-downtime-postgres-schema-migrations-lock-timeout-and-retries
- GitLab zero-downtime upgrades: https://docs.gitlab.com/update/zero_downtime/ ; Sentry: https://forum.sentry.io/t/how-to-upgrade-sentry-without-downtime/14838
- pgBackRest: https://pgbackrest.org/user-guide.html
- Google SRE workbook, canarying releases: https://sre.google/workbook/canarying-releases/
