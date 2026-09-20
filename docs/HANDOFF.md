# Sbarbase handoff

For the shortest overview, read [START-HERE](START-HERE.md). Unfinished local work: `lab/hba_authority.py` is an isolated, unintegrated prototype with 8 host shell tests and 56 pinned-image checks; no operation-authority guarantee follows from its presence.

Snapshot: 2026-09-20. Start here, then read [the current checkpoint](RESUME-CHECKPOINT.md). Repository files are the continuation source; older chat and chronological status entries may be superseded.

## Decision and rationale

Build an open-source, self-hosted platform on original Supabase services, initially on one server. Hierarchy: **installation > organization > project > environment**. Ownership is independent of the server hosting each environment.

Experimental runtime: shared PostgreSQL, a separate database and scoped service credentials per environment, original Auth/REST per environment, shared tenant-aware Storage. Keep compatibility without inventing request-time database switching in Auth/REST. Independent PostgreSQL instances remain the fallback if isolation, upgrades, recovery or resource savings do not justify sharing. Trusted host operators and SQL authors are assumed; this is not isolation from hostile administrators.

[Decision register](DECISIONS.md) compares alternatives and conditions for reconsideration. Schema-only separation does not provide the desired independent lifecycle. Full stacks remain the resource-cost baseline. Replacing Supabase conflicts with the user's compatibility requirement. Deployment managers alone do not provide this ownership and environment model. This is a tested local candidate, not a proven best architecture for every workload.

## What exists and what was measured

- Local console, management login, organizations/projects/environments, scoped keys, queued provisioning, original Supabase Auth/REST/Storage and retained volumes.
- Four local environments: three on the source cluster and one restored onto a separate local target. Real SDK tests cover identity, RLS, reads/writes, files and an unchanged signed URL created before export.
- Fenced encrypted export, independent restore, persistent routing, maintenance, address refresh, target startup/shutdown and combined supervisor. See [recovery details](INDEPENDENT-RESTORE.md) and [combined runtime](COMBINED-RUNTIME.md).
- Combined configured ceilings: **5888 MiB RAM and 5.75 CPUs**, within a 6 GiB/6 CPU admission cap plus host reserves. This is an experimental allocation budget, not actual peak use or a VPS recommendation. No measured 10/100-project limit exists.
- Latest recorded suites: 203 Python tests; 73 Bun tests, 408 assertions. The latest nine-check supervisor SIGKILL rehearsal passed for an idle worker and subsequent restart. The two review findings were fixed and reviewed; see the checkpoint. Test counts have different scopes and are not cumulative safety coverage.

## Research and reviews

Source links, findings and limitations are preserved in [architecture research](ARCHITECTURE-REVIEW.md), [feasibility](reviews/supabase-feasibility.md), [security](reviews/security-operations.md), [alternatives](reviews/alternatives-product.md), [capacity](reviews/capacity-method.md), [bootstrap](reviews/distribution-bootstrap.md), [Storage](reviews/shared-storage.md), [recovery](reviews/storage-recovery.md), [runtime](reviews/durable-runtime.md) and [management](reviews/upstream-management.md). Multiple adversarial reviews informed the work; they are not security certification. This handoff consolidates existing research, not a new source audit.

## Saved visuals

- [Ten-project hierarchy](diagrams/ten-projects.png).
- [Ownership, migration and recovery](diagrams/move-and-restore.png).
- [Diagram assumptions and prompts](diagrams/README.md).
- [Console concept](design/console-concept.png), [desktop](design/console-desktop.jpg), [mobile](design/console-mobile.jpg), [visual QA](design/CONSOLE-QA.md).

The pictures illustrate design intent, including future operations. Ten projects is an illustrative pilot proposal, not demonstrated capacity. The optional second server is future placement; current experiments use one computer. The UI follows Supabase's direction, not a complete implementation of its design system.

