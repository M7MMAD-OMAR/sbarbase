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

The latest recorded backend checks are 52 Bun tests with 269 assertions and 34 Python tests. UI typecheck and production build passed at the earlier UI checkpoint. Evidence files are versioned snapshots, not cumulative independent test totals.

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

## REST service budget checkpoint

The durable installer now publishes a REST admission cap from its configured PostgREST pool, currently 3. Service/environment/process acquisition is atomic and shared across managed factories. Two repeated 30-second arrival probes passed after the change, with the original failed evidence retained. The latest run has 14 checks including explicit budget verification and post-load recovery. Forty-nine Bun tests, 261 assertions, 27 Python tests, 24 HTTP checks and gateway strict types pass. See [comparison and limits](docs/SUSTAINED-OVERLOAD.md). Mixed SDK traffic under the new cap and actual upstream cancellation remain unverified.

## REST disconnect checkpoint

[SQL observation and retained admission](docs/REST-CANCELLATION.md) expose and mitigate a client-abort gap. Three cancelled HTTP requests leave three SQL RPCs active; the gateway now retains all admission counters and rejects a fourth until upstream completion/draining. Fifteen live checks and 52 unit tests with 269 assertions pass. This is not active SQL cancellation; timeout/transport failures can still release slots ahead of SQL completion.

## REST SQL deadline checkpoint

[Per-login, per-database REST defaults](docs/SQL-DEADLINES.md) now set statement_timeout=8s and transaction_timeout=12s. Fourteen live checks verify actual service-role settings, SQL error after eight seconds, transaction termination near twelve seconds despite a statement override, no surviving fixture SQL and successful reconnect/recovery. Thirty Python tests pass. Warm setting changes refuse before modifying defaults unless REST is stopped. Mixed SDK traffic under the combined policies remains next.

## Combined-policy SDK checkpoint

The [same mixed SDK workload](docs/SDK-LOAD.md) now passes with service admission, REST disconnect retention and SQL defaults active: 1,001 correct operations, no failed operations, cleanup and runtime stop completed. This remains two short closed-loop phases, not a capacity forecast. Next major recovery gate: restore onto a separate PostgreSQL cluster while preserving identity and objects, then verify source/neighbor isolation within the local resource budget.

## Independent recovery export checkpoint

A [selected-environment encrypted export](docs/RECOVERY-EXPORT.md) now includes scoped roles/settings, objects/xattrs and decrypted tenant signing keys enclosed under a new backup key. Twenty-nine live checks pass and the owned source is stopped with volumes retained. No shared platform key is intentionally added to configuration. Thirty-four Python tests pass. The next step remains a fresh-cluster restore and independent identity/object/signed-URL verification; archive round-trip is not restore certification.

## Documentation checkpoint for continuation

[Current resume state](docs/RESUME-CHECKPOINT.md) supersedes older next-step text. The latest export includes ICU locale metadata and table hashes. The independent database restore consumer is an uncommitted draft and failed resource preflight before target creation because the source is intentionally stopped. No independent restore success is claimed. Saved diagrams, research, decisions and unfinished source are included in the development handoff ZIP; private backup artifacts and keys are excluded.

## Independent database restore checkpoint

[Fresh-cluster database restoration](docs/INDEPENDENT-RESTORE.md) now passes 45 live checks, including 32 table content comparisons. Destination volume measurement fixes the stopped-source preflight. Source and target are stopped with separate volumes retained. This supersedes the preceding preflight failure checkpoint. Full role/ACL readback and application, identity, object and signed-URL verification remain open.

## Restored boundary verification

Five additional live checks independently compare complete scoped roles, memberships, database ACLs and settings with the encrypted export, then verify target shutdown. Review-driven repeat-run and cleanup fixes are implemented; new failure branches still need fault-injection verification. Target application services and object recovery remain next.

## Independent Auth and REST checkpoint

Original pinned Auth/REST services now pass 11 live checks against the independent restored cluster: original-password login, stable identity, valid session, original RLS rows and rejection of an unrelated signing secret. Source remains stopped; target services stop afterward. Storage and signed URLs remain the next recovery gate. Details: [independent recovery](docs/INDEPENDENT-RESTORE.md).

