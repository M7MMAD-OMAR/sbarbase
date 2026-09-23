# Sbarbase in one page

Updated 2026-09-23. This is the short entry point for any assistant or contributor. Detailed evidence remains in [HANDOFF](HANDOFF.md); older chronological checkpoints can be superseded.

## Decision

Keep original Supabase. Model **installation > organization > project > environment**, with ownership separate from server placement. Trial: shared PostgreSQL, separate database and service credentials per environment, original Auth/REST per environment, shared tenant-aware Storage. Independent PostgreSQL remains the fallback. This assumes trusted host operators and SQL authors. Each environment is administered through the original upstream Supabase Studio; the sbarbase console is the platform layer above it, covering organizations, projects, environments, connection details, keys and provisioning status.

## Why this candidate

Compatibility is required. Replacing Supabase changes that contract. Schema-only separation does not give the intended independent lifecycle. Full stacks are simpler isolation baselines but may cost more resources. Sharing Auth/REST through custom request-time switching adds unproven compatibility and security work. The shared candidate is conditional, not universally best. [Alternatives and reversal criteria](../../decisions/README.md).

## Evidence and limits

Four local environments have been exercised, including one restored to a separate local target. Real SDK checks cover Auth, RLS and private Storage access. Provisioning uses durable receipts, exact worker identities and scoped SQL guards. Complete HBA writes reject truncation and stale prepared requests; reload acknowledgment does not prove enforcement or terminate existing sessions.

The 0.1.0 release gates were 575 Python tests and 87 Bun tests, plus the UI typecheck and console build check. Earlier recorded checkpoints: 217 Python tests, 73 Bun tests/408 assertions, 76 fresh worker/SDK checks, 51 SQL-pair checks and 36 HBA checks. These have different scopes; Python, Bun and the fresh worker lifecycle were rerun for source HBA integration; other component checkpoints remain recorded evidence. They do not certify production security.

Configured retained ceilings are 5888 MiB RAM/5.75 CPUs, not measured demand or a hardware recommendation. There is no validated maximum of 10 or 100 projects. Daily visitors alone cannot determine capacity.

## Saved research and pictures

- [Ten-project hierarchy](../../diagrams/ten-projects.png) and [migration/recovery](../../diagrams/move-and-restore.png), with [assumptions](../../diagrams/README.md). These depict intent, not completed features or proven capacity.
- [Architecture review](../ARCHITECTURE-REVIEW.md), [Supabase feasibility](../reviews/supabase-feasibility.md), [security](../reviews/security-operations.md), [alternatives](../reviews/alternatives-product.md), [capacity method](../reviews/capacity-method.md), [recovery](../reviews/storage-recovery.md).
- [Console concept](../../design/console-concept.png) and [visual QA](../../design/CONSOLE-QA.md) record the platform-layer console. Environment administration is the original upstream Studio, so parity with Studio's design system is withdrawn as a goal for our own UI. See [the Studio integration specification](../STUDIO-INTEGRATION.md).

## Current state

Released as source version [0.1.0](../../../CHANGELOG.md) on 2026-09-21: resource tiers with per-device block IO limits, derived placement accounting, pressure sampling with a level 1 admission response, per-environment mail through original Auth, and operator notifications by email or signed webhook. The server installation path (preflight, installer, systemd unit) is rehearsed on the development host only ([readiness](../../reference/deployment-readiness.md)).

Source HBA authority is integrated into startup and worker provisioning. The retained legacy source was adopted explicitly on 2026-09-20 ([evidence](../../evidence/retained-source-adoption.json)). The container generation migration is implemented and passes five SIGKILL crash points on the disposable fixture ([design](../CONTAINER-GENERATION-MIGRATION.md), [plan](../plans/2026-09-21-generation-migration-plan.md)).

**Next step: the attended generation migration of the retained database.** `lab/migrate-generation.py` refuses the retained placement by design; that run is a deliberate operator action. Until it happens, `lab/durable-check.ts` stays disabled and the arrival driven pressure and mixed SDK load measurements stay blocked. Never remove journals, pins, receipts or revocations, and never recreate retained containers to get past a refusal.

Still open: a rehearsal on a real server, later-stage crash recovery, service effects, sustained mixed-load capacity, remote restore, upgrades, complete organization transfer and multi-host coordination. Realtime, functions, pooler, cron and the per-environment Studio are not completed platform features.

## Continue in either assistant

Use the same checkout and one active writer. The project files, not assistant memory, are authoritative. This update does not launch work in Hermes.

> Work in /home/sbarah/R/Projects/P/sbarbase. Read ~/AGENTS.md, docs/engineering/handoff/START-HERE.md, docs/engineering/handoff/HANDOFF.md, the latest docs/engineering/handoff/RESUME-CHECKPOINT.md entries and lab/README.md. Inspect Git and live state. Read docs/engineering/SOURCE-HBA-INTEGRATION.md and docs/engineering/HBA-OPERATION-AUTHORITY-DESIGN.md before extending the current source protocol. Continue bounded local experiments with adversarial review. Preserve retained volumes, source fencing and unrelated Docker resources. Do not claim production readiness, automatic later-stage recovery or fixed project capacity.

`sbarbase-handoff.zip` is a portable source/research/diagram handoff, including the source authority integration and its explicit remaining gates. It excludes secrets, local runtime state, dependencies and Git history. It is not a backup of application data.
