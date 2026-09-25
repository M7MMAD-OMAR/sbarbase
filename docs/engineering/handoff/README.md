# Handoff for agents

Current state and next step for a coding agent (Claude Code, Codex, Hermes or another) continuing this work. Updated 2026-09-25. Human-facing documentation starts at [docs/README.md](../../README.md); this folder is for continuity only.

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

Updated 2026-09-25.

- Source release [0.1.0](../../../CHANGELOG.md) (2026-09-21) plus a long unreleased list in the changelog.
- The retained database was migrated to a new container generation on 2026-09-25, attended, with every row count unchanged ([evidence](../../evidence/generation-migration-retained.json)); a safety copy sits in the gitignored `.lab/generation-migration-backup-20260925/`. `lab/durable-check.ts` is now a non-destructive stop and start of the retained runtime (31 checks), and the mixed SDK load and the sustained arrival run both pass on it.
- The retained moved environment stays on its recovery target; its source database and scoped logins remain fenced. Resume from the private cutover journal, never by rerunning export or allocation. `probe.json` names `e_f61bf85...` and `e_1f0624...`; `lab/recovery-export.py` exports `probe.environments[0]`, so running it fences that one.
- Every roadmap step that needs a server was rehearsed in a local VM on 2026-09-25 with `lab/vm-rehearsal.sh --preload-images` and `lab/vm-milestones.sh`: acceptance and reboot, TLS behind a local CA across a reboot, live invitations, a backup under traffic, a restore onto a second VM, upgrade and back, the environment limit, and a 60 minute idle soak ([summary](../../evidence/vm-milestones-2026-09-25.json)). No real server has been used.
- The host this runs on is shared with other agents and swings by several GiB of available memory; the VM watchdog powers a guest off under 3 GiB. A 6656 MiB guest was the largest that stayed up, and it holds two environments.
- Source HBA authority is integrated into startup and worker provisioning. Complete HBA writes reject truncation and stale prepared requests; a reload acknowledgment does not prove enforcement and does not end existing sessions.

## Next step

**The real server, next month** ([roadmap](../plans/2026-09-23-roadmap.md), milestone 1): repeat on it what the VM rehearsed, in the same order, then add what a VM cannot show: a public certificate and DNS, a seven-day soak with two real environments, and capacity under load (milestone 1b, item 3). Use [CONTRIBUTING.md](../../../CONTRIBUTING.md) for the workflow.

Still open after that: the `--pressure` sampling mode and the experimental-class phase of the resource experiments, a restore of an environment that used Studio, Realtime or direct database access, reclaiming a deleted environment's runtime, later-stage crash recovery, the connection pooler and cron, a release archive install, and multi-host coordination.

## History

The chronological project log is [checkpoints](../checkpoints.md). The earlier handoff snapshots (the one-page summary, the 2026-09-20 handoff, the Hermes handoff, the resume checkpoint log and the session record) were removed on 2026-09-24 and remain in the repository history.
