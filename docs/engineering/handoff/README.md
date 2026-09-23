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

## Current state

- Source release [0.1.0](../../../CHANGELOG.md) (2026-09-21) plus, unreleased, the container generation migration: `lab/migrate-generation.py`, journaled and passing five SIGKILL crash points on the disposable fixture.
- Both retained databases (source and recovery target) were adopted into the owned HBA authority on 2026-09-20 and carry generation pins.
- The retained moved environment stays on its recovery target; its source database and scoped logins remain fenced. Resume from the private cutover journal, never by rerunning export or allocation.
- The deployment path (preflight, installer, systemd unit, acceptance script) passes on the development workstation only. A clean-VM rehearsal is in progress; no real server has been used.

## Next step

**The attended generation migration of the retained database.** `lab/migrate-generation.py` refuses the retained placement by design; running it there is a deliberate operator action. Until it happens, `lab/durable-check.ts` stays disabled and the arrival-driven pressure and mixed SDK load measurements stay blocked.

Still open after that: a real-server rehearsal, later-stage crash recovery, service effects, sustained mixed-load capacity, off-host restore, upgrades, complete organization transfer, multi-host coordination, and Realtime, Functions, pooler, cron and per-environment Studio.

## Earlier handoff notes

Kept as they were written; newer notes supersede older ones.

- [START-HERE](START-HERE.md): the one-page summary as of 2026-09-23, before this restructuring.
- [HANDOFF](HANDOFF.md): decisions, measured experiments and remaining gates, snapshot of 2026-09-20.
- [HERMES-HANDOFF](HERMES-HANDOFF.md): the paused handoff, deployment blockers and ordered next work.
- [RESUME-CHECKPOINT](RESUME-CHECKPOINT.md): the long chronological checkpoint log.
- [SESSION-RECORD](SESSION-RECORD.md): implementation record through 2026-09-20.
- [checkpoints](../checkpoints.md): the former `PROJECT.md`, chronological project checkpoints.
