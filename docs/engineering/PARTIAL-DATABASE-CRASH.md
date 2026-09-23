# Partial database provisioning experiment

Purpose: measure the durable state left by interruption between completed SQL phases of the actual `run.provision_environment` helper. This is an isolated stock PostgreSQL 17 component experiment, not a full Supabase installation crash or automatic reconciliation implementation.

## Basis and boundary

[PostgreSQL 17 CREATE DATABASE](https://www.postgresql.org/docs/17/sql-createdatabase.html) documents that database creation cannot run inside a transaction block. Roles, database creation and subsequent grants therefore do not form one atomic provisioning transaction. The experiment checks three existing helper checkpoints: roles, database and permissions, plus an injected failure in the permission transaction.

The [Supabase changelog](https://supabase.com/changelog) was checked on 2026-09-20. The self-hosted PostgreSQL 15 to 17 migration notice does not alter this pinned PostgreSQL 17 experiment; no upgrade is performed. Hosted credential-restore changes do not prove equivalent behavior for our self-hosted recovery.

## Containment

The probe uses the locally pinned stock PostgreSQL image with pull disabled, a unique container, no networking or published ports, 512 MiB memory and swap ceiling, 0.5 CPU, 96 PIDs and a 384 MiB temporary data filesystem. Host admission requires 3 GiB available memory. No retained volume is mounted and no retained catalog or credential file is modified.

Each phase receives a fresh temporary catalog, environment identity, claim, receipt and stage journal. The native child persists the database stage before SQL. Its checkpoint stops it only after synchronous SQL success; the parent verifies roles/database state before killing its owned unreaped child. Independent worker, effect and operation locks are acquired before asking the real settlement function to recover.

Cleanup verifies the captured exact container ID, image, name and owner label before removing it. A cidfile preserves cleanup identity if the Docker command returns ambiguously. OOM is a test failure, not a simulated provisioning interruption.

## Interpretation

A retained database-stage marker is deliberately not proof that any particular SQL statement finished. Concrete database observations describe partial state, but do not authorize replay. The expected behavior is a retained running claim and unchanged pending receipt, with automatic recovery blocked. A separate neighboring sentinel checks that the probe did not replace its data.

This does not test death during an in-flight query, Docker daemon failure, service or Storage recovery, arbitrary SQL scripts, hostile administrators or host power loss. Disposable data is removed at the end, so evidence is sanitized assertions, not a backup.

## Measured finding and implemented hardening

The initial 39-check experiment showed that interruption after plain CREATE DATABASE left the new database with its default PUBLIC CONNECT privilege before the later revoke. The durable runtime also has per-database HBA restrictions, so this is not evidence that a visitor crossed a retained environment boundary. It identifies an avoidable open database default during incomplete provisioning.

New database creation now specifies ALLOW_CONNECTIONS false. Revoking PUBLIC access, granting the intended roles and reopening occur in one transaction against postgres. Only the invocation that created the database may reopen it. An existing closed database is refused before any role or permission mutations, preserving deliberate source fences and uncertain partial state. An existing open database follows its existing reconciliation path without being treated as newly created.

This closes the new-database connection window; it does not isolate new LOGIN roles from every other database. HBA, role permissions and resource controls remain required.

## Verified evidence

[64 live disposable checks](evidence/partial-database-crash-checks.json) pass. Roles and database existence are observed before SIGKILL, then checked again under fresh ownership. Actual neighbor login receives the expected closed-database or CONNECT-denied error at the database and permissions checkpoints. The intended scoped role connects after permissions complete. The roles-only checkpoint proves database absence, not a failed login.

An injected division-by-zero before COMMIT rolls back grants and reopening: the database remains closed, PUBLIC privileges remain at their pre-transaction defaults, and added role membership is absent. Direct reentry into a closed partial database is refused. The real receipt settlement leaves receipt bytes, exact claim, attempt and running state unchanged, and does not create a new claim. Neighbor sentinel data remains unchanged. The exact disposable container is absent from a successful final inventory and did not OOM.

Three focused unit tests pass. Two fail against the previous committed bootstrap, confirming coverage of the changed behavior. Adversarial code review found no remaining must-fix in this bounded hardening. This does not implement automatic database-stage recovery.

The full Python suite passes 91 tests. A separate 13-check retained Supabase supervisor integration also passes after the bootstrap change: the four existing environments respond, the known admission refusal settles, and all owned runtimes stop. New database creation was exercised in the disposable stock PostgreSQL component; the retained integration restarts existing databases and does not prove a fresh full Supabase environment creation.

The historical `lab/retry-check.py` entry point now delegates to this isolated probe. Its former direct retry of a closed partial database is intentionally unsupported, and it no longer creates disposable databases inside the retained component cluster.

## Upstream distribution and fresh service validation

The probe now accepts `--upstream`. It uses the pinned Supabase PostgreSQL image and its original canonical roles, with a 1 GiB memory/swap cap, 1 CPU, a 512 MiB tmpfs and 4 GiB host headroom. The generated bootstrap password is passed through a child-only environment by variable name, never as an argument or persistent file. Local trust is configured only inside the verified network-disabled disposable container to isolate database ACL behavior from password authentication.

[65 upstream SQL checks](evidence/upstream-partial-database-crash-checks.json) pass, including bootstrap role verification, unprivileged neighbor membership checks and absence of each fresh fixture before provisioning. The component profile now has the same neighbor and freshness assertions. Both profiles record their exact pinned image ID and remove their exact disposable container.

Separately, `lab/upstream-environments.py --storage` passes [122 fresh integration checks](evidence/upstream-closed-bootstrap-checks.json) after the bootstrap change. It creates two environment databases on the original Supabase PostgreSQL image, runs original Auth migrations and REST services, and exercises shared Storage. Checks include signup/login, real auth.uid RLS behavior, forged-owner denial, cross-environment service credentials and token rejection, Storage access, and preservation of accounts/database identity on bootstrap retry. Its probe containers and network were removed. This does not cover Realtime, functions, upgrades or interrupted service recovery.

The next recovery barrier is under design in [database operation fencing](DATABASE-OPERATION-FENCING-DESIGN.md). Automatic replay after database mutation remains disabled.
