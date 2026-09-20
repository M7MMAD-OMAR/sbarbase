# Sbarbase: start here

Updated 2026-09-20. Workspace: `/home/sbarah/R/Projects/P/sbarbase`.

## Product and current decision

Downloadable open source Supabase-based administration for multiple projects, usually on one VPS. Preserve Supabase SDK/SQL compatibility and use its design system for the future UI. Operators are trusted; application visitors are not. No production platform, complete installer or UI exists yet.

**Continue implementation and verification; production architecture is not approved.** Hierarchy: installation > organization > project > environment. Ownership is independent of server placement. Candidate: shared PostgreSQL, separate environment databases and service credentials, original Auth/REST per environment and shared Storage. Shared roles/processes/resources remain failure boundaries. Independent PostgreSQL is the fallback if compatibility, isolation or recovery gates fail. No fixed project capacity, distribution license or upstream adoption has been chosen.

## Current implementation and evidence

| Area | What works | Evidence and limits |
|---|---|---|
| Control plane | Owner/admin/viewer policy, durable metadata, scoped getUser authentication and publishable-key management | [Management](docs/evidence/management-checks.json), [key connection](docs/evidence/connection-checks.json). Dedicated management deployment and full audit remain pending |
| Provisioning | Atomic environment/job creation, exclusive local worker, stable runtime identity and interrupted-operation reconciliation | [7 live checks](docs/evidence/provision-checks.json), [scope](docs/PROVISIONING.md). Worker still uses stock PostgreSQL, Auth/REST only |
| Stock component lab | Four environments, Auth/RLS/credential/token isolation, SDK CRUD and key revocation | [97 component](docs/evidence/four-environment-component-checks.json), [44 SDK](docs/evidence/four-environment-sdk-checks.json). Minimal auth.uid fixture, not full upstream bootstrap |
| Upstream database | Original Supabase PostgreSQL, real Auth migrations/helpers, scoped ownership and restart/retry preservation | [40 checks](docs/evidence/upstream-environment-checks.json), [bootstrap findings](docs/reviews/distribution-bootstrap.md). Full component bootstrap and upgrades remain open |
| Shared Storage | One process, separate tenant logins, private files/RLS, SDK gateway operations, trusted tenant headers and public/signed URLs | [122 combined checks](docs/evidence/storage-recovery-checks.json), [details](docs/reviews/shared-storage.md). Includes the 40 upstream checks, not additional independent coverage |
| Recovery | Encrypted logical database plus file/xattr restore, unchanged source/neighbor, restored private download | [11 added checks](docs/evidence/storage-recovery-checks.json), [scope](docs/reviews/storage-recovery.md). Quiescent fixture, same cluster, no off-host/PITR guarantee |

Twenty-five unit tests pass with 128 assertions. Evidence files are versioned snapshots, not cumulative independent test totals.

The gateway now also permits API-key-free GET/HEAD on public-object and signed-download paths only. Storage enforces bucket visibility and signature validity. API-key revocation does not revoke previously issued signed URLs. Uploads currently buffer at most 1 MiB with a read deadline. Large/resumable uploads, CORS and service-key forwarding remain unfinished.

Local SQLite stores experimental control metadata and hashed API keys; application data stays in PostgreSQL. A shared Storage process can access all tenant configurations, so process compromise remains a shared boundary. No complete organization/server transfer or multi-host control plane exists.

## Next gates

1. Integrate the upstream database and shared Storage path into the durable lifecycle; verify migration readiness, pinned-version upgrades and failure recovery.
2. Complete management deployment, invitations, key rotation/auditing, admission controls, CORS/OAuth and streaming uploads. Integrate Realtime, pooler, functions and scheduled jobs with isolation tests.
3. Prove encrypted off-host recovery of databases, objects, secrets/configuration and function artifacts. Implement ownership transfer and server cutover; rollback after destination writes requires reconciliation.
4. Benchmark peak workloads and noisy neighbors, then 10 environments when resources permit. Admission must account for CPU, RAM, I/O, connections, disk and recovery headroom. Daily visitors do not establish 10/100-project capacity.
5. Build the Supabase-inspired administration UI and distributable installer against verified APIs.

## Research and saved diagrams

[Decisions and alternatives](docs/DECISIONS.md), [architecture review](docs/ARCHITECTURE-REVIEW.md), independent [feasibility](docs/reviews/supabase-feasibility.md), [security](docs/reviews/security-operations.md), [alternative products](docs/reviews/alternatives-product.md), [capacity method](docs/reviews/capacity-method.md). Detailed implementation: [control plane](docs/CONTROL-PLANE.md), [provisioning](docs/PROVISIONING.md). Earlier [session record](docs/SESSION-RECORD.md) is historical; this page takes precedence.

Saved images: [10 projects](docs/diagrams/ten-projects.png), [transfer/restore](docs/diagrams/move-and-restore.png), [assumptions](docs/diagrams/README.md). Their 10-project cap and two-server layout are illustrative, not measured limits or implemented features.

## Continue safely in Codex or Hermes

Use this repository and read lab/README.md before executing probes. Use bun and /usr/bin/python3. Recheck live resources and git status; preserve unrelated services. Proposed lab budget: 4 GB RAM, 4 CPUs, 20-30 GB disk. Persistent component lab ceilings are 3072 MiB/3 CPUs; the upstream Storage probe uses 2560 MiB/2.5 CPUs and removes its resources. Historical idle memory is not a capacity forecast. The component lab is stopped between runs with volumes retained.

Keep .secrets, .lab and dependencies out of sharing. The handoff ZIP includes source, research, pictures and sanitized evidence, not credentials, runtime data or Git history. Verify pinned image availability on another host. No remote server was changed and no Hermes execution was dispatched. Avoid concurrent mutation of one checkout by different assistants.