## Independent Storage checkpoint

Eight live checks now validate original signing material under fresh target platform encryption, exact object bytes/xattrs, per-object service-key downloads and unrelated-secret denial. Initial common-content fixture assumption was corrected and retained target verification passed. All owned services are stopped. End-user Storage RLS, valid pre-export signed URL continuity and full platform recovery remain open.

## Independent end-user Storage and source URL checkpoint

Twelve end-user checks now verify original-login object access, different-owner download/sign denial and anonymous denial. Seven sequential source/target checks verify source signing material equals the export and an unchanged source-issued signature works on the independent target while tampering fails. URL issuance occurred after export; pre-export chronology and public route cutover remain open. Both stacks are stopped.

## Recovery cleanup failure checkpoint

Six injected failure unit cases and six disposable-container live checks now cover independent cleanup attempts and truthful status publication. Three reproduced defects were fixed: helper failures skipping DB stop and a missing verified target being treated as success. All 40 Python tests pass. No retained data was modified. Actual interrupted restoration and process-death reconciliation remain unverified.

## Recovery interruption and reconciliation checkpoint

An explicit identity/ownership-checked reconciliation command stops retained target services without deleting data. Five new unit cases bring Python tests to 45. A real pg_restore interruption/retry probe passes 38 checks including rollback and all 32 table hashes after replay. Disposable database removed; target stopped. Controller death, automatic stage resume and route cutover remain open.

## Gateway drain checkpoint

[Per-environment pause leases](docs/GATEWAY-DRAIN.md) now reject new requests while existing gateway slots drain, without pausing neighbors. Five new tests include a shared managed-factory source-to-target resolver switch. All 57 Bun tests, 294 assertions and affected strict types pass. No live placement changed. Durable maintenance state, source write fencing and coordinated cutover remain next.

## Persistent routing checkpoint

[Durable maintenance and placement](docs/PERSISTENT-ROUTING.md) now survive catalog reopen, reject stale revisions and keep staged destinations inactive until resume. Managed traffic and connection discovery follow this state. All 60 Bun tests and 314 assertions pass; targeted routing types pass. Broader application typing has an existing custom-fetch/preconnect mismatch. No actual placement changed; source fencing and complete cutover orchestration remain next.

## Source database fence checkpoint

[Database connection fencing](docs/SOURCE-FENCING.md) persists refusal before terminating existing sessions. Installer restart/provisioning preserves it. All 49 Python tests and eight live disposable-database checks pass, including restart persistence, neighbor readability and explicit rollback. No source environment was fenced. Storage side effects and coordinated final export/cutover remain open.

## Export fence checkpoint

Two-stage export fencing now retains operator dump access while blocking scoped service logins, then closes the database after export persistence. Original login intent is journaled. The optional exporter integration is not yet exercised on the real source. 51 Python tests and eight disposable live checks pass. Real source remains unchanged and stopped.

## Chronological cutover export checkpoint

Coordinated source export passed 34 checks after durable maintenance of four ready environments. Selected source remains fenced. Previous target retained; new target passes 49 database, 11 Auth/REST, nine Storage and 14 end-user checks, including unchanged pre-export URL continuity. All stacks stopped; routing remains paused. Continue the private cutover operation journal. Public gateway publication and neighbor restoration remain next. This downtime rehearsal does not yet coordinate graceful drain across live gateway processes.

## Managed target publication checkpoint

Eleven real SDK checks now pass through persistent target placement and the composed gateway: identity, RLS reads/writes confirmed on target SQL, Storage upload/download and pre-export URL. Probe data/key cleaned up. Target placement retained paused and all containers stopped. Operation records possible target writes; stale-source rollback must not be automatic. Three maintenance cleanup unit cases bring Python to 54 tests. SDK/application strict types and four management/application tests pass. Unaffected source environment restoration remains next.

## Unaffected neighbor restoration checkpoint

Normal source startup preserved the moved database fence and left its old Auth/REST stopped. Three neighbors resumed with 16 gateway checks for maintenance transitions and Auth/REST availability. All source containers were stopped afterward; neighbor routes are active for the next normal startup, moved target remains paused. Routine target lifecycle/address refresh and combined-resource admission remain open.

