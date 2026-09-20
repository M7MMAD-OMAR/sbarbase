# Sbarbase: start here

Updated 2026-09-20. Workspace: `/home/sbarah/R/Projects/P/sbarbase`.

Short continuation brief: [handoff](docs/HANDOFF.md), including saved visuals, rationale and supervisor verification limits.

## Product and current decision

Downloadable open source Supabase-based administration for multiple projects, usually on one VPS. Preserve Supabase SDK/SQL compatibility and follow its design system for the administration UI. Operators are trusted; application visitors are not. A working local console now runs on the loopback API server. No production platform or complete installer exists yet.

**Continue implementation and verification; production architecture is not approved.** Hierarchy: installation > organization > project > environment. Ownership is independent of server placement. Candidate: shared PostgreSQL, separate environment databases and service credentials, original Auth/REST per environment and shared Storage. Shared roles/processes/resources remain failure boundaries. Independent PostgreSQL is the fallback if compatibility, isolation or recovery gates fail. No fixed project capacity, distribution license or upstream adoption has been chosen.

## Current implementation and evidence

| Area | What works | Evidence and limits |
|---|---|---|
| Console | Login, organization/project discovery, creation, provisioning status, connection details and key management | [Real browser workflow and saved screenshots](docs/design/CONSOLE-QA.md). Local only, foreground supervised worker, no health/capacity/backup UI yet |
| Control plane | Owner/admin/viewer policy, dedicated management Auth realm, durable publishable keys and composed loopback API | [28 upstream management checks](docs/evidence/upstream-management-checks.json), [scope](docs/reviews/upstream-management.md). Local operator bootstrap and organization discovery now work; production onboarding, invitations, full audit and edge controls remain pending |
| Initial operator | Private local setup, persisted intent, interrupted Auth/catalog recovery and authenticated organization discovery | [18 live checks](docs/evidence/bootstrap-checks.json), [usage and scope](docs/OPERATOR-SETUP.md). No real operator account retained; no public bootstrap endpoint |
| Provisioning | Atomic environment/job creation, exclusive local worker, stable runtime identity and interrupted-operation reconciliation | [7 live checks](docs/evidence/provision-checks.json), [scope](docs/PROVISIONING.md). Default worker retains the stock fixture; `--upstream` selects an isolated durable Supabase runtime |
| Stock component lab | Four environments, Auth/RLS/credential/token isolation, SDK CRUD and key revocation | [97 component](docs/evidence/four-environment-component-checks.json), [44 SDK](docs/evidence/four-environment-sdk-checks.json). Minimal auth.uid fixture, not full upstream bootstrap |
| Upstream database | Original Supabase PostgreSQL, real Auth migrations/helpers, scoped ownership and restart/retry preservation | [40 checks](docs/evidence/upstream-environment-checks.json), [bootstrap findings](docs/reviews/distribution-bootstrap.md). Full component bootstrap and upgrades remain open |
| Durable upstream lifecycle | Original Supabase PostgreSQL and one shared Storage process, separate databases/Auth/REST, persistent worker and volumes | [25 live checks](docs/evidence/durable-upstream-checks.json), [scope](docs/reviews/durable-runtime.md). Container recreation preserves Auth, SQL rows, objects and earlier signed URLs; same host only |
| Shared Storage | One process, separate tenant logins, private files/RLS, SDK gateway operations, trusted tenant headers and public/signed URLs | [122 combined checks](docs/evidence/storage-recovery-checks.json), [details](docs/reviews/shared-storage.md). Includes the 40 upstream checks, not additional independent coverage |
| Recovery | Encrypted logical database plus file/xattr restore, unchanged source/neighbor, restored private download | [11 added checks](docs/evidence/storage-recovery-checks.json), [scope](docs/reviews/storage-recovery.md). Quiescent fixture, same cluster, no off-host/PITR guarantee |

Retained component containers now reject image or configured-environment drift before reuse: [12 read-only live checks](docs/evidence/runtime-reuse-checks.json) and six Python tests. This guard does not perform upgrades or container reconciliation.

Thirty-six Bun tests pass with 205 assertions; UI typecheck and production build also pass. Evidence files are versioned snapshots, not cumulative independent test totals.

