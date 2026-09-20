# Sbarbase: start here

Updated 2026-09-20. Workspace: `/home/sbarah/R/Projects/P/sbarbase`.

## Product and current decision

Downloadable open source Supabase-based administration for multiple projects, usually on one VPS. Preserve Supabase SDK/SQL compatibility and use its design system for the future UI. Operators are trusted; application visitors are not. A loopback API server exists for the durable experiment. No production platform, complete installer or UI exists yet.

**Continue implementation and verification; production architecture is not approved.** Hierarchy: installation > organization > project > environment. Ownership is independent of server placement. Candidate: shared PostgreSQL, separate environment databases and service credentials, original Auth/REST per environment and shared Storage. Shared roles/processes/resources remain failure boundaries. Independent PostgreSQL is the fallback if compatibility, isolation or recovery gates fail. No fixed project capacity, distribution license or upstream adoption has been chosen.

## Current implementation and evidence

| Area | What works | Evidence and limits |
|---|---|---|
| Control plane | Owner/admin/viewer policy, dedicated management Auth realm, durable publishable keys and composed loopback API | [28 upstream management checks](docs/evidence/upstream-management-checks.json), [scope](docs/reviews/upstream-management.md). Production onboarding, invitations, full audit and edge controls remain pending |
| Provisioning | Atomic environment/job creation, exclusive local worker, stable runtime identity and interrupted-operation reconciliation | [7 live checks](docs/evidence/provision-checks.json), [scope](docs/PROVISIONING.md). Default worker retains the stock fixture; `--upstream` selects an isolated durable Supabase runtime |
| Stock component lab | Four environments, Auth/RLS/credential/token isolation, SDK CRUD and key revocation | [97 component](docs/evidence/four-environment-component-checks.json), [44 SDK](docs/evidence/four-environment-sdk-checks.json). Minimal auth.uid fixture, not full upstream bootstrap |
| Upstream database | Original Supabase PostgreSQL, real Auth migrations/helpers, scoped ownership and restart/retry preservation | [40 checks](docs/evidence/upstream-environment-checks.json), [bootstrap findings](docs/reviews/distribution-bootstrap.md). Full component bootstrap and upgrades remain open |
| Durable upstream lifecycle | Original Supabase PostgreSQL and one shared Storage process, separate databases/Auth/REST, persistent worker and volumes | [25 live checks](docs/evidence/durable-upstream-checks.json), [scope](docs/reviews/durable-runtime.md). Container recreation preserves Auth, SQL rows, objects and earlier signed URLs; same host only |
| Shared Storage | One process, separate tenant logins, private files/RLS, SDK gateway operations, trusted tenant headers and public/signed URLs | [122 combined checks](docs/evidence/storage-recovery-checks.json), [details](docs/reviews/shared-storage.md). Includes the 40 upstream checks, not additional independent coverage |
| Recovery | Encrypted logical database plus file/xattr restore, unchanged source/neighbor, restored private download | [11 added checks](docs/evidence/storage-recovery-checks.json), [scope](docs/reviews/storage-recovery.md). Quiescent fixture, same cluster, no off-host/PITR guarantee |

Retained component containers now reject image or configured-environment drift before reuse: [12 read-only live checks](docs/evidence/runtime-reuse-checks.json) and six Python tests. This guard does not perform upgrades or container reconciliation.

Twenty-seven TypeScript unit tests pass with 141 assertions. Evidence files are versioned snapshots, not cumulative independent test totals.

The gateway now also permits API-key-free GET/HEAD on public-object and signed-download paths only. Storage enforces bucket visibility and signature validity. API-key revocation does not revoke previously issued signed URLs. Uploads currently buffer at most 1 MiB with a read deadline. Large/resumable uploads, CORS and service-key forwarding remain unfinished.

Local SQLite stores experimental control metadata and hashed API keys; application data stays in PostgreSQL. A shared Storage process can access all tenant configurations, so process compromise remains a shared boundary. No complete organization/server transfer or multi-host control plane exists.

## Next gates

1. Harden the new durable upstream lifecycle: verify full migration readiness, configuration reconciliation, pinned-version upgrades, failure injection and full-install fault recovery. Dedicated management Auth/key integration now passes locally. It currently uses an isolated experimental catalog and local worker.
2. Complete production management deployment, operator bootstrap, invitations, key rotation/auditing, admission controls, CORS/OAuth and streaming uploads. Integrate Realtime, pooler, functions and scheduled jobs with isolation tests.
3. Prove encrypted off-host recovery of databases, objects, secrets/configuration and function artifacts. Implement ownership transfer and server cutover; rollback after destination writes requires reconciliation.
4. Benchmark peak workloads and noisy neighbors, then 10 environments when resources permit. Admission must account for CPU, RAM, I/O, connections, disk and recovery headroom. Daily visitors do not establish 10/100-project capacity.
5. Build the Supabase-inspired administration UI and distributable installer against verified APIs.

## Research and saved diagrams

[Decisions and alternatives](docs/DECISIONS.md), [architecture review](docs/ARCHITECTURE-REVIEW.md), independent [feasibility](docs/reviews/supabase-feasibility.md), [security](docs/reviews/security-operations.md), [alternative products](docs/reviews/alternatives-product.md), [capacity method](docs/reviews/capacity-method.md). Detailed implementation: [control plane](docs/CONTROL-PLANE.md), [provisioning](docs/PROVISIONING.md). Earlier [session record](docs/SESSION-RECORD.md) is historical; this page takes precedence.

Saved images: [10 projects](docs/diagrams/ten-projects.png), [transfer/restore](docs/diagrams/move-and-restore.png), [assumptions](docs/diagrams/README.md). Their 10-project cap and two-server layout are illustrative, not measured limits or implemented features.

## Continue safely in Codex or Hermes

Use this repository and read lab/README.md before executing probes. Use bun and /usr/bin/python3. Recheck live resources and git status; preserve unrelated services. Proposed lab budget: 4 GB RAM, 4 CPUs, 20-30 GB disk. Persistent component lab ceilings are 3072 MiB/3 CPUs; the upstream Storage probe uses 2560 MiB/2.5 CPUs and removes its resources. Historical idle memory is not a capacity forecast. The component and durable upstream labs are stopped between runs with volumes retained. The durable upstream experiment uses 2816 MiB/2.75 CPUs at two environments including management Auth; its four-environment guard caps container limits at 3840 MiB/3.75 CPUs, not a measured capacity guarantee. Do not run these labs concurrently without rechecking aggregate resources.

Keep .secrets, .lab and dependencies out of sharing. The handoff ZIP includes source, research, pictures and sanitized evidence, not credentials, runtime data or Git history. Verify pinned image availability on another host. No remote server was changed and no Hermes execution was dispatched. Avoid concurrent mutation of one checkout by different assistants.