## Retained target lifecycle checkpoint

[Target up/stop lifecycle](docs/TARGET-LIFECYCLE.md) now validates resources, preserves maintenance through health checks, refreshes addresses and resumes the moved environment. Stop pauses admission and retains data. Ten live checks and three new unit cases pass; Python total 57. Source/target startup is symmetrically restricted to staged mode pending combined resource admission and supervisor integration. Both stacks are stopped; moved route paused, neighbors ready for normal source startup.

## Combined foreground installation checkpoint

Normal dev.py now supervises source neighbors and moved target through a combined controller. Conservative planned ceilings: 5888 MiB and 5.75 CPUs, admitted under 6 GiB/6 CPU caps with host memory/CPU reserves and pressure checks. Actual supervisor passed 14 simultaneous gateway checks across console, management and all four environments; shutdown stopped both stacks. 62 Python and 60 Bun tests pass, with 314 Bun assertions. Continuous capacity, controller death and production HA remain unverified.

## Documentation handoff checkpoint

Consolidated [HANDOFF.md](docs/HANDOFF.md) with current decisions, alternatives, source research, saved diagrams and a portable continuation prompt. [RESUME-CHECKPOINT.md](docs/RESUME-CHECKPOINT.md) records the nine-check idle-supervisor SIGKILL/restart pass and two unresolved review findings: process-group identity after reaping and HTTP readiness polling. Latest recorded Python suite: 64 tests. Supervisor implementation remains uncommitted and explicitly unfinished. No production or 10/100-project capacity claim is made.

## Supervisor crash and ownership checkpoint

Parent-bound direct children now exit when the supervisor dies. Process-group cleanup retains an unreaped leader through all signals, avoiding numeric identity reuse; already-reaped leaders authorize no group signals. Two regression tests failed before the fix. All 66 Python tests now pass; nine live supervisor SIGKILL/restart checks pass with target metadata and source fence preserved. Adversarial review found no remaining must-fix in this scope. All owned runtimes stopped after the probe. Active provisioning and in-flight writes remain unverified.

## Provisioning effect lock checkpoint

The direct provisioning process now inherits the same worker flock, validates it before execution and retains it through exec. Worker death cannot release ownership while that process is still alive. A real-process regression failed before the fix; 68 Python tests, 60 Bun tests with 314 assertions and worker strict types now pass. Review found no must-fix for this scope. No Docker runtime or catalog job was created. Provisioner death, downstream Docker effects and bounded cancellation remain open; see docs/WORKER-EFFECT-OWNERSHIP.md.

## Provisioning replay protection checkpoint

Durable receipts now precede worker effects and block normal startup/replay when their outcome is unknown. Exact-claim completion is persisted before receipt removal, and historical receipts cannot overwrite newer retries. Runtime startup bypass and retry/consumption race found in adversarial review are fixed. 72 Python tests, 65 Bun tests with 354 assertions and 11 live known-capacity-refusal checks pass. No new runtime allocated; all owned runtimes stopped. Explicit uncertain-effect reconciliation and descendant containment remain open. See docs/PROVISIONING-RECEIPTS.md.

## Effect guardian checkpoint

Parent-bound guardians now retain ownership while supervising separate effect process groups, with a 180-second local deadline and TERM/KILL cleanup. Worker death cancellation failed before the change and now passes, including lock retention during cleanup. Non-cooperative grandchild fixtures and stop-during-spawn tests pass. Totals: 74 Python tests, 65 Bun tests/354 assertions, strict worker types and 11 real receipt integration checks. All owned runtimes stopped. Pending Docker/database outcomes still need explicit reconciliation; local termination is not rollback. Details: docs/EFFECT-GUARDIAN.md.

## Native outcome recovery checkpoint

