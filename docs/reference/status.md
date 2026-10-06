[العربية](status.ar.md)

# Status

Current cutoff, 2026-10-06: the reviewed repairs close SQLite connections in `recovery_bundle` after their original transactions and close `HTTPError` responses in `notify`. They also capture and assert complete expected stderr in 27 fixture methods and register tempfile close/unlink ownership before a fallible write. A newer creator fixture associates its tempfile warning with the HTTPError response lifecycle; it does not establish the allocation causes in earlier warning packets.

Current result: **1504 tests passed with zero failures, errors, warnings or skips**, independently reviewed after native exit 0. All 881 public source bindings, both 294-file captures and both 881-entry mode checks matched; all eight helpers were removed with no unresolved resources. This includes 55 regressions for the two newly published private SQL completion components and the 52 focused preparation cases. See the [current public regression summary](../evidence/private-pump-regression-2026-10-06.json). The [earlier 1449-case result](../evidence/public-regression-2026-10-06.json) retains its original snapshot. [CI at `9666947`](https://github.com/M7MMAD-OMAR/sbarbase/actions/runs/37404496379) passed checks, Python 3.12 compatibility and Docker installation, including the four additions. Empty-host acceptance was skipped on this ordinary push; its earlier result remains bound to `066f5ec`. Component and preparation tests do not establish installed private client compatibility, PostgreSQL startup, Cron/Vault continuity or full restoration.

The fixed installed PostgreSQL client returned version 17.6 in a fresh isolated local Docker helper, with independent review, quiet command completion and verified helper removal. See the [public client observation](../evidence/default-factory-client-2026-10-06.json). This verifies `psql --version` only; private SQL, database startup, restart continuity and restoration still require actual acceptance.

[CI 37388098529](https://github.com/M7MMAD-OMAR/sbarbase/actions/runs/37388098529) passed all four jobs for code commit `066f5ec`, including Python 3.12, Docker integration and fresh-host systemd acceptance. [Website 37388119460](https://github.com/M7MMAD-OMAR/sbarbase/actions/runs/37388119460) passed build and tests only. See the [CI acceptance summary](../evidence/ci-acceptance-2026-10-06.json); its runtime evidence remains bound to that exact code commit.

The normal `legacy-shared` platform has historical Docker integration evidence for installation, the first project, Auth, REST, Storage, Studio, Realtime, Functions, backups and upgrades. Fresh CI and manually dispatched empty-host acceptance passed for code commit `066f5ec`. The user's computer is the selected isolated Linux Docker test server; CI provides an additional fresh-host route. Public DNS and a public TLS certificate are future operator configuration.

The experimental `native-dedicated` path remains incomplete: the PostgreSQL 17.11 candidate has not received accepted startup, effective private-safe configuration, real private request operation, cold/warm Cron and Vault continuity, or physical recovery evidence. Retained isolated candidate volumes must not be adopted, read or deleted. Package 0.2.0 remains a development snapshot; production acceptance and a signed release are not established.

The [project goal](../../PROJECT_GOAL.md), [product plan](../engineering/plans/2026-10-03-product-and-portability-plan.md), [execution method](../engineering/plans/2026-10-03-gauntlet-execution-method.md) and [native placement contract](../engineering/plans/2026-10-03-native-placement-identity.md) define the current scope. The [ledger](../engineering/gauntlet-ledger.json) records source-bound evidence and preserved losses. Local ignored packets are not distributed evidence or a supported operator interface.

| Current evidence area | Accepted scope and remaining gate |
|---|---|
| [Original-image configured effects](../engineering/reviews/2026-10-04-native-cron-effects.md) | Bounded configured Cron writes and native HTTP effects on the pinned original image. This does not accept the PostgreSQL 17.11 candidate, full service recovery or production. |
| [Streaming backup transport](../engineering/reviews/2026-10-04-streaming-backup-transport.md) | Historical exact-source offline transport acceptance. Native physical restore and remote S3 remain unaccepted. |
| [Patched PostgreSQL 17.11](../engineering/reviews/2026-10-04-patched-postgres-observations.md) | Public image/configuration/script observations and preservation only. Candidate startup, effective settings, real private request pump, candidate Cron effects, warm Vault continuity and physical restore remain UNRUN or unaccepted. |
| [Root-key publication](../engineering/reviews/2026-10-04-native-root-key-source.md) and [native filesystem guards](../engineering/reviews/2026-10-04-native-root-key-filesystem.md) | Scoped publication, preservation and guard observations. The primitive is not wired into candidate startup; retained key material is not a recovery proof. |
| [Private diagnostics](../engineering/reviews/2026-10-04-private-diagnostics-fifo.md), [bridge](../engineering/reviews/2026-10-04-private-diagnostics-bridge.md) and [live prefixes/credentials](../engineering/reviews/2026-10-04-live-diagnostics-and-credentials.md) | Separately scoped fixture acceptances. The later 25 public FIFO component regressions passed locally; they do not prove real private role ownership, complete launcher/client/pump operation or general secret safety. |
| [Native startup preparation](../../deploy/verify/native-startup-preparation.md) | Eight public source files define prepare-only materials and a daemon guardian, with 52 focused regressions. Actual PostgreSQL startup remains a separate gate. |


## Historical test suites, 2026-09-24

Run from the repository root in a clean container with Python 3.14 and `cryptography`, as root. These suites do not start containers; they also pass with no Docker daemon reachable, which CI now checks.

| Suite | Command | Result |
|---|---|---|
| Python unit tests | `DOCKER_HOST=unix:///var/run/docker.sock /usr/bin/python3 -m unittest discover -s lab -p 'test_*.py'` | 642 tests, OK; 4 skipped as root or where `/usr/bin/python3` is older than 3.14, each with its reason; none skipped on CI |
| Bun tests (root) | `bun test` | 101 pass, 0 fail, 620 assertions, 20 files (the 19 in `tests/` plus `website/tests/site.test.ts`) |
| Website | `cd website && bun run build && bun test` | build OK; 2 pass, 0 fail, 40 assertions |
| Console typecheck | `bun run typecheck:ui` | passes |

Earlier pages recorded other totals (for example 575 Python and 87 Bun tests at the 0.1.0 release gate, and 625 and 93 on the workstation on 2026-09-23). Those were correct for their date and scope; this table superseded those earlier totals within the September snapshot only.

On 2026-09-25 the Python suite had 765 tests: `OK` with no skips on the workstation, `OK (skipped=2)` there with no Docker daemon reachable, and in a throwaway `python:3.12` container `OK (skipped=6)` as root and `OK (skipped=4)` as an unprivileged user. Each skip names what the host lacks: root for a permission refusal, a `/usr/bin/python3` of 3.12 or a Docker daemon for the acceptance script, `systemd-analyze`, or a block device for `/`.

### Historical Python interpreter probes, 2026-09-25

The preflight now accepts `/usr/bin/python3` 3.12 or newer, which admits the interpreters Ubuntu 24.04 and Debian 13 ship. To check that the code runs on them, the same Python suite ran in throwaway containers as an ordinary user (uid 1000). The checkout was mounted, Bun was on the path and no Docker daemon was reachable. Ubuntu and Debian used their own `python3` and `python3-cryptography` packages; the `python:` images used the current `cryptography` wheel. `python -m compileall lab deploy` passes on all of them.

| Interpreter | Result |
|---|---|
| Ubuntu 24.04: `python3` 3.12.3, `python3-cryptography` 41.0.7 | 768 tests; 3 skipped; 6 fail, all bound to the host (below) |
| Debian 13: `python3` 3.13.5, `python3-cryptography` 43.0.0 | 768 tests; 3 skipped; 7 fail, bound to the host |
| `python:3.12` (3.12.14), `python:3.13` (3.13.15) | 768 tests; 3 skipped; the same 7 fail |
| `python:3.14` (3.14.7), run as a control | 768 tests; 3 skipped; the same 7 fail |
| The workstation's `/usr/bin/python3` 3.14.7 | 768 tests, OK |

The control fails the same 7 tests, so the failures come from the container, not the interpreter version. Five need a block device behind `/` (the IO limits refuse with `io_device_unavailable`), one needs `systemd-analyze`, and one needs a home directory for uid 1000, which the Ubuntu image has and the others do not. The 3 skips need a Docker daemon or a device source for `/`. Within that September interpreter probe, neither Ubuntu 24.04 nor Debian 13 had an end-to-end install rehearsal; the separate local-VM rehearsal used Fedora 44. Current source CI is described independently above.

## Historical live evidence

Live probes start real containers and write their results to [docs/evidence](../evidence/). Each file names its own scope; counts from different files overlap and are not additive. The count below is the one recorded in the file.

### Platform and management

| What | Evidence | Checks |
|---|---|---|
| Dedicated management Auth, memberships and the composed API | [upstream-management-checks.json](../evidence/upstream-management-checks.json) | 28 |
| First operator setup, interruption recovery, discovery | [bootstrap-checks.json](../evidence/bootstrap-checks.json) | 18 |
| Management-issued keys, SDK access, revocation | [connection-checks.json](../evidence/connection-checks.json) | 9 |
| Console build and static serving | [console-build.json](../evidence/console-build.json), [console-serve.json](../evidence/console-serve.json) | passed; 19 |

### Environments and isolation

| What | Evidence | Checks |
|---|---|---|
| Four environments: Auth, RLS, credential and token isolation | [four-environment-component-checks.json](../evidence/four-environment-component-checks.json) | 97 |
| Same, through the SDK and key gateway | [four-environment-sdk-checks.json](../evidence/four-environment-sdk-checks.json) | 44 |
| Original Supabase PostgreSQL, two environments, real Auth migrations | [upstream-environment-checks.json](../evidence/upstream-environment-checks.json) | 40 |
| Durable runtime: persistent worker, volumes, container recreation | [durable-upstream-checks.json](../evidence/durable-upstream-checks.json) | 25 |
| Shared Storage with database and object recovery rehearsal | [storage-recovery-checks.json](../evidence/storage-recovery-checks.json) | 122 |
| Fresh closed bootstrap: Auth, REST and shared Storage | [upstream-closed-bootstrap-checks.json](../evidence/upstream-closed-bootstrap-checks.json) | 122 |
| Connection limit saturation with a neighbour still served | [connection-limit-checks.json](../evidence/connection-limit-checks.json) | 7 |
| A busy environment borrows idle gateway slots, recently active neighbours keep their whole share, and only refusals raise a saturation notice (loopback HTTP, controlled upstream, not Supabase) | [fair-share-checks.json](../evidence/fair-share-checks.json) | 7 |
| Pressure response on a disposable container | [pressure-response-checks.json](../evidence/pressure-response-checks.json) | 9 |

### Provisioning and crash safety

| What | Evidence | Checks |
|---|---|---|
| Fresh worker lifecycle with receipts, leases and SDK checks | [fresh-worker-checks.json](../evidence/fresh-worker-checks.json) | 76 |
| Native worker SIGKILL after intent / after witness | [worker-hba-crash-after-intent.json](../evidence/worker-hba-crash-after-intent.json), [worker-hba-crash-after-witness.json](../evidence/worker-hba-crash-after-witness.json) | 90 each |
| Receipt settlement through the real supervisor | [worker-receipt-checks.json](../evidence/worker-receipt-checks.json) | 13 |
| Supervisor SIGKILL during preflight | [active-preflight-crash-checks.json](../evidence/active-preflight-crash-checks.json) | 25 |
| Generation migration, five SIGKILL crash points on the disposable fixture | [fresh-worker-generation-crash-all.json](../evidence/fresh-worker-generation-crash-all.json) | 196 |
| Complete-file HBA replacement | [upstream-atomic-hba-checks.json](../evidence/upstream-atomic-hba-checks.json) | 36 |
| Partial database interruption, component and upstream | [partial-database-crash-checks.json](../evidence/partial-database-crash-checks.json), [upstream-partial-database-crash-checks.json](../evidence/upstream-partial-database-crash-checks.json) | 64; 65 |
| Retained source adopted into HBA authority | [retained-source-adoption.json](../evidence/retained-source-adoption.json) | recorded |

### Recovery

| What | Evidence | Checks |
|---|---|---|
| Fenced encrypted export | [recovery-export-checks.json](../evidence/recovery-export-checks.json) | 34 |
| Restore into a separate engine | [independent-database-restore.json](../evidence/independent-database-restore.json) | 49 |
| Original Auth and REST on the restored copy | [independent-service-checks.json](../evidence/independent-service-checks.json) | 11 |
| Storage and end-user RLS on the restored copy | [independent-storage-checks.json](../evidence/independent-storage-checks.json), [independent-storage-rls-checks.json](../evidence/independent-storage-rls-checks.json) | 9; 14 |
| Interrupted pg_restore rolls back cleanly | [recovery-interruption-checks.json](../evidence/recovery-interruption-checks.json) | 38 |
| SDK through the gateway to the moved environment | [cutover-sdk-checks.json](../evidence/cutover-sdk-checks.json) | 11 |
| Unaffected neighbours restarted on the source | [cutover-neighbor-checks.json](../evidence/cutover-neighbor-checks.json) | 16 |

### Import from Supabase (inspection)

| What | Evidence | Checks |
|---|---|---|
| Read-only inspection of a source database: refusals, warnings and manual steps before any dump, on the pinned image as the `postgres` role | [import-inspect-checks.json](../evidence/import-inspect-checks.json) | 6 |

The full import (schema, users, rows, files, verification) is `lab/import_project.py`; its end-to-end run is in the Docker table below.

### Install with Docker, daily backups, Studio and upgrades (CI, clean runner)

On a clean GitHub runner with only Docker, CI runs the Docker install on every change ([install with Docker](../guides/docker.md)). Not a real server: no public network, certificate or host reboot.

| What | Evidence | Checks |
|---|---|---|
| Build and start, first operator, first project through supabase-js, container restart, clean stop | [docker-install-checks.json](../evidence/docker-install-checks.json) | 15 |
| Back up one environment while it serves, change rows, users and files, restore, compare with the backup, discard the set-aside state | [docker-backup-restore.json](../evidence/docker-backup-restore.json) | 15 |
| Supabase Studio for one environment: started on demand, entered with the console ticket, table list, SQL, users and buckets through it, refused without the session or with another environment's session, stopped ([Studio guide](../guides/studio.md)) | [docker-studio-checks.json](../evidence/docker-studio-checks.json) | 22 |
| Sign-in settings: site URL, redirects and a GitHub provider saved and applied by recreating Auth, a keyless OAuth start and email link, sign-up after the recreate, the provider removed ([sign-in](../guides/sign-in.md)) | [docker-sign-in-checks.json](../evidence/docker-sign-in-checks.json) | 15 |
| Realtime for one environment: turned on and started by the supervisor, broadcast, presence and a database change between two supabase-js clients through the gateway, the broadcast REST API, a wrong key refused, turned off ([Realtime](../guides/realtime.md)) | [docker-realtime-checks.json](../evidence/docker-realtime-checks.json) | 19 |
| Encrypted off-site copies: configured from stdin, a backup copied to S3-compatible storage (a throwaway MinIO) by itself, only ciphertext stored, a wrong passphrase refused, the copy fetched and restored ([backup and restore](../guides/backup-and-restore.md#copies-off-the-server)) | [docker-offsite-checks.json](../evidence/docker-offsite-checks.json) | 12 |
| Direct database access: a developer login turned on, a PostgreSQL client through the listener running a Supabase-style migration (policy, trigger on `auth.users`, Storage policy), the superuser and other databases refused, a password reset, turned off ([database access](../guides/database-access.md)) | [docker-database-checks.json](../evidence/docker-database-checks.json) | 16 |
| Edge Functions for one environment: a Supabase functions folder deployed with one command, called with supabase-js, a function using supabase-js from npm with the service role on its own Auth, REST and Storage, a keyless webhook, a secret, a redeploy, a removal, turned off ([Edge Functions](../guides/edge-functions.md)) | [docker-functions-checks.json](../evidence/docker-functions-checks.json) | 24 |
| Logs and metrics for one environment: requests through the gateway counted with errors, response times and service memory use, the request log and the Auth, REST and Storage logs read through the management API, no key or token in any answer ([logs and metrics](../guides/logs-and-metrics.md)) | [docker-observe-checks.json](../evidence/docker-observe-checks.json) | 20 |
| Uploads: a 20 MiB file through the gateway and back byte for byte, a file just under the 50 MiB limit accepted and one just over it refused, then the same after a new `SBARBASE_UPLOAD_LIMIT_MB` recreated Storage | [docker-upload-checks.json](../evidence/docker-upload-checks.json) | 20 |
| Import into a new environment from another environment standing in for a Supabase project: the user signs in with the old password, row level security, a private file with its Storage policy and a sign-up trigger carry over, a second import into the full environment is refused ([move from Supabase](../guides/move-from-supabase.md)) | [docker-import-checks.json](../evidence/docker-import-checks.json) | 12 |
| Signing key rotation with Realtime on: the old session, refresh token and `service_role` token refused by Auth, REST and Storage, a new sign-in with the same publishable key accepted everywhere, Realtime still answering ([signing key](../guides/signing-keys.md)) | [docker-signing-checks.json](../evidence/docker-signing-checks.json) | 18 |
| Upgrade to a newer PostgREST with `lab/upgrade.py`; then broken versions the supervisor moves back from by itself (one whose PostgREST never answers, one that migrates the control catalog and then stops, one that never passes its health checks, with application traffic held with 503 meanwhile); and release tags, one signed by the listed key accepted, unsigned and unlisted-key ones refused. Users, identities, buckets and files unchanged throughout ([upgrades](../guides/upgrades.md)) | [docker-upgrade-checks.json](../evidence/docker-upgrade-checks.json) | 45 |

### Empty server, simulated in a local VM

A disposable Fedora 44 Cloud VM with 4 vCPU and 6 GiB, a clean clone, the one-command acceptance with `--install-unit --first-project`, then a reboot ([lab/vm-rehearsal.sh](../../lab/vm-rehearsal.sh)). Not a real server: no public network or certificate. The runs found ten defects the workstation could not show, all fixed with tests; they are listed in the summary record.

| What | Evidence | Checks |
|---|---|---|
| Summary: VM, command, reboot, idle footprint, defects found | [vm-empty-server-rehearsal.json](../evidence/vm-empty-server-rehearsal.json) | recorded |
| Install, supervised start, console, management realm, operator bootstrap, unit, clean stop | [vm-empty-server-acceptance.json](../evidence/vm-empty-server-acceptance.json) | 12 |
| First project: login, project, environment provisioned, key, supabase-js Auth sign-up, REST and Storage through the gateway, a browser's cross-origin call, revocation refused with 401 (rerun 2026-09-25) | [vm-empty-server-first-project.json](../evidence/vm-empty-server-first-project.json) | 15 |
| Reboot: the service and the environment came back without help | [vm-empty-server-rehearsal.json](../evidence/vm-empty-server-rehearsal.json) | passed |

Idle with one environment, the containers used about 250 MiB and the supervisor about 130 MiB. The preflight still reserves container limits (2304 MiB for that placement) plus 2560 MiB for the host; see [choosing a server](../guides/choosing-a-server.md).

### Roadmap milestones in the VM, 2026-09-25

The steps that need a server, rehearsed in the same kind of VM (4 vCPU, 6656 MiB, the pinned images copied from the workstation) with [lab/vm-milestones.sh](../../lab/vm-milestones.sh) until a server is bought. A local CA stands in for a public certificate. Summary with scope and limits: [vm-milestones-2026-09-25.json](../evidence/vm-milestones-2026-09-25.json).

| What | Evidence | Checks |
|---|---|---|
| Pinned console port, TLS proxy as a unit, reboot, then the whole first project over HTTPS | [vm-https-first-project.json](../evidence/vm-https-first-project.json) | 15 |
| Invitations against the real management Auth: invite, preview, redeem, sign in, viewer refused, cancel, remove | [vm-invitation-check.json](../evidence/vm-invitation-check.json) | 16 |
| Backup of both environments while they served 13326 requests, none failed (small databases, about 1 s) | [vm-backup-traffic.json](../evidence/vm-backup-traffic.json) | 11 |
| Restore of both backups onto a second VM with `sbarbase relink` and `restore`; old users signed in with old passwords | [vm-restore-drill.json](../evidence/vm-restore-drill.json) | 16 |
| Upgrade to a newer PostgREST, a broken version moved back by itself, then a return to the installed pins | [vm-upgrade-checks.json](../evidence/vm-upgrade-checks.json), [vm-upgrade-return.json](../evidence/vm-upgrade-return.json) | 10 + 4 |
| Environment limit: at 6656 MiB memory admission refuses the third environment; about 258 MiB used at rest | [vm-environment-limit.json](../evidence/vm-environment-limit.json) | 4 |
| Soak: 60 minutes idle with two environments, no restart, flat memory, disk and logs | [vm-soak.json](../evidence/vm-soak.json) | 3 |

These runs found five defects, all fixed with tests: the acceptance waited for the console before the images existed; an image pull gave up after one immediate retry; redeeming an invitation answered 500; the first upgrade after an acceptance was refused because the acceptance rewrites tracked evidence; and SELinux refuses a unit that runs Bun from `/home`. GitHub private vulnerability reporting was turned on the same day.

### Deployment path (workstation only)

| What | Evidence | Checks |
|---|---|---|
| Server acceptance script, end to end, unit installed as root | [server-acceptance-latest.json](../evidence/server-acceptance-latest.json) | 13 |
| Deployment rehearsal, source and target lifecycle | [deployment-rehearsal.json](../evidence/deployment-rehearsal.json) | 11 |
| Supervised path under systemd | [supervised-run.json](../evidence/supervised-run.json) | 10 |
| TLS termination | [tls-termination.json](../evidence/tls-termination.json) | 23 |
| Recovery target placement | [target-placement-rehearsal.json](../evidence/target-placement-rehearsal.json) | 12 |
| Pinned images present and matching digests | [pinned-images.json](../evidence/pinned-images.json) | 5 pins |

The itemised server matrix is [deployment readiness](deployment-readiness.md).

## Update channel, 2026-09-25

Built on 2026-09-25 ([upgrades](../guides/upgrades.md)): signed release tags checked against `deploy/release-signers`; four classes computed from the diff (safe, attended for an Auth, Storage or Realtime pin change, rebuild, manual); the console notice and Updates page for the installation operator, with the supervisor's install verdict; one-click install of safe releases and of attended ones after an acknowledgement; opt-in automatic updates for safe releases inside a maintenance window; a drain before the move; backups kept for the last 3 upgrades that moved the checkout; a control state snapshot; a start guard that runs first on every start; application traffic held and management changes refused with 409 until a health round, including probes through the gateway, passes within 120 seconds; and the way back.

| What | Evidence | Result |
|---|---|---|
| Release channel and classes, request, settings and verdict files, automatic decision and tries, snapshot and restore, guard, drain, health-gated confirmation, the CI upgrade check's own logic (the upgrade file also holds the older upgrade tests) | `lab/test_release_channel.py`, `lab/test_updates.py`, `lab/test_upgrade.py`, `lab/test_upgrade_health.py`, `lab/test_upgrade_guard.py`, `lab/test_upgrade_drain.py`, `lab/test_upgrade_check.py` | 214 Python tests, OK, on the workstation on 2026-09-26; the whole Python suite 1041, OK |
| Traffic hold, the probe past it, updates routes, console page logic | `tests/hold.test.ts`, `tests/hold-bypass.test.ts`, `tests/updates-routes.test.ts`, `tests/updates-ui.test.ts` | 58 Bun tests pass; the whole Bun suite 295 pass |
| The three CI cases (a release that migrates the catalog and then fails, one that fails its health checks, an unsigned tag) | [docker-upgrade-checks.json](../evidence/docker-upgrade-checks.json), CI run 36195831433 on 2026-09-25, a clean CI machine | 45 of 45 checks pass |
| The command line cycle in the rehearsal VM under systemd: `start --to` a newer PostgREST, the operator's rollback, a broken version moved back by itself with the snapshot restored | [vm-upgrade-checks.json](../evidence/vm-upgrade-checks.json), 2026-09-26, the local VM | 26 of 26 checks pass |
| The channel in the rehearsal VM as an operator uses it: a check and an install through the management API the console uses, a broken release moved back by itself, automatic mode inside its window, an attended Auth release after the acknowledgement; the drain, exit 42, the systemd restart, the guard, the hold | [vm-channel-checks.json](../evidence/vm-channel-checks.json), 2026-09-26, the local VM, releases from a local repository signed with a throwaway key | 114 of 114 checks pass |

Beyond unit tests, the CI cases above ran on a clean CI machine with `lab/upgrade.py` from the command line, and the local rehearsal VM ran both the command line cycle and the channel itself: releases asked for through the management API and by automatic mode, carried out by the supervisor under systemd. Nothing about the channel has run on a real server, with the canonical repository or with the maintainer's key, and the console's "Roll back", a way back from an attended release and a release that needs a rebuild have not run outside unit tests. Since the first pass of this section, every way back moves the checkout by force (local changes copied to `.lab/upgrades/aside-<time>/`), an operator's rollback refuses local changes and names them, the guard stops retrying after 3 failed moves and either starts the previous version, stays on the confirmed one, or stays stopped as `stuck`, and only upgrades that moved the checkout protect their backups; all of that is unit tested only. The maintainer's release key was listed in `deploy/release-signers` on 2026-09-26; a version before that commit refuses every release as unsigned.

A supervisor killed instead of stopped (during the health checks or at any other time) no longer leaves the systemd service down: the next start stops the owned containers it left running, volumes kept, before the preflight, unless a live supervisor or worker holds its lock or a receipt or journal waits for reconciliation (`lab/leftover_runtime.py`, [server deployment](../guides/server-deployment.md)). The unit gained a line for it, so an installed unit takes effect only after `supervise --apply`. Unit tested only; no live kill has been run.

Every backup run (`create all`, daily and before each upgrade) now also backs up Storage's shared `storage_metadata` database to `.lab/backups/storage/`, restored with `lab/backup.py restore-storage`, so an attended release that changes the Storage pin can be undone completely by restoring the run taken before it, by hand ([backup and restore](../guides/backup-and-restore.md)). Unit tested only; it has not run against a live Storage.

The `sbarbase` command reaches the channel too: `upgrade channel`, `upgrade start --release vX.Y.Z` with `--allow-class`, and `upgrade rollback --check` ([the sbarbase command](../guides/cli.md)).

## Resources

A start needs its containers' memory limits plus a 2560 MiB reserve: 1792 MiB of limits on an empty server and 512 MiB more per environment, and CPU ceilings of at most twice the cores after one core is kept for the host. At most four environments per installation are allowed by a lab guard for now; the management API refuses the fifth with 409 before queueing it, and the runtime guard still enforces it. The configured ceilings for the workstation's retained combined placement are 5888 MiB of container memory and 5.75 CPUs, admitted under a 6 GiB and 6 CPU cap plus a 2560 MiB host reserve. These are allocation limits, not measured demand or a hardware recommendation. Sustained mixed load has not been measured, so there is no validated maximum of 10 or 100 environments, and daily visitor counts alone cannot size a server.

## What does not exist yet

- A rehearsal on a real server: a public certificate and DNS, a seven-day soak with real traffic, and capacity under load. Everything else in milestone 1 passed in a local VM.
- The connection pooler and an exposed operator Cron feature in the legacy workflow. Accepted pinned original-image Cron/HTTP effects are separate bounded prerequisites. `SUPABASE_DB_URL` inside Edge Functions.
- Point-in-time recovery, SSH or rsync targets for the off-host copies (S3-compatible storage only), and rebuilding a whole lost server from the off-site copies in one step (each environment's copy restores, on a new installation after `sbarbase relink` recreates its client, project and environment with their original ids; the installation manifest records what the backups need, but members, keys and settings do not travel with it yet, and this path was rehearsed onto a second VM on 2026-09-25 for environments that never used Studio, Realtime or direct database access).
- Importing schemas other than `public`, Vault secrets and cron jobs from a Supabase project (the [import](../guides/move-from-supabase.md) moves `public`, users, rows and files).
- Automatic recovery of later-stage provisioning failures; they block until an operator reconciles them.
- Adoption of any upstream release through the update policy. A run of the update channel on a real server: automatic updates exist, off by default, and have run only in unit tests, the CI cases and the local rehearsal VM (above).
- MFA and login rate limits. Moving a project between clients revokes its API keys but does not rotate its JWT signing key or direct database password. Deleting an environment keeps its runtime (database, containers, files), which still counts against the environment limit; reclaiming it is not built.
- Multi-server placement and coordination.
- Resumable (TUS) uploads through the gateway; standard uploads go up to the upload limit, 50 MiB by default.

## Current priorities

Follow the October product plan and execution method, with the ledger as the scoped evidence index. Continue the required service, recovery and security/profile contracts after the accepted bounded configured-effects fragment. The owner selected this computer as an isolated local server, with historical Fedora 44 x86_64 observations; the changed tree needs fresh host-bound verification, and buying an external server is not a prerequisite. Independent-host portability and physical HA require separate evidence. This page does not authorize infrastructure purchases or host changes.

## September next-step record

The September roadmap proposed a real server: [milestone 1 of the roadmap](../engineering/plans/2026-09-23-roadmap.md), to repeat on it what the VM rehearsed on 2026-09-25 (above). On the workstation, the attended generation migration of the retained database ran on 2026-09-25 with every row count unchanged ([evidence](../evidence/generation-migration-retained.json)). The durable lifecycle probe, reworked into a non-destructive stop and start of the retained runtime, passed 31 checks ([evidence](../evidence/durable-lifecycle-restart.json)). On that fixture the mixed SDK load passed with no failed operation ([evidence](../evidence/sdk-policy-regression.json)), and the sustained arrival run failed: 14 of 600 target arrivals ended without an HTTP status while every neighbour arrival was correct ([evidence](../evidence/gateway-sustained-failure.json)). The cause was found in the loopback listener and fixed without Docker: a refused request's connection was announced as closing but stayed open, a pooled client reused it, and a one-second cleanup cut off the next long request on it ([details](../engineering/RESOURCE-POLICY.md)). The live sustained run was repeated after the fix and passed: 555 correct 429s, 45 correct 200s and all 60 neighbour arrivals correct, with no request left without an HTTP status ([evidence](../evidence/gateway-sustained-checks.json)). The pressure-sampling mode and the experimental-class phase are not built.

## Preserved October verification history

These are preserved statements from earlier source windows, with their original verdicts and links. They do not describe the current V11 outcome. Later HTTPError lifecycle evidence does not retroactively establish their allocation causes.

Current cutoff, 2026-10-05: six independently reviewed source fixes are integrated. Two SQLite paths now close their connections after the original transaction exits; 27 expected-negative fixture methods capture and assert complete stderr in three files; one tempfile fixture owns close and registers unlink before its fallible write. These are source repairs, with fresh full native verification UNRUN. Earlier 875-file source, identity, mode and full results are historical bindings and cannot approve this changed tree.

The separate public55 V3 role has actual MET: 25 component and 30 pump tests passed, original 290 and added 4 actual mode/byte captures matched, and all three known helpers were removed with no unresolved resources. Public BusyBox V8 help metadata also has actual MET: fixed-path Bash 5.3.3 and BusyBox 1.37.0 help returned zero, with its one helper removed. Neither result accepts private request operation, timeout timing/signal behavior, installed binary source provenance, candidate startup, cold/warm Vault or Cron continuity, physical restore or production.

The preserved warning-window V4 negative run reported 1447 ordinary tests OK but six ResourceWarnings, native 1 and observed window 839 for every warning. Three records have exact sqlite3.Connection type; three report tempfile.py line 484 with NoneType source. Observation windows are not allocation causes, and the earlier packet did not establish the three tempfile allocation causes. Fresh complete source/identity/mode/full bindings, final-revision CI and manually dispatched empty-host acceptance remain required; published 0e745394 CI and Website results retain only their earlier revision scope.

Current scope updated 2026-10-05. Package 0.2.0 remains a development snapshot; [0.1.0](../../CHANGELOG.md) is the historical source release. No signed 0.2.0 or production release is established.

Published source `0e745394` passed [CI run 37206229288](https://github.com/M7MMAD-OMAR/sbarbase/actions/runs/37206229288), including checks, Python floor and Docker installation. Empty-host acceptance was **SKIPPED** and needs a fresh manually dispatched run. The separate build-only [Website run 37206294329](https://github.com/M7MMAD-OMAR/sbarbase/actions/runs/37206294329) passed. These observations belong to that revision; later source and documentation edits need their own gates.

The [project goal](../../PROJECT_GOAL.md), [product plan](../engineering/plans/2026-10-03-product-and-portability-plan.md), [execution method](../engineering/plans/2026-10-03-gauntlet-execution-method.md) and [native placement contract](../engineering/plans/2026-10-03-native-placement-identity.md) define the current scope. The [ledger](../engineering/gauntlet-ledger.json) records source-bound evidence and preserved losses. Local ignored packets are not distributed evidence or a supported operator interface.

| Current evidence area | Accepted scope and remaining gate |
|---|---|
| [Original-image configured effects](../engineering/reviews/2026-10-04-native-cron-effects.md) | Bounded configured Cron writes and native HTTP effects on the pinned original image. This does not accept the PostgreSQL 17.11 candidate, full service recovery or production. |
| [Streaming backup transport](../engineering/reviews/2026-10-04-streaming-backup-transport.md) | Historical exact-source offline transport acceptance. Native physical restore and remote S3 remain unaccepted. |
| [Patched PostgreSQL 17.11](../engineering/reviews/2026-10-04-patched-postgres-observations.md) | Public image/configuration/script observations and preservation only. Candidate startup, effective settings, real private request pump, candidate Cron effects, warm Vault continuity and physical restore remain UNRUN or unaccepted. |
| [Root-key publication](../engineering/reviews/2026-10-04-native-root-key-source.md) and [native filesystem guards](../engineering/reviews/2026-10-04-native-root-key-filesystem.md) | Scoped publication, preservation and guard observations. The primitive is not wired into candidate startup; retained key material is not a recovery proof. |
| [Private diagnostics](../engineering/reviews/2026-10-04-private-diagnostics-fifo.md), [bridge](../engineering/reviews/2026-10-04-private-diagnostics-bridge.md) and [live prefixes/credentials](../engineering/reviews/2026-10-04-live-diagnostics-and-credentials.md) | Separately scoped fixture acceptances. The later 25 public FIFO component regressions passed locally; they do not prove real private role ownership, complete launcher/client/pump operation or general secret safety. |
| [Native startup preparation](../../deploy/verify/native-startup-preparation.md) | Eight integrated public source files define material preparation and an independent daemon guardian. Preparation-only status cannot accept PostgreSQL startup. The focused 14/9/29 cases passed in the completed V9 initV2 run. The full suite has an admission minimum of 1356; actual discovery executed 1447 cases and failed with one failure and 15 errors. This earlier completed result remains historical NOT_MET; the later mode/scratch full window also refused despite raw unittest OK, as described below. |

The completed V9 initV2 baked run passed all 14 material, 9 executor and 29 guardian cases, 52 focused cases. Its full run executed 1447 tests in 72.300 seconds and is NOT_MET, with one failure and 15 errors. Seven server-acceptance permission errors, eight read-only test-scratch errors and one executable-bit assertion prevented full acceptance. Both 290-file captures and all 875 public bindings matched, and all six known helpers were removed with no unresolved resources. The earlier release Git failure did not recur.

The historical mode/scratch correction had independently accepted source and source/identity build evidence; its old 875 bindings are no longer current after the six integrated source repairs. Its completed full window is NOT_MET: raw unittest reports 1447 tests OK in 51.862 seconds, all 52 focused cases passed, both 290-file captures and both 875-entry mode jobs matched, and all eight known helpers were removed with no unresolved resources. The full helper nevertheless returned native 1 without its required positive terminal. Its original child cause is not established by that packet; 70 expected-negative fixture stderr lines also violate the parent progress grammar. A later independently accepted negative-only numeric diagnostic reports 1447 raw tests OK in 51.835 seconds, stage 7 with six captured warnings, native 1 and exact cleanup. This identifies that diagnostic invocation's warning-gate refusal, not the earlier invocation's precise cause, and does not accept full verification. The reviewed 27-method expected-stderr repair is now integrated together with two SQLite close fixes and one tempfile setup-failure repair. Their changed-source native regression remains UNRUN. The later warning-window diagnostic identifies observation window 839 and limited type metadata, not allocation causes; the earlier packet did not establish the three tempfile allocation causes. Fresh exact-source full verification remains required. Native preparation, private pump, startup, Vault continuity, physical restore and production remain unaccepted.

The accepted negative diagnostic retained 17 commands and 34 complete EOF streams, totaling 378181 bytes, and closed in 55.055934754 seconds under unchanged bounds. Its real result has one runner result, discovered/run 1447, runpy status 0 and zero failures, errors, skips, expected failures and unexpected successes; warnings are six, not zero. Native 1 and a closed FAIL receipt remain negative evidence. It did not rerun the captures, mode jobs or focused roles and cannot transfer their evidence to later source edits.

The historical GNU-only PostgreSQL 17.11 metadata probe remains NOT_MET. The later fixed-path BusyBox help metadata probe has scoped actual MET with Bash 5.3.3 and BusyBox 1.37.0, native 0 and exact known-helper cleanup. It does not establish GNU semantics, timeout behavior, installed source provenance, private client execution or an accepted private runtime profile.

The accepted public55 role retained 47 commands and 94 complete EOF streams, totaling 5411798 bytes in 6.259089567000046 seconds; all three helpers were removed. The later BusyBox metadata role retained 12 commands and 24 complete EOF streams, totaling 12761 bytes in 1.1989835960011987 seconds; its one helper was removed. The negative V4 warning diagnostic retained 17 commands and 34 streams and 1447 ordinary outcomes OK in 52.936 seconds, but native 1 and its no-warning refusal remain negative evidence. None of these prior-source observations accepts the six changed source files.

The notification failure fixture now captures and asserts its entire expected startup diagnostic. The process-boundary fixture saves the caller's actual subreaper state and registers verified restoration before changing it; a nested failure regression checks both original states. The full baked test helper uses explicit init admission while retaining its original PID and resource caps. These test isolation changes are not production guardian lifecycle changes or proof of a complete repair.

The developer's computer is being used as a local Linux Docker server for declared isolated fixtures. This does not establish an independent clean host, public DNS/TLS, a production deployment or all-host support. Full service operation, candidate security maintenance, Cron/Vault continuity and physical recovery still need their own actual evidence.

The September tables below are historical workstation and local-VM observations, not a freshly verified matrix for this tree. No independent public-server pilot or fixed projects-per-server capacity is established. Complete recovery, key/object continuity, security maintenance, HA and PITR remain open.
