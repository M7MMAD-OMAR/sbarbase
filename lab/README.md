# Local component laboratory

This is an isolated compatibility experiment, not the Sbarbase product or a production deployment. It initially compares three environment databases on one PostgreSQL 17 instance with original Supabase Auth and PostgREST services. Storage, Realtime, functions, UI, recovery and upgrade gates remain separate work.

All containers use the `sbarbase-lab` ownership label, an internal bridge with no public port publishing, dedicated networking and a dedicated volume. Secrets stay in ignored `.secrets/`. Run with `/usr/bin/python3 lab/run.py up`, `status`, or `stop`. Stop preserves data. No existing services are managed.

Live probes: `/usr/bin/python3 lab/verify.py` and `bun lab/sdk-check.ts`. The SDK probe starts the initial environment-key gateway on loopback with new lab keys backed by persistent hashed metadata, revoked at probe completion. It is not a complete production gateway. Test fixture `auth.uid()` is a minimal JSON-claim helper on stock PostgreSQL; full Supabase database bootstrap is a separate gate. Stop the lab between test sessions to preserve workstation headroom.

Recovery probes: `/usr/bin/python3 lab/retry-check.py` now delegates to the isolated `partial-database-crash-check.py` probe. It verifies partial SQL states, closed database defaults and blocked automatic replay; it no longer retries a closed database in the retained component cluster. `/usr/bin/python3 lab/restore-check.py` streams a logical backup in memory to a temporary database and verifies source/neighbor preservation. Neither probe is a complete platform restore.

Management probe: `bun lab/management-check.ts` uses a_stage as a temporary management Auth realm and a_prod as the application realm. It exercises the actual HTTP management handler and Supabase SDK getUser. This is test-only realm substitution, not a production management deployment.

Dynamic provisioning: `/usr/bin/python3 lab/worker.py` drains `.lab/control.sqlite` operations with an exclusive worker lock. `bun lab/provision-check.ts` tests lost-completion recovery. See [scope and limitations](../docs/engineering/PROVISIONING.md). Run `lab/verify.py` before the SDK probe to install test fixtures in all enrolled environments.

Connection probe: `bun lab/connection-check.ts` exercises management-authenticated publishable key issuance, connection discovery, SDK access and revocation. It requires the provisioning probe and its test fixtures, and always revokes its issued key afterward.

Distribution probe: `/usr/bin/python3 lab/distro-check.py` starts a separate ephemeral Supabase PostgreSQL/Auth pair without network exposure, checks upstream bootstrap and migration behavior, and removes its containers afterward. It does not replace the component lab.

Upstream environment probe: `/usr/bin/python3 lab/upstream-environments.py` tests two independent Auth/REST databases on the pinned Supabase PostgreSQL image with real migrated identity helpers. It reuses the environment reconciler and service configuration builders, and removes its owned containers/network/environment files afterward.

Shared Storage probe: `/usr/bin/python3 lab/upstream-environments.py --storage` adds one original multi-tenant Storage process, two scoped storage logins and a separate metadata database. It tests private objects and credential/token boundaries, then removes its temporary resources.

The shared Storage probe also invokes `lab/storage-sdk-check.ts` over stdin to test the original Supabase SDK through the gateway. Do not run this helper with credentials on command-line arguments or save its input.

The Storage probe now rehearses encrypted database+object recovery using `storage_restore_probe.py` and the fixed `storage-files.cjs` helper. It requires Python cryptography. Ciphertext and its separate key remain in ignored local directories, not in the handoff. See [scope](../docs/engineering/reviews/storage-recovery.md).

## Retained runtime configuration

`run.py` resolves the requested image pin to its Docker image config identity and
compares it with a retained container before starting it. It also compares every
requested environment setting without printing secret values. Drift fails closed;
startup never deletes or recreates a database as an implicit upgrade. Image-default
environment variables may remain. This is not a full configuration reconciler: mount,
network, resource-limit and command drift still need checks. The lab's existing
failed-start cleanup policy still applies.