Known native completion can now be recovered after a lost guardian acknowledgment without reexecution. Fresh per-worker effect leases prevent inherited worker-lock bypass; surviving workers cannot settle native evidence without restart. Live verification exposed and fixed Bun source-FD swap aliasing by duplicating sources above mapping slots. 76 Python tests, 67 Bun tests/377 assertions, strict types and 12 live known-refusal checks pass. No new runtime allocation; all owned containers stopped. Unknown native/daemon effects remain blocked. Details: docs/NATIVE-OUTCOME-RECOVERY.md.

## Unresolved-effect inspection checkpoint

Read-only provisioning inspection now binds receipt/witness/catalog identities, independently acquires existing locks and sanitizes exact-owned Docker observations. Conditional metadata SQL is bounded and READ ONLY; stopped services stay stopped. Replay is never authorized. Six isolated tests and seven live checks pass, with unchanged state hashes and 19 owned containers stopped. Total Python suite: 82. Stage-specific partial-effect recovery remains open. Details: docs/PROVISIONING-INSPECTION.md.

## Bounded preflight recovery, latest checkpoint

Read docs/PREFLIGHT-RECOVERY.md. New native stage records allow exact pre-mutation recovery under fresh worker/effect ownership and the operation lock, with at most two automatic requeues. Later stages remain blocked without native completion. Review exposed and fixed retained-credential admission bypass and startup provisioning of reservations before authorization. Startup now resumes only published environments.

Validation: 87 Python tests, 73 Bun tests/408 assertions, strict worker/receipt types and 13 live known-refusal integration checks. Temporary native SIGKILL tests cover both sides of the stage boundary, not a full active-supervisor crash. No new runtime allocated; all owned runtimes stopped and receipt consumed after the live check. Next: stage-aware read-only inspection and a full active-provisioning supervisor crash rehearsal.

## Active preflight supervisor crash, latest checkpoint

Read docs/ACTIVE-PREFLIGHT-CRASH.md. Twenty-five live checks pass through actual supervisor SIGKILL with the native provisioner paused after durable preflight. Fresh startup requeues the exact claim once; the next attempt is capacity-refused with a matching native witness. No runtime allocation or credential change. Four existing environments respond, then all owned runtimes stop. No pending receipt remains. The fixture has consumed one lifetime automatic preflight requeue; do not reset history.

The inspector now reports sanitized exact stage evidence and recommends fresh bounded evaluation only with a missing native witness and current matching claim. All 88 Python tests pass; unchanged Bun checkpoint is 73 tests/408 assertions. Review added ownership-aware cleanup and capacity preconditions before the live run. Later-stage effects and arbitrary crash timing remain unproven. Next design isolated database/service fault fixtures and explicit reconciliation, preserving all retained environments.

## Partial database interruption and closed bootstrap, latest checkpoint

Read docs/PARTIAL-DATABASE-CRASH.md. A disposable pinned PostgreSQL 17 container exercised actual provision_environment SQL at roles, database, permissions and injected transaction failure. Fifty-nine checks pass: partial SQL persists, pending receipts and exact claims remain unchanged, automatic replay is blocked, scoped access works after permissions, and neighbor data remains unchanged. The isolated container was removed by exact identity; retained volumes/catalogs were not used by this probe.

Measured default PUBLIC CONNECT before the permission step motivated hardening: CREATE DATABASE now starts with ALLOW_CONNECTIONS false. Revoke/grant/reopen commit together, only for a database created by that invocation. Existing closed databases are refused before writes. Two regression tests fail against the previous bootstrap; all 91 Python tests pass now. Adversarial review found no remaining must-fix in this scope. The separate retained integration passes 13 checks and stops all owned runtimes. Bun code is unchanged at the recorded 73 tests/408 assertions.

This is interruption between completed SQL calls, not a daemon failure or in-flight query cancellation guarantee. New full Supabase provisioning after this change remains a separate integration gate. Next establish explicit database-stage reconciliation and disposable upstream-distribution coverage before permitting recovery of partial allocations. Do not un-fence the moved source or clear an unknown receipt.

## Upstream SQL boundary and fresh services, latest checkpoint

The partial-database probe now supports an explicit upstream profile with the pinned Supabase PostgreSQL distribution. Sixty-five checks pass there; 64 pass on the component profile. Each verifies unprivileged neighbors and initially absent fixtures. The upstream profile uses 1 GiB memory, 1 CPU, 512 MiB tmpfs and 4 GiB required host headroom, without network or published ports. Both remove only their exact disposable resource.

