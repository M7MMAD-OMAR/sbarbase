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
- Latest previously published source `f58d6ec2f5ca1a7d78b805cbbbc449e3121f541c` passed checks, Python floor and Docker install in successful CI run 37174688429; empty-host acceptance was conditionally skipped. Manually dispatched build-only Website run 37174694973 passed. Pending source or documentation edits require fresh checks.
- Existing shared-runtime Docker workflows include Studio, Realtime, Functions, daily backups, bounded encrypted offsite recovery and update-channel rehearsals. These are not proof of all-host or complete cloud compatibility.
- Native placement declarations and read-only routing are accepted only in their named scope. Dedicated activation and remaining consumers are unaccepted. The [configured-effects review](../reviews/2026-10-04-native-cron-effects.md) accepts bounded original Cron/native HTTP behavior at baked source `43aa3887dbdc3d84990d70875959cee0b882602538f21ec7014b3b52eb71c3d0`, after exact source/file/GUC/image provenance and the seven vendor grants were independently bound. Prior failed packets remain failed; this does not activate native placement.
- [Streaming backup transport](../reviews/2026-10-04-streaming-backup-transport.md) is accepted only for offline regressions. Final publication baked source `f6dea99d62276412e9fd0673f04a7a44759f2611e6e05c6994936239b71045be` passed six stages, 1367 Python tests, 301 Bun tests, 6440 expect calls, all 25 offsite tests and two exact source-resource absences. Physical/native recovery and remote S3 remain unaccepted.
- [Patched PostgreSQL observations](../reviews/2026-10-04-patched-postgres-observations.md) accept four separate bounded scopes on immutable manifest `d6844e`: static metadata/control contents, finite public configuration, provider/entrypoint text as data, and direct configuration-volume preservation under native UID 100/GID 101. The preservation review bound 293 checks, six phases and all nine exact owned absences. No server or key-provider execution occurred. Those earlier four packets did not complete initializer/include inventories or retain a volume. The later [whole-root and key source review](../reviews/2026-10-04-native-root-key-source.md) accepts fixed 61-file/20-file public data, optional schema absence and a different positive whole-root baseline handoff. It also accepts the standalone key primitive source and 28 tests, with exact baked frozen-v2 byte capture; native key-volume/provider/startup remains unrun. Preserve every prior failed packet and the original 43aa Cron evidence at its unchanged scope.
- Historical retained runtime and VM observations are in the dated [checkpoints](../checkpoints.md) and evidence files. Do not assume those fixture identities or resource observations describe present live state. Inspect only the explicitly authorized fixture before any runtime work.

## Next work

Follow the October plan and execution contract. Prioritize the capability/support registry, host security lifecycle, coherent application and operator recovery, and bounded native admission. Retain the accepted exact installed-hook/source witness and closed ACL model; refuse drift rather than broadening grants. Each runtime slice needs its declared resource budget, immutable source, named fixture, independent critic and explicit losses. Factory initializer/custom include inventories are now accepted as data only. Before any separately admitted bootstrap, renew the exact retained volume owner/zero-consumer/full-baseline witness, freshly build/admit the Python helper runtime and match its public module bytes to accepted v2. Prove native key publication/guard, effective secret-safe logging before sensitive SQL, warm Vault continuity and coherent recovery without exposing key bytes. The provider must never run with a missing key. The standalone guard is not yet wired into candidate startup. Never reuse the removed source-verification image by tag or silently adopt/rekey a failed key fixture. Public reproducibility, independent-host pilot, capacity, full services/pools, security maintenance and release remain open. The owner selected this machine as an isolated local server with a verified Fedora 44 x86_64 profile; an external server purchase is not a prerequisite. Physical HA, independent-host portability and the PostgreSQL 17.11 patched security profile remain separate unaccepted work. A roadmap does not authorize purchases, deployment or host changes.

## History

The chronological project log is [checkpoints](../checkpoints.md). The earlier handoff snapshots (the one-page summary, the 2026-09-20 handoff, the Hermes handoff, the resume checkpoint log and the session record) were removed on 2026-09-24 and remain in the repository history.
