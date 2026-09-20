# Paused handoff for Hermes

The user explicitly stopped Codex implementation on 2026-09-20 to continue with another Hermes agent/model. No new implementation or experiments should run in this Codex task without a new user request. This file is a handoff, not a claim that the platform is complete.

## Project and decision

Checkout: `/home/sbarah/R/Projects/P/sbarbase`. Read `~/AGENTS.md` first. Use Bun and `/usr/bin/python3`. Keep credentials and runtime state private. Use one active writer.

Open-source self-hosted platform on original Supabase. Hierarchy: installation > organization > project > environment. Ownership is separate from server placement. Current candidate shares PostgreSQL with separate databases/scoped credentials per environment, original Auth/REST per environment and shared tenant-aware Storage. Independent PostgreSQL remains the fallback. Trusted host operators and SQL authors are assumed. [Reasons and alternatives](DECISIONS.md).

## Completed and evidenced

- Local console, organizations/projects/environments, scoped keys, queued provisioning and original Supabase services.
- Four retained local environments, including one moved to a separate local recovery target. Auth/RLS/private Storage, export/restore, source fencing and persistent routing were exercised.
- Source HBA authority integrated into startup and worker provisioning, with exact ownership, generation pins, immutable attempts and durable completion.
- Latest full Python run: 217 passing tests. Recorded Bun run: 73 tests/408 assertions. Healthy real worker/restart/SDK rehearsal: 76 checks.
- Latest native worker SIGKILL rehearsals: 90 checks each, after intent registration and after durable applied witness. Counts overlap the healthy baseline. HBA-only reconciliation preserves the unresolved services-stage job and prevents replay. [Exact scope](WORKER-HBA-CRASH.md).

## State at stop

Read-only inventory confirms all 11 source containers and all 8 recovery-target containers are stopped. No fresh test containers remain. Preserve source/targets and all volumes. The moved source remains fenced and target routing paused according to the last retained checkpoint; inspect live private state before any operation.

The retained source has no HBA generation pin. Its startup deliberately refuses pending explicit adoption. Do not remove journals, pins or revocations to bypass this gate. The old container-recreation probe is disabled before side effects until generation migration exists. The ZIP excludes secrets, local state, dependencies and Git history; it is not an application-data backup.

## Next work, in order

1. Implement and crash-test the [durable legacy adoption operation](HBA-LEGACY-ADOPTION-DESIGN.md) on disposable legacy fixtures. Same-container adoption requires an explicit quiescence assumption; stopped containers and locks do not prove that no old Docker requests remain.
2. Only after review and verification, reconcile retained adoption. Container-generation migration and recovery-target writers are separate unfinished work.
3. Broader worker/supervisor interruption, service effects and later-stage recovery. Current evidence is two native worker checkpoints, not arbitrary crash or power-loss safety.
4. Sustained mixed-load capacity, off-host restore, upgrades, complete organization transfer and multi-host coordination. Realtime/functions/pooler/cron remain unfinished.

There is no verified capacity guarantee for 10 or 100 projects. Retained configured ceilings of 5888 MiB/5.75 CPUs are allocation limits, not measured peak demand.

## References and visuals

[Current integration](SOURCE-HBA-INTEGRATION.md), [full handoff/research map](HANDOFF.md), [chronological checkpoint](RESUME-CHECKPOINT.md), [ten-project diagram](diagrams/ten-projects.png), [migration/recovery diagram](diagrams/move-and-restore.png). Pictures show design intent, not completed features or proven capacity. Source links and adversarial reviews are preserved in the repository.
