[العربية](status.ar.md)

# Status

The single place for what works, what does not, and every number. Updated 2026-09-25. Current source release: [0.1.0](../../CHANGELOG.md) (2026-09-21), plus the unreleased container generation migration.

Everything below was verified on one development workstation, except the empty-server rehearsal, which ran in a local virtual machine. **Nothing has been run on a real server yet.** Nothing here certifies production readiness or security, and no fixed number of projects per server is claimed.

## Test suites, 2026-09-24

Run from the repository root in a clean container with Python 3.14 and `cryptography`, as root. These suites do not start containers; they also pass with no Docker daemon reachable, which CI now checks.

| Suite | Command | Result |
|---|---|---|
| Python unit tests | `DOCKER_HOST=unix:///var/run/docker.sock /usr/bin/python3 -m unittest discover -s lab -p 'test_*.py'` | 642 tests, OK; 4 skipped as root or where `/usr/bin/python3` is older than 3.14, each with its reason; none skipped on CI |
| Bun tests (root) | `bun test` | 101 pass, 0 fail, 620 assertions, 20 files (the 19 in `tests/` plus `website/tests/site.test.ts`) |
| Website | `cd website && bun run build && bun test` | build OK; 2 pass, 0 fail, 40 assertions |
| Console typecheck | `bun run typecheck:ui` | passes |

Earlier pages recorded other totals (for example 575 Python and 87 Bun tests at the 0.1.0 release gate, and 625 and 93 on the workstation on 2026-09-23). Those were correct for their date and scope; this table replaces them.

On 2026-09-25 the Python suite had 765 tests: `OK` with no skips on the workstation, `OK (skipped=2)` there with no Docker daemon reachable, and in a throwaway `python:3.12` container `OK (skipped=6)` as root and `OK (skipped=4)` as an unprivileged user. Each skip names what the host lacks: root for a permission refusal, a `/usr/bin/python3` of 3.12 or a Docker daemon for the acceptance script, `systemd-analyze`, or a block device for `/`.

### Python interpreters, 2026-09-25

The preflight now accepts `/usr/bin/python3` 3.12 or newer, which admits the interpreters Ubuntu 24.04 and Debian 13 ship. To check that the code runs on them, the same Python suite ran in throwaway containers as an ordinary user (uid 1000). The checkout was mounted, Bun was on the path and no Docker daemon was reachable. Ubuntu and Debian used their own `python3` and `python3-cryptography` packages; the `python:` images used the current `cryptography` wheel. `python -m compileall lab deploy` passes on all of them.

| Interpreter | Result |
|---|---|
| Ubuntu 24.04: `python3` 3.12.3, `python3-cryptography` 41.0.7 | 768 tests; 3 skipped; 6 fail, all bound to the host (below) |
| Debian 13: `python3` 3.13.5, `python3-cryptography` 43.0.0 | 768 tests; 3 skipped; 7 fail, bound to the host |
| `python:3.12` (3.12.14), `python:3.13` (3.13.15) | 768 tests; 3 skipped; the same 7 fail |
| `python:3.14` (3.14.7), run as a control | 768 tests; 3 skipped; the same 7 fail |
| The workstation's `/usr/bin/python3` 3.14.7 | 768 tests, OK |

The control fails the same 7 tests, so the failures come from the container, not the interpreter version. Five need a block device behind `/` (the IO limits refuse with `io_device_unavailable`), one needs `systemd-analyze`, and one needs a home directory for uid 1000, which the Ubuntu image has and the others do not. The 3 skips need a Docker daemon or a device source for `/`. Neither Ubuntu 24.04 nor Debian 13 has had an install rehearsed end to end; only Fedora 44 has.

## Live evidence

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
| Upgrade to a newer PostgREST with `lab/upgrade.py`, then a broken version that the supervisor moves back from by itself, with users, identities, buckets and files unchanged ([upgrades](../guides/upgrades.md)) | [docker-upgrade-checks.json](../evidence/docker-upgrade-checks.json) | 10 |

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

Built on 2026-09-25 ([upgrades](../guides/upgrades.md)): signed release tags checked against `deploy/release-signers`; four classes computed from the diff (safe, attended for an Auth, Storage or Realtime pin change, rebuild, manual); the console notice and Updates page for the installation operator, with the supervisor's install verdict; one-click install of safe releases and of attended ones after an acknowledgement; opt-in automatic updates for safe releases inside a maintenance window; a drain before the move; backups kept for the last 3 upgrades; a control state snapshot; a start guard that runs first on every start; application traffic held and management changes refused with 409 until a health round, including probes through the gateway, passes within 120 seconds; and the way back.