Run `/usr/bin/python3 -m unittest discover -s lab -p test_runtime_reuse.py`.
Six regression tests cover manifest/config digest resolution, ownership recheck,
image drift, missing/changed settings and secret-safe errors. Twelve recorded
read-only live checks verified existing containers and rejected mismatched pins
and credentials without changing their states.

## Durable upstream lifecycle

`/usr/bin/python3 lab/durable_runtime.py up` starts the separate persistent
Supabase PostgreSQL and shared Storage runtime. `/usr/bin/python3 lab/worker.py
--upstream` drains `.lab/upstream/control.sqlite`; it never consumes the stock
component catalog. The historical `bun lab/durable-check.ts` container-recreation
probe is currently disabled before catalog/Docker effects pending explicit HBA
generation migration. `/usr/bin/python3 lab/fresh-worker-check.py` tests current
startup, original services, worker HBA authority and same-container restart in
an isolated disposable namespace. It does not replace recreation coverage.

Source startup now requires a matching generation pin for existing containers.
The retained legacy source has not been adopted and intentionally refuses to
start. Preserve its state and volumes. See [current scope and adoption gate](../docs/engineering/SOURCE-HBA-INTEGRATION.md).

## Container generation migration

`/usr/bin/python3 lab/migrate-generation.py` is the one operation that may
replace a managed database container, and it refuses the retained placement
outright: it is a deliberate operator run, not this command. Its private record
lives beside the generation pin (`.lab/upstream/hba-migration`), and a record,
torn or whole, blocks ordinary startup, worker preflight and a repeated
migration until it completes or an operator reconciles it with `--reconcile`.

`/usr/bin/python3 lab/fresh-worker-check.py --generation-crash all` runs the five
crash tests on the disposable fixture: one SIGKILL at each durable checkpoint,
then the startup refusal, the repeated-migration refusal, exactly one
reconciliation, and the recreated container carrying its resource tier and its
per-device block IO limits on the same pgdata volume with its data intact. Each
phase writes its own `docs/evidence/generation-migration-<phase>.json`. See
[migration design and its three deviations](../docs/engineering/CONTAINER-GENERATION-MIGRATION.md).

`/usr/bin/python3 lab/durable_runtime.py stop` stops owned containers without
deleting volumes. State is in `.lab/upstream`, credentials in `.secrets/upstream`,
resources carry `io.sbarbase.owner=durable-upstream`. Nothing publishes host
ports. Existing stock lab data is not migrated. See
[verified scope and remaining gates](../docs/engineering/reviews/durable-runtime.md).

## Dedicated management and local API

The durable runtime now also provisions a separate `management` database and
Auth process, with independent credentials/signing key, disabled public signup,
and no application REST/Storage route. Existing application identities cannot
authenticate management operations. Private operator bootstrap is available through `lab/bootstrap.py`; the integration
probe creates and removes its own confirmed fixture identity through the private
upstream admin API without sending email.

Run `bun lab/upstream-server.ts` after starting the durable runtime to launch
the composed API on a newly assigned loopback port. Its descriptor is written to
`.lab/upstream/server.json` and removed on normal SIGINT/SIGTERM shutdown. The
server is not a supervisor; restart it after the management Auth endpoint changes.
Application routes reload trusted runtime metadata on each request. A descriptor
file alone is not proof the server is alive.

`bun lab/upstream-management-check.ts` requires the durable lifecycle fixture.
It tests actual management login/memberships, key issuance and revocation across
Auth/REST/Storage, cross-realm/database denial and runtime restart. Its finalizer
removes the test management identity/membership, revokes its key and stops the
runtime. See [scope](../docs/engineering/reviews/upstream-management.md).

## Initial operator setup

After starting the durable runtime, run `/usr/bin/python3 lab/bootstrap.py`
in a terminal. Email and organization are prompted normally; the password is
entered twice without echo. `--stdin` accepts bounded JSON for secure automation.
Do not put passwords in command arguments or shell history. Setup creates only
the initial organization owner, not unrestricted authority over other organizations.