Separately, fresh two-environment original Auth/REST/shared Storage integration passes 122 checks after the closed-bootstrap change. Sanitized evidence is docs/evidence/upstream-closed-bootstrap-checks.json. Probe containers/networks are removed, and retained runtime state is unchanged by these experiments. No new unit-test claim: the prior full-suite checkpoint remains 91 Python and 73 Bun tests/408 assertions.

Adversarial review found no remaining must-fix in profile implementation. It rejected a proposed single-control-database advisory lock as whole-operation fencing: advisory locks are database-local. Read docs/DATABASE-OPERATION-FENCING-DESIGN.md. Next prove a same-database SQL revocation barrier and explicitly handle target-database statements before enabling any database-stage recovery. No such SQL barrier is implemented yet; unknown partial effects remain blocked.

## Database-local SQL revocation prototype, latest checkpoint

Read docs/SQL-OPERATION-FENCE.md. Experimental scripts and a disposable upstream probe now prove a same-database token revocation barrier: active SQL completes before queued revocation; delayed old SQL rechecks after the lock and fails; cancelled revocation leaves authority active. Thirty-one live checks cover tombstones, stale identities, private registry access despite default grants, reconnects and CREATE DATABASE. Identical lock keys in separate databases are explicitly shown not to exclude each other.

The full Python checkpoint is 94 tests; unchanged Bun checkpoint is 73 tests/408 assertions. Exact disposable cleanup passed. No retained runtime or catalog mutation. This is not integrated into Runtime.sql or worker recovery, and no operator revoke endpoint exists. Next design and test target-database barriers and registration/coordinator crash order before claiming whole-operation fencing or enabling database-stage recovery.

## Sequential SQL revocation and handoff, latest checkpoint

Read docs/SQL-PAIR-REVOCATION.md. The experimental coordinator passes 28 upstream live checks, including actual SIGKILL between control and target commits, target writes during the gap, retry, tombstones, absent/closed targets and replacement detection. Registry bootstrap is now atomic with privilege revocation; the separate same-database probe passes 34 checks. All 98 Python tests pass; recorded unchanged Bun checkpoint remains 73 tests/408 assertions. Exact disposable cleanup passed.

These prototypes are not integrated into runtime SQL or worker recovery and do not authorize replay. Next inventory and guard runtime SQL effects, then address services/Storage and durable reconciliation. Keep unknown later-stage receipts blocked. HANDOFF.md remains the concise entry point, with decisions, research, saved diagrams and a continuation prompt for either assistant.

## Runtime mutation audit and generation counterexample, latest checkpoint

Read docs/PROVISIONING-MUTATION-MAP.md before integration. Independent review found that an absent-target barrier cannot stop delayed old registration after a newer claim creates the database. The real upstream pair probe now reproduces this counterexample among 29 checks. It remains unresolved; do not treat the count as a complete safety result. HBA file writes and service-driven migrations are additional unguarded boundaries.

A separate live failing regression exposed guard lock output contaminating scalar query results. DO/PERFORM now suppresses guard rows; the same-database probe passes 35 checks. Both disposable probes removed their exact containers. No retained runtime changes or new replay permissions. Next implement generation-bound target admission before connecting SQL evidence to recovery.

## Bound target registration, latest checkpoint

The isolated register_target coordinator now acquires target metadata through the exact active control guard, refuses absent/closed targets and pins cluster/OID checks before target bootstrap. Real delayed dispatch tests reject revocation and DROP/CREATE after binding. The pair probe passes 37 checks, including the retained deliberate counterexample for unsafe low-level registration. All 100 Python tests pass. Read SQL-PAIR-REVOCATION.md for assumptions and exclusions.

Not integrated into runtime provisioning. Next build the explicit guarded executor and target registration transition, separating routine resume from creation and moving HBA out of the SQL-only boundary. Service migration, Storage and durable recovery remain open. OID reuse and restored clones are not covered by this prototype.
