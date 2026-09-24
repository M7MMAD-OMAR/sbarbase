[العربية](status.ar.md)

# Status

The single place for what works, what does not, and every number. Updated 2026-09-24. Current source release: [0.1.0](../../CHANGELOG.md) (2026-09-21), plus the unreleased container generation migration.

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

### Import from Supabase (phase 0 only)

| What | Evidence | Checks |
|---|---|---|
| Read-only inspection of a source database: refusals, warnings and manual steps before any dump, on the pinned image as the `postgres` role | [import-inspect-checks.json](../evidence/import-inspect-checks.json) | 6 |

The dump, restore, object copy and verification phases do not exist yet; see [the migration plan](../engineering/plans/2026-09-23-verification-and-migration-plan.md).

### Install with Docker, daily backups, Studio and upgrades (CI, clean runner)

On a clean GitHub runner with only Docker, CI runs the Docker install on every change ([install with Docker](../guides/docker.md)). Not a real server: no public network, certificate or host reboot.

| What | Evidence | Checks |
|---|---|---|
| Build and start, first operator, first project through supabase-js, container restart, clean stop | [docker-install-checks.json](../evidence/docker-install-checks.json) | 15 |
| Back up one environment while it serves, change rows, users and files, restore, compare with the backup, discard the set-aside state | [docker-backup-restore.json](../evidence/docker-backup-restore.json) | 15 |
| Supabase Studio for one environment: started on demand, entered with the console ticket, table list, SQL, users and buckets through it, refused without the session or with another environment's session, stopped ([Studio guide](../guides/studio.md)) | [docker-studio-checks.json](../evidence/docker-studio-checks.json) | 22 |
| Upgrade to a newer PostgREST with `lab/upgrade.py`, then a broken version that the supervisor moves back from by itself, with users, identities, buckets and files unchanged ([upgrades](../guides/upgrades.md)) | [docker-upgrade-checks.json](../evidence/docker-upgrade-checks.json) | 10 |

### Empty server, simulated in a local VM

A disposable Fedora 44 Cloud VM with 4 vCPU and 6 GiB, a clean clone, the one-command acceptance with `--install-unit --first-project`, then a reboot ([lab/vm-rehearsal.sh](../../lab/vm-rehearsal.sh)). Not a real server: no public network or certificate. The runs found ten defects the workstation could not show, all fixed with tests; they are listed in the summary record.

| What | Evidence | Checks |
|---|---|---|
| Summary: VM, command, reboot, idle footprint, defects found | [vm-empty-server-rehearsal.json](../evidence/vm-empty-server-rehearsal.json) | recorded |
| Install, supervised start, console, management realm, operator bootstrap, unit, clean stop | [vm-empty-server-acceptance.json](../evidence/vm-empty-server-acceptance.json) | 12 |
| First project: login, project, environment provisioned in 10 s, key, supabase-js Auth sign-up, REST and Storage through the gateway, revocation refused with 401 | [vm-empty-server-first-project.json](../evidence/vm-empty-server-first-project.json) | 13 |
| Reboot: the service and the environment came back without help | [vm-empty-server-rehearsal.json](../evidence/vm-empty-server-rehearsal.json) | passed |

Idle with one environment, the containers used about 250 MiB and the supervisor about 130 MiB. The preflight still reserves container limits (2304 MiB for that placement) plus 2560 MiB for the host; see [choosing a server](../guides/choosing-a-server.md).

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

## Resources

A start needs its containers' memory limits plus a 2560 MiB reserve: 1792 MiB of limits on an empty server and 512 MiB more per environment, and CPU ceilings of at most twice the cores after one core is kept for the host. At most four environments per installation are allowed by a lab guard for now; the management API refuses the fifth with 409 before queueing it, and the runtime guard still enforces it. The configured ceilings for the workstation's retained combined placement are 5888 MiB of container memory and 5.75 CPUs, admitted under a 6 GiB and 6 CPU cap plus a 2560 MiB host reserve. These are allocation limits, not measured demand or a hardware recommendation. Sustained mixed load has not been measured, so there is no validated maximum of 10 or 100 environments, and daily visitor counts alone cannot size a server.

## What does not exist yet

- A rehearsal on a real server. The empty-server install passed in a local VM only.
- Realtime, Edge Functions, the connection pooler and cron.
- Automatic copies of the daily backups to another machine (copy them yourself, as the backup guide shows), and point-in-time recovery.
- Importing a project from Supabase Cloud or a self-hosted stack beyond the read-only inspection.
- Automatic recovery of later-stage provisioning failures; they block until an operator reconciles them.
- Adoption of any upstream release through the update policy; unattended upgrades (an operator starts each one with [lab/upgrade.py](../../lab/upgrade.py)).
- Complete project transfer between clients, invitations, MFA and login rate limits.
- Multi-server placement and coordination.
- Large or resumable uploads, browser CORS and OAuth providers through the gateway.

## Next step

The real server: [milestone 1 of the roadmap](../engineering/plans/2026-09-23-roadmap.md). On the workstation, the attended generation migration of the retained database is still pending; until it runs, the durable container-recreation probe stays disabled and the arrival-driven pressure and mixed SDK load measurements stay blocked.