| What | Evidence | Result |
|---|---|---|
| Release channel and classes, request, settings and verdict files, automatic decision and tries, snapshot and restore, guard, drain, health-gated confirmation, the CI upgrade check's own logic (the upgrade file also holds the older upgrade tests) | `lab/test_release_channel.py`, `lab/test_updates.py`, `lab/test_upgrade.py`, `lab/test_upgrade_health.py`, `lab/test_upgrade_guard.py`, `lab/test_upgrade_drain.py`, `lab/test_upgrade_check.py` | 178 Python tests, OK, on the workstation |
| Traffic hold, the probe past it, updates routes, console page logic | `tests/hold.test.ts`, `tests/hold-bypass.test.ts`, `tests/updates-routes.test.ts`, `tests/updates-ui.test.ts` | 58 Bun tests pass |
| The three CI cases (a release that migrates the catalog and then fails, one that fails its health checks, an unsigned tag) | in the CI job; no recorded run | **not run** |
| VM rehearsal of a real bump and back through the channel, the guard, the drain, an attended release | none yet | **not run** |

Unit tests only: nothing about the channel has run live or in the VM. No release signing key is listed in `deploy/release-signers` yet, so every release is refused as unsigned until one is. The CI upgrade run in the Docker table above and the VM run ([vm-upgrade-checks.json](../evidence/vm-upgrade-checks.json)) used `lab/upgrade.py start --to`, not the channel, the console or automatic updates.

## Resources

A start needs its containers' memory limits plus a 2560 MiB reserve: 1792 MiB of limits on an empty server and 512 MiB more per environment, and CPU ceilings of at most twice the cores after one core is kept for the host. At most four environments per installation are allowed by a lab guard for now; the management API refuses the fifth with 409 before queueing it, and the runtime guard still enforces it. The configured ceilings for the workstation's retained combined placement are 5888 MiB of container memory and 5.75 CPUs, admitted under a 6 GiB and 6 CPU cap plus a 2560 MiB host reserve. These are allocation limits, not measured demand or a hardware recommendation. Sustained mixed load has not been measured, so there is no validated maximum of 10 or 100 environments, and daily visitor counts alone cannot size a server.

## What does not exist yet

- A rehearsal on a real server: a public certificate and DNS, a seven-day soak with real traffic, and capacity under load. Everything else in milestone 1 passed in a local VM.
- The connection pooler and cron. `SUPABASE_DB_URL` inside Edge Functions.
- Point-in-time recovery, SSH or rsync targets for the off-host copies (S3-compatible storage only), and rebuilding a whole lost server from the off-site copies in one step (each environment's copy restores, on a new installation after `sbarbase relink` recreates its client, project and environment with their original ids; the installation manifest records what the backups need, but members, keys and settings do not travel with it yet, and this path was rehearsed onto a second VM on 2026-09-25 for environments that never used Studio, Realtime or direct database access).
- Importing schemas other than `public`, Vault secrets and cron jobs from a Supabase project (the [import](../guides/move-from-supabase.md) moves `public`, users, rows and files).
- Automatic recovery of later-stage provisioning failures; they block until an operator reconciles them.
- Adoption of any upstream release through the update policy. A live run of the update channel: automatic updates exist, off by default, but have unit tests only (above).
- MFA and login rate limits. Moving a project between clients revokes its API keys but does not rotate its JWT signing key or direct database password. Deleting an environment keeps its runtime (database, containers, files), which still counts against the environment limit; reclaiming it is not built.
- Multi-server placement and coordination.
- Resumable (TUS) uploads through the gateway; standard uploads go up to the upload limit, 50 MiB by default.

## Next step

The real server: [milestone 1 of the roadmap](../engineering/plans/2026-09-23-roadmap.md), to repeat on it what the VM rehearsed on 2026-09-25 (above). On the workstation, the attended generation migration of the retained database ran on 2026-09-25 with every row count unchanged ([evidence](../evidence/generation-migration-retained.json)). The durable lifecycle probe, reworked into a non-destructive stop and start of the retained runtime, passed 31 checks ([evidence](../evidence/durable-lifecycle-restart.json)). On that fixture the mixed SDK load passed with no failed operation ([evidence](../evidence/sdk-policy-regression.json)), and the sustained arrival run failed: 14 of 600 target arrivals ended without an HTTP status while every neighbour arrival was correct ([evidence](../evidence/gateway-sustained-failure.json)). The cause was found in the loopback listener and fixed without Docker: a refused request's connection was announced as closing but stayed open, a pooled client reused it, and a one-second cleanup cut off the next long request on it ([details](../engineering/RESOURCE-POLICY.md)). The live sustained run was repeated after the fix and passed: 555 correct 429s, 45 correct 200s and all 60 neighbour arrivals correct, with no request left without an HTTP status ([evidence](../evidence/gateway-sustained-checks.json)). The pressure-sampling mode and the experimental-class phase are not built.
