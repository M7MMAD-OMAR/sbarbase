# Handoff for agents

Current state and next step for a coding agent (Claude Code, Codex, Hermes or another) continuing this work. Updated 2026-10-04. Human-facing documentation starts at [docs/README.md](../../README.md); this folder is for continuity only.

## Read first

1. The repository's `CLAUDE.md` and the machine-wide agent instructions. `AGENTS.md` is a local, untracked file (graft guidance) and may be absent.
2. [COMPLIANCE](../../../COMPLIANCE.md), [project goal](../../../PROJECT_GOAL.md), [October product plan](../plans/2026-10-03-product-and-portability-plan.md), [execution method](../plans/2026-10-03-gauntlet-execution-method.md), [native identity](../plans/2026-10-03-native-placement-identity.md) and [ledger](../gauntlet-ledger.json). Then [status](../../reference/status.md) for capability scope and dated evidence.
3. [lab/README.md](../../../lab/README.md) before running anything that starts containers.
4. The engineering note for the subsystem you will touch, from the [notebook index](../README.md).

## Rules

- Package manager is bun; Python is `/usr/bin/python3`. A bare `python3` may be a different interpreter.
- Assign disjoint source paths to writers; freeze public source during a declared immutable runtime evidence window. Only one checkout or worktree may run a placement per Docker daemon, because container names are fixed.
- Inspect Git and live state first. Preserve retained volumes, `.lab/`, source fencing and unrelated Docker resources.
- Never remove journals, generation pins, receipts, tombstones or revocations, and never recreate retained containers, to get past a refusal.
- Never read or print `.secrets/`.
- Do not claim production readiness, automatic later-stage recovery, full security or fixed project capacity.
- Keep human pages (README, explain, guides, reference, decisions) free of agent instructions and absolute home paths. Run `/usr/bin/python3 -m unittest discover -s lab -p test_docs_links.py` after moving or renaming any document, and `test_doc_references.py` after renaming a script or evidence file.

## Decision in one paragraph

Keep original Supabase. Model installation > organization > project > environment, with ownership separate from server placement. The candidate shares one PostgreSQL engine with a separate database and scoped service logins per environment, runs original Auth and REST per environment, and shares one tenant-aware Storage process. Independent PostgreSQL remains the fallback. Operators and SQL authors are trusted; application visitors are not. The shared-runtime path administers environments through on-demand original upstream Studio ([specification](../STUDIO-INTEGRATION.md)); the console covers only the platform layer. Why, and the alternatives: [decisions](../../decisions/README.md), [architecture review](../ARCHITECTURE-REVIEW.md), [Supabase feasibility](../reviews/supabase-feasibility.md), [security](../reviews/security-operations.md), [alternatives](../reviews/alternatives-product.md), [capacity method](../reviews/capacity-method.md), [storage recovery](../reviews/storage-recovery.md).

Configured resource ceilings are not measured demand or a hardware recommendation. There is no validated maximum of 10 or 100 projects, and daily visitors alone cannot determine capacity.

## Current state, 2026-10-04

- Package 0.2.0 is a development snapshot; historical 0.1.0 release evidence remains separately dated.
- Source baseline `6c112c3` passed all four CI jobs in run 37135601249 and Website run 37134926306. Pending source or documentation edits require fresh checks.
- Existing shared-runtime Docker workflows include Studio, Realtime, Functions, daily backups, bounded encrypted offsite recovery and update-channel rehearsals. These are not proof of all-host or complete cloud compatibility.
- Native placement declarations and read-only routing are accepted only in their named scope. Dedicated activation and remaining consumers are unaccepted. The latest configured-effects fixture refuses the routine privilege model after the corrected projection executes; do not expand privileges to bypass it.
- Historical retained runtime and VM observations are in the dated [checkpoints](../checkpoints.md) and evidence files. Do not assume those fixture identities or resource observations describe present live state. Inspect only the explicitly authorized fixture before any runtime work.

## Next work

Follow the October plan and execution contract. Prioritize the capability/support registry, host security lifecycle, coherent application and operator recovery, and bounded native admission. Establish the exact installed-hook cause before any future routine ACL admission change. Each runtime slice needs its declared resource budget, immutable source, named fixture, independent critic and explicit losses. Public reproducibility, independent-host pilot, capacity, full services/pools, security maintenance and release remain open. A roadmap does not authorize purchases, deployment or host changes.

## History

The chronological project log is [checkpoints](../checkpoints.md). The earlier handoff snapshots (the one-page summary, the 2026-09-20 handoff, the Hermes handoff, the resume checkpoint log and the session record) were removed on 2026-09-24 and remain in the repository history.
