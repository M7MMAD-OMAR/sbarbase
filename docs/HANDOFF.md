# Sbarbase handoff

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
- Latest recorded suites: 91 Python tests; 73 Bun tests, 408 assertions. The latest nine-check supervisor SIGKILL rehearsal passed for an idle worker and subsequent restart. The two review findings were fixed and reviewed; see the checkpoint. Test counts have different scopes and are not cumulative safety coverage.

## Research and reviews

Source links, findings and limitations are preserved in [architecture research](ARCHITECTURE-REVIEW.md), [feasibility](reviews/supabase-feasibility.md), [security](reviews/security-operations.md), [alternatives](reviews/alternatives-product.md), [capacity](reviews/capacity-method.md), [bootstrap](reviews/distribution-bootstrap.md), [Storage](reviews/shared-storage.md), [recovery](reviews/storage-recovery.md), [runtime](reviews/durable-runtime.md) and [management](reviews/upstream-management.md). Multiple adversarial reviews informed the work; they are not security certification. This handoff consolidates existing research, not a new source audit.

## Saved visuals

- [Ten-project hierarchy](diagrams/ten-projects.png).
- [Ownership, migration and recovery](diagrams/move-and-restore.png).
- [Diagram assumptions and prompts](diagrams/README.md).
- [Console concept](design/console-concept.png), [desktop](design/console-desktop.jpg), [mobile](design/console-mobile.jpg), [visual QA](design/CONSOLE-QA.md).

The pictures illustrate design intent, including future operations. Ten projects is an illustrative pilot proposal, not demonstrated capacity. The optional second server is future placement; current experiments use one computer. The UI follows Supabase's direction, not a complete implementation of its design system.

Direct provisioning effects now retain worker ownership after worker death; see [scope and tests](WORKER-EFFECT-OWNERSHIP.md). A [durable receipt gate](PROVISIONING-RECEIPTS.md) now blocks replay and startup after unknown outcomes. A [parent-bound guardian](EFFECT-GUARDIAN.md) now applies a local deadline and group termination protocol. A [native completion witness](NATIVE-OUTCOME-RECOVERY.md) now recovers a lost acknowledgment without replay. A [read-only inspector](PROVISIONING-INSPECTION.md) now reports evidence for unresolved effects. [Bounded preflight recovery](PREFLIGHT-RECOVERY.md) now requeues proven pre-mutation interruptions under fresh ownership. Later-stage unknown outcomes remain blocked. [Partial database interruption checks](PARTIAL-DATABASE-CRASH.md) now cover 64 component and 65 upstream-distribution assertions; new databases stay closed until their connection permissions commit. Fresh original Auth/REST/Storage integration also passes 122 checks after that change. [A real active-preflight supervisor crash and restart](ACTIVE-PREFLIGHT-CRASH.md) passed 25 checks without allocating resources.

## Remaining gates

Test later-stage active-job crash recovery, coordinated graceful cutover and sustained mixed traffic across placements. Production installation, off-host recovery, upgrades, full organization transfer, multi-host coordination, Realtime/functions/pooler/cron and capacity at 10 or 100 projects remain unfinished. Daily visitor counts alone cannot size the system.

## Continue here or in Hermes

Both can continue from this same directory. Use one active writer; changing assistant does not improve or invalidate the architecture. No Hermes execution has been dispatched. Suggested continuation message:

> Work in /home/sbarah/R/Projects/P/sbarbase. Read ~/AGENTS.md, docs/HANDOFF.md, docs/RESUME-CHECKPOINT.md and lab/README.md. Inspect Git and live state first. Read docs/PREFLIGHT-RECOVERY.md for the latest limited recovery guarantee and remaining validation. Preserve retained volumes, source fencing and unrelated Docker resources. Continue with active-provisioning crash recovery before extending crash guarantees. Continue the open-source Supabase platform with bounded local experiments and adversarial review. Do not claim production readiness or fixed project capacity.

The ignored `sbarbase-handoff.zip` includes source, research, diagrams and sanitized evidence, including the reviewed supervisor crash changes and verified preflight recovery source. It excludes `.secrets/`, `.lab/`, dependencies and Git history. It is a development handoff, not a data backup or a runnable copy of the retained installation. On this computer, continue in the existing directory.
