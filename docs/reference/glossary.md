[العربية](glossary.ar.md)

# Glossary

Terms used in the code, the engineering notes and the evidence files, in plain words. User-facing pages say "client" and "your server"; the code says `organization` and `installation`.

## Hierarchy and placement

- **Installation.** The code's name for your server: one Sbarbase checkout with its catalog, runtime and state.
- **Organization.** The code's name for a client. It owns projects and has members with roles.
- **Project.** A client's app. It groups environments and holds no data of its own.
- **Environment.** One deployable copy of a project, such as production or staging, with its own database, logins, keys, Auth and REST. The unit of isolation, move and restore.
- **Runtime.** The internal identifier of an environment's running services, such as `e_1f06...`. Routes and state files are keyed by it.
- **Placement.** Where an environment's services actually run. Stored separately from ownership so an environment can move without changing its owner.
- **Source / target.** In a move or restore, the source is the engine the environment came from and the target is the fresh engine it is restored into.
- **Routing record.** The catalog row that says where requests for a runtime go, whether it is in maintenance, and its revision number.
- **Maintenance.** A flag on the routing record. While set, the gateway answers `503` instead of forwarding.
- **Revision.** A counter on the routing record. A change must name the revision it expects, so a stale caller cannot overwrite a newer decision.
- **Management realm.** The dedicated Auth instance that operators log in to. It is never an application environment's Auth.
- **Catalog.** The local SQLite database of clients, projects, environments, memberships, jobs and routing. Application data is never stored there.

## Credentials and access

- **Publishable key.** The API key an app sends in the `apikey` header. Only its hash is stored; the raw key is shown once.
- **Scoped login.** A PostgreSQL login that belongs to one environment and one service (Auth, REST or Storage).
- **Canonical roles.** The Supabase API roles `anon`, `authenticated` and `service_role`. They exist once per PostgreSQL engine and are shared by name.
- **HBA.** PostgreSQL's host-based authentication file (`pg_hba.conf`): the rules that say which login may connect to which database from where. Sbarbase writes exact login and database pairs.
- **HBA authority.** The protocol that decides which process may change the HBA file, so two writers cannot overwrite each other.
- **Tenant.** An environment as seen by the shared Storage process, with its own configuration and signing keys.

## Provisioning and crash safety

- **Job, claim, attempt.** A queued piece of provisioning work; the worker's claim on it; and the numbered try. Records name all three so a stale try cannot be confused with a newer one.
- **Worker lock.** An exclusive file lock held by the one provisioning worker allowed to run.
- **Operation lock.** A lock that serializes lifecycle operations (start, stop, export, migration) on the runtime.
- **Effect.** Any change outside the catalog: a container, a database, a login, an HBA rule.
- **Effect receipt.** A small durable file written before an effect starts, naming the exact job claim and attempt. A completed receipt is settled on restart; a pending one blocks startup and replay.
- **Settle.** Record a receipt's known outcome in the catalog, then remove the receipt.
- **Guardian.** A supervising process that runs an effect with a deadline and cleans up its process group if the worker dies.
- **Witness.** A durable file the provisioner writes when it finishes, so a lost acknowledgment can be recovered without running the work again.
- **Effect lease.** A fresh lock each worker invocation holds for its lifetime, so a replacement worker cannot act while an old effect is still running.
- **Preflight.** The checks before any external change. An interruption proven to be in preflight can be requeued automatically.
- **Stage protocol.** Per-operation markers (preflight, database, services, storage, publication) that record how far an effect got.
- **Journal.** An append-only or atomically replaced record of an operation's intent and progress, written before acting and used to resume or reconcile.
- **Fence.** A barrier that stops an old or stale actor from writing. The source fence closes a database; the SQL operation fence and HBA fence reject a revoked operation token.
- **Operation token.** A random identifier bound to one operation. Writes carry it; once revoked, it can never write again.
- **Tombstone.** The permanent record that a token was revoked. It is never deleted, so a revoked operation cannot come back.
- **Generation.** One specific managed database container, identified by its exact container ID and image.
- **Generation pin.** The stored identity of the current generation. Startup refuses if the live container does not match it.
- **Generation migration.** The journaled, crash-tested procedure (`lab/migrate-generation.py`) that replaces a database container on the same data volume.
- **Retained.** Containers, volumes and state kept between runs on purpose. Retained data is never deleted or recreated to get past a refusal.
- **Adoption.** Bringing an older retained database under the current HBA authority and generation pin, explicitly, once.
- **Reconcile.** An explicit operator step that inspects actual state after an interruption and records a safe result.
- **Unknown outcome.** An effect that may or may not have happened. It blocks replay until reconciled.

## Resources and load

- **Resource tier.** A policy row giving a container its CPU weight, CPU limit, memory and per-device IO limits.
- **Admission.** The checks before a new environment or placement starts: memory, disk, inodes, pressure and connection budget. A refusal starts nothing.
- **Combined admission.** Admission for starting source and target placements together, with a host reserve.
- **Pressure.** Linux cgroup pressure stall information: how long tasks waited for CPU, IO or memory. High pressure refuses new environments.
- **Connection budget.** The PostgreSQL connections reserved per service login, which must fit under the engine's limit with room for operators.
- **Gateway admission.** The in-process limit on concurrent requests per environment and in total. No queue: excess requests get `429` or `503`.
- **Drain.** Waiting for requests already admitted by a gateway process to finish.
- **Pause lease.** An in-process handle that pauses one environment in one gateway process and can wait for its drain. It disappears on restart.
- **Deadline.** A time limit on a request, response stream or SQL statement, after which it is failed rather than left hanging.
- **Noisy neighbour.** One environment's load slowing another on the shared engine or host.

## Recovery

- **Export.** An encrypted bundle of one environment: database dump, logins, grants, settings, files and signing keys.
- **Quiescence.** The state where no client session, prepared transaction or background job can change the data being exported.
- **Cutover.** Moving an environment's traffic from source to target after a verified restore.
- **Recovery target.** The fresh, separate PostgreSQL engine an environment is restored into.

## Project process

- **Pin / lock file.** An exact image tag and digest recorded in `lab/*.lock.json`.
- **Evidence.** A JSON file in `docs/evidence/` written by a live probe, recording each check and its result.
- **Probe / fixture.** A live test script (`lab/*-check.*`) and the retained test environments it runs against.
- **Checkpoint.** A dated entry in the engineering notes recording what was proven at that time.
