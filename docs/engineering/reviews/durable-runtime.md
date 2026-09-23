# Durable upstream runtime experiment

Verified 2026-09-20. This is an implementation gate, not a production installer.

## What changed

`lab/durable_runtime.py` preserves the pinned Supabase PostgreSQL data and shared
Storage objects in two owned Docker volumes. Organizations, projects and queued
environment operations use an isolated local catalog. `worker.py --upstream`
selects this catalog and provisions original Auth/REST per environment plus a
tenant in the one shared Storage process. The old component lab is retained
without migrating or overwriting its data.

Credentials are generated once, stored privately with atomic replacement and
fsync, and reused during reconciliation. Every environment has its own database,
Auth/REST/Storage login and JWT signing secret. HBA grants each service login
access only to its corresponding database; Storage control metadata has a
separate login and database. Runtime identities remain independent of ownership.
The existing shared PostgreSQL role and shared-process trust boundaries remain.

Storage tenant creation is skipped when already registered, preserving its
signing keys. Readiness requires Auth and REST health, the Storage bucket API,
and Auth/Storage schema presence before endpoints are persisted. Registration
status alone is insufficient. This is not a complete pending-migration check
and does not reconcile a drifted tenant configuration or repair failed migrations.

The runtime checks resource ownership, retained image/configuration agreement,
required mounted volume identities and network membership. It does not implicitly
upgrade images or replace a drifted container. Network, command, privilege and
resource configuration verification is not comprehensive yet. The runtime is
internal and unpublished, with lab-only email auto-confirmation and internal
service tokens. Do not expose it as a production service.

## Evidence

[25 live lifecycle checks](../../evidence/durable-upstream-checks.json) cover two
environments with original Supabase SDK access through a loopback gateway:

- Worker completion and separate identities for the same email.
- Application RLS inserts and private Storage uploads.
- Stopping and removing only owned runtime containers, retaining named volumes
  and credentials, then recreating those containers.
- Preserved database OIDs, Auth identities/password login, SQL rows and exact
  private object bytes.
- Previously issued signed URLs still functioning after Storage recreation.
- Neighbor environment JWTs still rejected after recreation.

The initial provisioning path also reached success after a simulated lost
completion, before the first probe failed in its gateway harness. The harness
passed a callback where `createGateway` requires a map; that defect was fixed.
The saved successful rerun reuses its two environments, so its 25 checks do not
independently establish fresh-install or interruption coverage. Expand fault
injection and fresh-install evidence before release.

At this lifecycle checkpoint, six Python runtime-reuse tests and 25 TypeScript tests (128 assertions) passed.
The new probe uses a fixture management actor and temporary gateway API keys.
It does not yet prove dedicated management Auth plus durable key issuance on
this upstream profile. That integration has since passed in the separate [dedicated management review](upstream-management.md).

## Resource and recovery limits

This initial lifecycle checkpoint used container ceilings of 2560 MiB and 2.5 CPUs for two environments. The subsequent dedicated management Auth process adds 256 MiB and 0.25 CPU. The experiment
refuses a fifth environment; including management Auth, four environments are capped at 3840 MiB and 3.75 CPUs.
These are conservative local guardrails, not a public project capacity limit.
The runtime checks available host memory before startup. Admission still needs
ongoing memory, disk, I/O, connection and recovery-headroom accounting.

Recreating containers with intact volumes is not a backup. Host loss, volume
corruption, remote restoration, old/new version upgrades and live-write cutover
remain unverified. Earlier encrypted same-cluster recovery is documented
separately. Keeping Storage metadata preserves its signing keys here; encrypted
backup of that metadata and its decryption secret remains required for full
disaster recovery.

## Sources

Implementation follows the previously inspected pinned PostgreSQL image and
Storage v1.73.1 tenant route implementation. See
[shared Storage findings](shared-storage.md) and
[upstream bootstrap findings](distribution-bootstrap.md). Official operational
context: [Supabase self-hosting](https://supabase.com/docs/guides/self-hosting/docker).
The current changelog was reviewed; no image versions were changed in this step.
