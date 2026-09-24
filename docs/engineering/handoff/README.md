# Handoff for agents

Current state and next step for a coding agent (Claude Code, Codex, Hermes or another) continuing this work. Updated 2026-09-23. Human-facing documentation starts at [docs/README.md](../../README.md); this folder is for continuity only.

## Read first

1. The repository's `CLAUDE.md` and `AGENTS.md`, and the machine-wide agent instructions they import.
2. [Status](../../reference/status.md): what works and the current test numbers.
3. [lab/README.md](../../../lab/README.md) before running anything that starts containers.
4. The engineering note for the subsystem you will touch, from the [notebook index](../README.md).

## Rules

- Package manager is bun; Python is `/usr/bin/python3`. A bare `python3` may be a different interpreter.
- One active writer per checkout. Only one checkout or worktree may run a placement per Docker daemon, because container names are fixed.
- Inspect Git and live state first. Preserve retained volumes, `.lab/`, source fencing and unrelated Docker resources.
- Never remove journals, generation pins, receipts, tombstones or revocations, and never recreate retained containers, to get past a refusal.
- Never read or print `.secrets/`.
- Do not claim production readiness, automatic later-stage recovery, full security or fixed project capacity.
- Keep human pages (README, explain, guides, reference, decisions) free of agent instructions and absolute home paths. Run `/usr/bin/python3 -m unittest discover -s lab -p test_docs_links.py` after moving or renaming any document, and `test_doc_references.py` after renaming a script or evidence file.

## Decision in one paragraph

Keep original Supabase. Model installation > organization > project > environment, with ownership separate from server placement. The candidate shares one PostgreSQL engine with a separate database and scoped service logins per environment, runs original Auth and REST per environment, and shares one tenant-aware Storage process. Independent PostgreSQL remains the fallback. Operators and SQL authors are trusted; application visitors are not. Each environment is meant to be administered through the original upstream Studio ([specification](../STUDIO-INTEGRATION.md)); the console covers only the platform layer. Why, and the alternatives: [decisions](../../decisions/README.md), [architecture review](../ARCHITECTURE-REVIEW.md), [Supabase feasibility](../reviews/supabase-feasibility.md), [security](../reviews/security-operations.md), [alternatives](../reviews/alternatives-product.md), [capacity method](../reviews/capacity-method.md), [storage recovery](../reviews/storage-recovery.md).

Configured resource ceilings are not measured demand or a hardware recommendation. There is no validated maximum of 10 or 100 projects, and daily visitors alone cannot determine capacity.

## Current state

- Source release [0.1.0](../../../CHANGELOG.md) (2026-09-21) plus, unreleased, the container generation migration: `lab/migrate-generation.py`, journaled and passing five SIGKILL crash points on the disposable fixture.
- Both retained databases (source and recovery target) were adopted into the owned HBA authority on 2026-09-20 and carry generation pins.
- The retained moved environment stays on its recovery target; its source database and scoped logins remain fenced. Resume from the private cutover journal, never by rerunning export or allocation.
- The deployment path passes on the development workstation and, from an empty server, in a local Fedora 44 VM with 4 vCPU and 6 GiB: acceptance 12 of 12, first project 13 of 13, reboot survived ([summary](../../evidence/vm-empty-server-rehearsal.json)). Repeat it with `lab/vm-rehearsal.sh`. No real server has been used.
- Source HBA authority is integrated into startup and worker provisioning. Complete HBA writes reject truncation and stale prepared requests; a reload acknowledgment does not prove enforcement and does not end existing sessions.
- 2026-09-24: the [verification and migration plan](../plans/2026-09-23-verification-and-migration-plan.md) records the hierarchy follow-ups (most done, each with tests), the competitor comparison and the import design; import phase 0 exists as `lab/import_inspect.py`.

## Next step

**Follow the [roadmap](../plans/2026-09-23-roadmap.md)**: a real server first (the owner expects one next month), then backups as a feature, Studio per environment and upgrades. Use [CONTRIBUTING.md](../../../CONTRIBUTING.md) for the workflow.

On the workstation, still pending: **the attended generation migration of the retained database.** `lab/migrate-generation.py` refuses the retained placement by design; running it there is a deliberate operator action, and its acceptance is in [what remains](../CONTAINER-GENERATION-MIGRATION.md#what-remains). Until it happens, `lab/durable-check.ts` stays disabled and the arrival-driven pressure and mixed SDK load measurements stay blocked.

Still open after that: a real-server rehearsal, later-stage crash recovery, service effects, sustained mixed-load capacity, off-host restore, upgrades, complete organization transfer, multi-host coordination, and Realtime, Functions, pooler, cron and per-environment Studio.

## History

The chronological project log is [checkpoints](../checkpoints.md). The earlier handoff snapshots (the one-page summary, the 2026-09-20 handoff, the Hermes handoff, the resume checkpoint log and the session record) were removed on 2026-09-24 and remain in the repository history.