Direct provisioning effects now retain worker ownership after worker death; see [scope and tests](WORKER-EFFECT-OWNERSHIP.md). A [durable receipt gate](PROVISIONING-RECEIPTS.md) now blocks replay and startup after unknown outcomes. A [parent-bound guardian](EFFECT-GUARDIAN.md) now applies a local deadline and group termination protocol. A [native completion witness](NATIVE-OUTCOME-RECOVERY.md) now recovers a lost acknowledgment without replay. A [read-only inspector](PROVISIONING-INSPECTION.md) now reports evidence for unresolved effects. [Bounded preflight recovery](PREFLIGHT-RECOVERY.md) now requeues proven pre-mutation interruptions under fresh ownership. Later-stage unknown outcomes remain blocked. A [database-local SQL revocation prototype](SQL-OPERATION-FENCE.md) passes 35 live checks but is not integrated into recovery. [Partial database interruption checks](PARTIAL-DATABASE-CRASH.md) now cover 64 component and 65 upstream-distribution assertions; new databases stay closed until their connection permissions commit. Fresh original Auth/REST/Storage integration also passes 122 checks after that change. [A real active-preflight supervisor crash and restart](ACTIVE-PREFLIGHT-CRASH.md) passed 25 checks without allocating resources.

A [sequential two-database revocation prototype](SQL-PAIR-REVOCATION.md) also passes 51 live checks, including coordinator SIGKILL between commits and retry. It is not an atomic cutoff or automatic recovery. Registry creation and privilege restriction now commit together to remove a default-grant exposure window.

Latest adversarial finding: a real counterexample restores old target authority after a newer claim creates a previously absent database. A new isolated admission path now rejects this by obtaining a control-authorized, database-identity-bound registration. The unpinned primitive still reproduces the counterexample and must not be used for target admission. [The mutation map](PROVISIONING-MUTATION-MAP.md) records this blocker plus HBA and service-driven mutation boundaries. No production recovery was enabled.

The [guarded SQL adapter](GUARDED-PROVISIONING-SQL.md) now executes the actual closed-bootstrap provisioning path in a disposable upstream fixture, preserving output and refusing post-revocation writes. [Receipt-bound runtime SQL](RECEIPT-BOUND-SQL.md) is now wired: exact worker claim validation precedes SQL, and SQL authority retires before services. A [complete fresh worker-driven lifecycle](FRESH-WORKER-LIFECYCLE.md) now passes 57 live checks with real Auth/REST/Storage, positive and negative SDK access tests and isolated cleanup.

[Published-environment resume](PUBLISHED-ENVIRONMENT-RESUME.md) now starts retained services without rerunning native database provisioning or creating missing Storage tenants. All four retained environments respond in the latest supervisor rehearsal and all owned containers stop afterward.

[Atomic HBA replacement](ATOMIC-HBA-REPLACEMENT.md) now validates complete content before replacing connection rules. Thirty-six real checks cover producer EOF, helper death around rename and stale prepared-request rejection under a stable lock. Real connection probes distinguish file publication, reload and enforcement; existing sessions survive HBA restriction. Full operation revocation and activation recovery remain unresolved.

## Remaining gates

Test later-stage active-job crash recovery, coordinated graceful cutover and sustained mixed traffic across placements. Production installation, off-host recovery, upgrades, full organization transfer, multi-host coordination, Realtime/functions/pooler/cron and capacity at 10 or 100 projects remain unfinished. Daily visitor counts alone cannot size the system.

## Continue here or in Hermes

Both can continue from this same directory. Use one active writer; changing assistant does not improve or invalidate the architecture. No Hermes execution has been dispatched. Suggested continuation message:

> Work in /home/sbarah/R/Projects/P/sbarbase. Read ~/AGENTS.md, docs/START-HERE.md, docs/HANDOFF.md, the latest docs/RESUME-CHECKPOINT.md entries and lab/README.md. Inspect Git and live state first. The next bounded task is source-writer integration of the validated HBA protocol, with explicit startup ownership, guardian receipt versioning and a fail-closed legacy-adoption gate. Read docs/HBA-OPERATION-AUTHORITY-DESIGN.md before changing it. Preserve retained volumes, source fencing and unrelated Docker resources. Continue local experiments with adversarial review and one active writer. Do not claim production readiness, automatic later-stage recovery or fixed project capacity.

The ignored `sbarbase-handoff.zip` includes source, research, diagrams and sanitized evidence, including the reviewed supervisor crash changes and verified preflight recovery source. It excludes `.secrets/`, `.lab/`, dependencies and Git history. It is a development handoff, not a data backup or a runnable copy of the retained installation. On this computer, continue in the existing directory.
