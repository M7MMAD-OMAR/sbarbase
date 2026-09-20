# Resume checkpoint

Current state after the chronological cutover rehearsal, 2026-09-20. This file supersedes older next-step paragraphs. Read HANDOFF.md and PROJECT.md for broader product scope.

## Decisions

Keep original Supabase services and PostgreSQL. Hierarchy: installation > organization > project > environment. Ownership is separate from runtime placement. Shared PostgreSQL with separate databases remains an experimental candidate; no production certification or 10/100-project capacity claim exists.

## Actual runtime state

- Source and both recovery targets are stopped with all volumes retained.
- Three unaffected source runtime routes have been resumed and work on normal source startup. Containers are currently stopped. The moved environment alone remains in persistent maintenance with target placement.
- Selected source database has ALLOW_CONNECTIONS=false and its three scoped service logins have NOLOGIN. Do not clear these automatically.
- Current target was restored from a new, fenced export. Previous target descriptor is retained under `.lab/upstream/recovery-target-history/`, with its path recorded in the operation journal.
- `.lab/upstream/cutover-operation.json` phase is `target-stopped-routing-paused`. Current target descriptor: `.lab/upstream/recovery-target.json`. New encrypted export pointer: `.lab/upstream/recovery-latest.json`.
- The latest SDK probe removed its row/file, revoked its temporary key and paused routing before target shutdown. Original identity, target writes and the pre-export URL passed through the managed gateway.
- None of these private paths or credentials belong in the development ZIP. Load programmatically without printing secrets.

## Verified evidence

- New export: 34 checks, including pre-export signed URL capture, service-login fence and complete database fence afterward.
- Fresh target: 49 database checks, 11 Auth/REST checks, nine Storage checks and 14 end-user Storage checks. Original identity/password, table contents, scoped roles, files/xattrs, signing material and the unchanged URL issued before export work on the target.
- Prior recovery failure work: backend termination rolls back pg_restore and the same dump restores cleanly; cleanup failures do not skip DB shutdown or falsely report success. Automatic recovery-stage resume and controller death during active operations remain unproven.
- Routing: durable maintenance and revision-checked pause/stage/resume exist, plus an in-process pause/drain lease. Latest Bun suite: 60 tests, 314 assertions. Python: 66 tests. SDK/application dependency-chain strict typing now passes after the management fetch wrapper preserved preconnect.

## Next work

1. Continue the retained operation, not another export/allocation. Reinspect live state and journals under operation.lock.
2. Target startup and managed SDK publication rehearsal now pass 11 checks. Target placement is persisted but paused; refresh its addresses before the next resume. The journal marks target_writes_may_exist, so never blindly switch to the stale source.
3. Unaffected source startup/resume now passes 16 gateway checks. Selected database remains fenced, has zero sessions and old Auth/REST stay stopped. Routine moved-target startup/stop now passes 10 checks, including stale-address replacement. Use target_runtime.py up/stop. Standalone commands retain staged guards. The normal dev.py supervisor now uses installation_runtime.py for combined startup after a 6 GiB/6 CPU ceiling, host reserve and pressure checks. Four environments plus management/console passed 14 simultaneous gateway checks and full shutdown. An idle-worker supervisor SIGKILL/restart rehearsal now passes nine checks. Active-operation death, durable reconciliation and sustained mixed traffic across both placements remain open.
4. Coordinate live gateway drain for graceful cutover. Current rehearsal stops services after maintenance and can abort admitted uploads.
5. Define recovery after controller death and rollback rules. Once target writes are accepted, routing back to a stale source is unsafe.

Realtime/functions/pooler/cron, production installer, upgrades, complete ownership transfer, multi-host coordination and sustained capacity remain outside completed scope.

## Latest supervisor crash checkpoint

Linux parent-death signaling binds direct API, worker and stage children to the foreground supervisor. [Nine live checks](evidence/supervisor-crash-checks.json) pass after the cleanup fix: API closure, idle worker lock release, restart, target application/Storage metadata digest preservation and source fencing. Docker containers deliberately survive the supervisor crash until explicit reconciliation. The final probe stopped all owned runtimes. Active provisioning, in-flight writes, daemon failure and power loss were not tested.

Adversarial review found a pre-existing process-group identity race and a probe HTTP readiness race. Both are fixed. Cleanup now observes child exit with WNOWAIT, retains its owned leader until all group signals finish, and never signals a group from an already-reaped leader. Production callers preserve exclusive ownership of child waiting. Two regression tests failed before the fix and now pass. Readiness requires HTTP 200 within a monotonic deadline. Reviewer parent_death_review found no remaining must-fix in this scope. Latest Python suite: 66 tests.

Next: controller death during active provisioning, with bounded descendant cleanup and operation reconciliation. The idle-worker result does not establish that guarantee.