The gateway now also permits API-key-free GET/HEAD on public-object and signed-download paths only. Storage enforces bucket visibility and signature validity. API-key revocation does not revoke previously issued signed URLs. Uploads currently buffer at most 1 MiB with a read deadline. Large/resumable uploads, CORS and service-key forwarding remain unfinished.

Local SQLite stores experimental control metadata and hashed API keys; application data stays in PostgreSQL. A shared Storage process can access all tenant configurations, so process compromise remains a shared boundary. No complete organization/server transfer or multi-host control plane exists.

## Next gates

1. Harden the new durable upstream lifecycle: verify full migration readiness, configuration reconciliation, pinned-version upgrades, failure injection and full-install fault recovery. Dedicated management Auth/key integration now passes locally. It currently uses an isolated experimental catalog and local worker.
2. Complete production management deployment, operator onboarding UX, invitations, key rotation/auditing, admission controls, CORS/OAuth and streaming uploads. Integrate Realtime, pooler, functions and scheduled jobs with isolation tests.
3. Prove encrypted off-host recovery of databases, objects, secrets/configuration and function artifacts. Implement ownership transfer and server cutover; rollback after destination writes requires reconciliation.
4. Benchmark peak workloads and noisy neighbors, then 10 environments when resources permit. Admission must account for CPU, RAM, I/O, connections, disk and recovery headroom. Daily visitors do not establish 10/100-project capacity.
5. Extend the working local console with actual operations, complete design-system integration and accessible onboarding. Build a distributable installer/supervisor against verified APIs.

## Research and saved diagrams

[Decisions and alternatives](docs/DECISIONS.md), [architecture review](docs/ARCHITECTURE-REVIEW.md), independent [feasibility](docs/reviews/supabase-feasibility.md), [security](docs/reviews/security-operations.md), [alternative products](docs/reviews/alternatives-product.md), [capacity method](docs/reviews/capacity-method.md). Detailed implementation: [control plane](docs/CONTROL-PLANE.md), [provisioning](docs/PROVISIONING.md). Earlier [session record](docs/SESSION-RECORD.md) is historical; this page takes precedence.

Saved images: [10 projects](docs/diagrams/ten-projects.png), [transfer/restore](docs/diagrams/move-and-restore.png), [assumptions](docs/diagrams/README.md). Their 10-project cap and two-server layout are illustrative, not measured limits or implemented features.

## Continue safely in Codex or Hermes

Use this repository and read lab/README.md before executing probes. Use bun and /usr/bin/python3. Recheck live resources and git status; preserve unrelated services. Proposed lab budget: 4 GB RAM, 4 CPUs, 20-30 GB disk. Persistent component lab ceilings are 3072 MiB/3 CPUs; the upstream Storage probe uses 2560 MiB/2.5 CPUs and removes its resources. Historical idle memory is not a capacity forecast. The component and durable upstream labs are stopped between runs with volumes retained. The durable upstream experiment uses 2816 MiB/2.75 CPUs at two environments including management Auth. The UI and supervisor probes now retain four environments, whose container ceilings total 3840 MiB/3.75 CPUs. The experimental admission guard is full; this is not a measured capacity guarantee. Do not run these labs concurrently without rechecking aggregate resources.

Keep .secrets, .lab and dependencies out of sharing. The handoff ZIP includes source, research, pictures and sanitized evidence, not credentials, runtime data or Git history. Verify pinned image availability on another host. No remote server was changed and no Hermes execution was dispatched. Avoid concurrent mutation of one checkout by different assistants.

## Local runner checkpoint

`/usr/bin/python3 lab/dev.py` builds the console, starts the owned runtime and continuously processes provisioning jobs. Ctrl+C stops its children and owned containers while preserving volumes. Five lifecycle unit tests and [eight live smoke checks](docs/evidence/supervisor-smoke-checks.json) pass. Worker locks remain reserved across restarts; repeated failures stop the runner. Forced idle-worker restart and [one in-flight provisioning interruption](docs/evidence/supervisor-recovery-checks.json) now pass. The interrupted job was reclaimed and completed with the same runtime identity and one catalog operation. Further crash points and full installation recovery remain open. This foreground runner is not a production service manager. Other local Docker services remained running after the smoke check.