`bun lab/bootstrap-check.ts` tests real Auth creation and interruption recovery
with a private temporary catalog, then deletes its test Auth user and private
files. It requires a running durable runtime. It does not stop the runtime itself;
stop it after testing. Details: [operator setup](../docs/guides/operator-setup.md).

## Local console

Run `bun install --frozen-lockfile`, then `/usr/bin/python3 lab/dev.py`. The foreground runner builds the console, starts the owned runtime and continuously processes queued creation operations. Open its printed loopback URL. Create the initial operator with `lab/bootstrap.py` if needed. Ctrl+C stops the runner and its owned runtime while preserving volumes. Browser sessions are in memory, so reloading requires login. Do not run manual lifecycle commands concurrently with the runner. This is not a production service manager.

This console is the platform layer: organizations, projects, environments, connection details, keys and provisioning status. Environment administration is handed to the original upstream Studio, which is specified in [the integration specification](../docs/engineering/STUDIO-INTEGRATION.md) and is not served by any command yet. When it lands, its route and its authenticated gate join this runner and its admission math.

`bun run typecheck:ui` checks frontend types. `lab/ui-fixture.ts` creates only a
private temporary QA identity attached to the existing durable probe organization;
use its explicit `cleanup` command afterward. It is not operator onboarding.
The captured [real browser workflow](../docs/design/CONSOLE-QA.md) includes the
third durable environment created through the UI and its revoked test key.

## Supervisor failure checks

`/usr/bin/python3 lab/supervisor-check.py` verifies runner exclusion, idle worker restart and graceful shutdown. `/usr/bin/python3 lab/supervisor-recovery-check.py` consumes the fourth retained environment and interrupts its worker after private runtime state is persisted. It verifies recovery with the same runtime identity and one catalog operation. This second check intentionally refuses a repeated fixture; inspect retained state instead of allocating more environments. Both scripts own their runner process and stop the runtime in a finalizer. They require existing durable probe fixtures and available host resources. Neither proves every provisioning crash point or complete disaster recovery.

`/usr/bin/python3 lab/admission-check.py` requires the four retained environments. It queues a refused fifth environment through the trusted local fixture actor, verifies database/container/endpoint identities remain unchanged, checks Auth/REST and console liveness, then stops its runner. Repeats retry the same failed metadata instead of allocating another environment. Exit code 75 from the durable provisioner maps to the safe `capacity_exceeded` status; other failures map to `runtime_failed`. Raw child output never becomes an API error. The guard remains a local count limit.

`/usr/bin/python3 lab/resource_admission.py` reads a resource snapshot while the durable runtime is running. New allocation checks memory and mounted-volume space/inodes before persisting credentials. See [policy and limits](../docs/engineering/RESOURCE-ADMISSION.md). A passing snapshot does not bypass the four-environment guard.

`/usr/bin/python3 lab/connection-limit-check.py` requires a running durable runtime. It temporarily saturates one fixture Auth login, verifies PostgreSQL rejects an extra connection while a neighbor and operator remain available, then terminates its sessions and stops the owned runtime. Do not run it concurrently with normal lab use. [Connection policy and scope](../docs/engineering/CONNECTION-BUDGET.md).

`/usr/bin/python3 lab/pressure_admission.py` reads cgroup v2 pressure from the running database and Storage containers. New environments require both to be below the [documented thresholds](../docs/engineering/PRESSURE-ADMISSION.md). The same module holds the bounded repeated sampler (`series`, `summarise`) and the one response this design supports today (`response`): refuse admissions while the most recent reading is at or over a threshold, and append the crossing to `.lab/pressure-crossings.jsonl`. `/usr/bin/python3 lab/pressure-response-check.py` measures that on disposable containers only, with no retained runtime started and nothing left behind. `lab/noisy-neighbor-check.py` runs a bounded read-only SQL microbenchmark on two existing environments and stops the owned runtime afterward. Do not run it alongside normal lab use. [Results and limits](../docs/engineering/NOISY-NEIGHBOR.md).

