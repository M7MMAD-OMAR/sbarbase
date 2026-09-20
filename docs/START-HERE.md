# Sbarbase in one page

Updated 2026-09-20. This is the short entry point for Codex or Hermes. Detailed evidence remains in [HANDOFF](HANDOFF.md); older chronological checkpoints can be superseded.

## Decision

Keep original Supabase. Model **installation > organization > project > environment**, with ownership separate from server placement. Trial: shared PostgreSQL, separate database and service credentials per environment, original Auth/REST per environment, shared tenant-aware Storage. Independent PostgreSQL remains the fallback. This assumes trusted host operators and SQL authors.

## Why this candidate

Compatibility is required. Replacing Supabase changes that contract. Schema-only separation does not give the intended independent lifecycle. Full stacks are simpler isolation baselines but may cost more resources. Sharing Auth/REST through custom request-time switching adds unproven compatibility and security work. The shared candidate is conditional, not universally best. [Alternatives and reversal criteria](DECISIONS.md).

## Evidence and limits

Four local environments have been exercised, including one restored to a separate local target. Real SDK checks cover Auth, RLS and private Storage access. Provisioning uses durable receipts, exact worker identities and scoped SQL guards. Complete HBA writes reject truncation and stale prepared requests; reload acknowledgment does not prove enforcement or terminate existing sessions.

Recorded checkpoints: 198 Python tests, 73 Bun tests/408 assertions, 57 fresh worker/SDK checks, 51 SQL-pair checks and 36 HBA checks. These have different scopes; Python was rerun for the authority prototype, while the other runtime checkpoints remain recorded evidence. They do not certify production security.

Configured retained ceilings are 5888 MiB RAM/5.75 CPUs, not measured demand or a hardware recommendation. There is no validated maximum of 10 or 100 projects. Daily visitors alone cannot determine capacity.

## Saved research and pictures

- [Ten-project hierarchy](diagrams/ten-projects.png) and [migration/recovery](diagrams/move-and-restore.png), with [assumptions](diagrams/README.md). These depict intent, not completed features or proven capacity.
- [Architecture review](ARCHITECTURE-REVIEW.md), [Supabase feasibility](reviews/supabase-feasibility.md), [security](reviews/security-operations.md), [alternatives](reviews/alternatives-product.md), [capacity method](reviews/capacity-method.md), [recovery](reviews/storage-recovery.md).
- [Console concept](design/console-concept.png) and [visual QA](design/CONSOLE-QA.md). Supabase design direction is retained; full design-system parity is unfinished.

## Exact stopping point

The isolated HBA prototype covers immutable journals, worker/startup ownership, configured container identity and exact-token retirement under fresh ownership. The latest checkpoints are 198 Python tests, 56 registry checks and a separate 24-check real PostgreSQL applied-path probe. It remains **unintegrated** with actual runtime writers. Next: [live-owner adversarial validation and all-writer integration](HBA-OPERATION-AUTHORITY-DESIGN.md). Retirement alone retains the journal. Separate settlement can archive either a verified unchanged baseline or an exact durable applied/reload witness before clearing the pending journal. Neither proves activation or enables later-stage automatic replay.

Last recorded retained state: owned containers stopped, moved source fenced, target routing paused. Preserve both recovery targets and their volumes. Inspect live state before acting; never switch back blindly to the stale source. No runtime changes were made for this documentation update.

Still open: later-stage crash recovery, service effects, sustained mixed-load capacity, remote restore, upgrades, complete organization transfer and multi-host coordination. Realtime, functions, pooler and cron are not completed platform features.

## Continue in either assistant

Use the same checkout and one active writer. The project files, not assistant memory, are authoritative. This update does not launch work in Hermes.

> Work in /home/sbarah/R/Projects/P/sbarbase. Read ~/AGENTS.md, docs/START-HERE.md, docs/HANDOFF.md, the latest docs/RESUME-CHECKPOINT.md entries and lab/README.md. Inspect Git and live state. Review the isolated lab/hba_authority.py against docs/HBA-OPERATION-AUTHORITY-DESIGN.md before using it. Continue bounded local experiments with adversarial review. Preserve retained volumes, source fencing and unrelated Docker resources. Do not claim production readiness, automatic later-stage recovery or fixed project capacity.

`sbarbase-handoff.zip` is a portable source/research/diagram handoff, including the explicitly unfinished authority prototype. It excludes secrets, local runtime state, dependencies and Git history. It is not a backup of application data.