## Admission checkpoint

The four-environment local guard now has [12 live checks](docs/evidence/admission-checks.json): a fifth runtime is refused without new databases, containers or endpoint entries; existing Auth/REST endpoints, console and worker remain available. Failed metadata is retained for explicit retry. The worker stores only `capacity_exceeded` or `runtime_failed`, and the authorized status API exposes that safe reason. The console labels capacity refusals and explains the next action. Thirteen Python tests pass, including guard ordering and reconciliation of existing environments at capacity. This count guard is not production CPU, memory, disk or workload admission.

## Resource admission checkpoint

New durable environments now also require host-memory and mounted-volume free-space headroom before credentials or databases are allocated. [Policy, live snapshot and limitations](docs/RESOURCE-ADMISSION.md). Eighteen Python tests pass. This is a local snapshot gate with fixed reserves; CPU/I/O, quotas and continuous pressure monitoring remain unfinished; connection budgeting is described below. The four-environment count guard remains in force.

## Connection budget checkpoint

Durable service logins and databases now enforce finite PostgreSQL connection limits. New environment admission accounts for planned service connections and operational headroom against actual PostgreSQL settings. [Policy, seven live saturation checks and limits](docs/CONNECTION-BUDGET.md). A neighboring SQL login and the operator remained available while one Auth login was saturated. Twenty-two Python tests pass. This does not isolate query CPU, memory or I/O.

## Pressure and initial load checkpoint

New allocations now check cgroup CPU, I/O and memory pressure for the owned database and Storage containers. [Policy and snapshot](docs/PRESSURE-ADMISSION.md). Twenty-seven Python tests pass. An [initial neighboring-environment SQL probe](docs/NOISY-NEIGHBOR.md) validated 150 results across baseline, an eight-second neighboring CPU workload and recovery. Its small latency difference is not production capacity evidence. Continuous overload response, HTTP workload benchmarks and sustained write/I/O tests remain open.

## Managed SDK load checkpoint

[Two-environment SDK workload](docs/SDK-LOAD.md) completed 1,010 verified operations without errors across two ten-second paced phases, at nominal aggregate rates of 20 and 80 operations/second. Reads, inserts, identity checks and private uploads/downloads used the managed gateway. Fixture resources were cleaned up, keys revoked and runtime stopped. A preceding 200-operation burst is separately retained. These short local runs do not establish production capacity or SLOs. Next: bounded request admission and overload response, then longer open-loop and recovery tests.

## Documentation handoff checkpoint

The [compact handoff](docs/HANDOFF.md) indexes decisions, research and saved diagrams. The [resume checkpoint](docs/RESUME-CHECKPOINT.md) records unfinished gateway/HTTP changes, the unsupported Bun connection-counting call, evidence boundaries and exact next steps. Read it before treating older next-step paragraphs or evidence as current.

## Gateway overload checkpoint

[Application request admission and streaming](docs/GATEWAY-OVERLOAD.md) now pass 44 unit tests, 22 real HTTP checks and a 1,000-operation SDK regression without failures. An earlier 8-check actual Supabase saturation probe verifies neighbor correctness and recovery. The unsupported connection-counting call recorded in the handoff is fixed. Shared resource containment, sustained open-loop capacity and production recovery remain open.

## Pre-header deadline checkpoint

The application gate now bounds pre-header waiting independently of transport cooperation. It returns 504 on expiry, releases admission, signals cancellation and cancels late response bodies. Client abort releases the slot with 408. Forty-six unit tests with 248 assertions and 24 HTTP checks pass; strict types pass for the adapter, handler and gate. This bounds gateway waiting, not arbitrary upstream work that ignores cancellation. The earlier SDK regression predates this additional deadline change.

## Sustained arrival failure checkpoint

The [30-second open-loop slow-RPC probe](docs/SUSTAINED-OVERLOAD.md) exposed two target client timeouts among 600 offered target requests. It also observed 549 expected rejections, 49 correct target results and 60 correct neighbor results. Cleanup completed. This is failed acceptance, not capacity certification; next investigate REST queueing and cancellation, then coordinate service admission with connection budgets.