`bun lab/sdk-load-check.ts` requires the running durable fixture. It issues temporary scoped keys through the trusted local fixture actor and exercises the actual managed gateway with two environments. Ten-second phases use one then four paced workers per environment. Temporary SQL tables, policies, Auth users and private buckets/objects are removed, keys revoked and the runtime stopped. It must run without other fixture mutators. [Measured results and limitations](../docs/engineering/SDK-LOAD.md).

## Retained recovery target

After the documented cutover rehearsal, `target_runtime.py up` and `target_runtime.py stop` manage the retained moved target under the operation lock. See `../docs/engineering/TARGET-LIFECYCLE.md`. Current staged mode requires the source stopped and refuses simultaneous startup in either direction. Normal `dev.py` now uses `installation_runtime.py` for combined source/target startup after bounded resource admission. See `../docs/engineering/COMBINED-RUNTIME.md`.

## Provisioning effect receipts

The supervisor now settles known receipts under its worker lock before runtime startup. Unknown outcomes block replay and startup; direct provision commands cannot bypass a pending worker receipt. Use `worker.py --upstream --settle-only` only to settle known durable outcomes, not to clear uncertainty. Read [the receipt protocol](../docs/engineering/PROVISIONING-RECEIPTS.md) before recovering interrupted provisioning. Do not remove pending receipts or blindly requeue their jobs. `worker-receipt-check.py` reuses the retained failed-capacity fixture to test real receipt settlement without allocating another environment.

## Read-only provisioning inspection

Run `/usr/bin/python3 lab/inspect-provisioning.py` to inspect pending upstream effects without starting services or settling receipts. The JSON report distinguishes missing, invalid, busy and unavailable evidence; it never authorizes replay. Read [inspection limits](../docs/engineering/PROVISIONING-INSPECTION.md). `/usr/bin/python3 lab/inspection-check.py` verifies the current stopped retained fixture and unchanged logical state hashes.


For the original Supabase PostgreSQL distribution, run `/usr/bin/python3 lab/partial-database-crash-check.py --upstream`. This uses a fresh network-disabled container and requires 4 GiB host headroom. It tests SQL boundaries only; `upstream-environments.py --storage` separately tests fresh Auth/REST/Storage integration. See [measured scopes](../docs/engineering/PARTIAL-DATABASE-CRASH.md).

The experimental same-database SQL revocation proof runs with `/usr/bin/python3 lab/partial-database-crash-check.py --upstream --sql-fence`. It does not enable production recovery. Read [the precise barrier scope](../docs/engineering/SQL-OPERATION-FENCE.md).

The sequential two-database prototype runs with `/usr/bin/python3 lab/partial-database-crash-check.py --upstream --cross-fence`. See [its evidence and limits](../docs/engineering/SQL-PAIR-REVOCATION.md). It does not enable production recovery.

Run `/usr/bin/python3 lab/fresh-worker-check.py` for the fresh real worker/SQL/Auth/REST/Storage lifecycle in a private namespaced source snapshot. It requires 6 GiB host headroom and removes only its isolated Docker resources. Read [scope and cleanup](../docs/engineering/FRESH-WORKER-LIFECYCLE.md).

Run `/usr/bin/python3 lab/partial-database-crash-check.py --upstream --hba` to verify complete-file HBA replacement under producer EOF and helper interruption. See [limits](../docs/engineering/ATOMIC-HBA-REPLACEMENT.md).

## One placement per Docker daemon

The component container names in this lab are installation-wide constants
(`sbarbase-durable-*` and `sbarbase-restore-*`), and a running container carries
the owner label of whoever started it. A second checkout, a worktree beside the
main tree, or a test run started while another placement is already up therefore
cannot start a placement independently: the names collide, and a container that
exists belongs to the state directory that started it. Start a placement from one
checkout at a time, and check `docker ps` after any run that does not stop its own
containers.
