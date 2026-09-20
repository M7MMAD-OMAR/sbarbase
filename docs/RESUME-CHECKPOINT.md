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
- Routing: durable maintenance and revision-checked pause/stage/resume exist, plus an in-process pause/drain lease. Latest Bun suite: 60 tests, 314 assertions. Python: 68 tests. SDK/application dependency-chain strict typing now passes after the management fetch wrapper preserved preconnect.

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

## Direct provisioning effect ownership

[Inherited worker lock](WORKER-EFFECT-OWNERSHIP.md) now prevents a new worker from taking ownership while the previous worker's direct effect remains alive. A real Bun/Python regression failed before the fix. Full Python suite: 68 tests; Bun: 60 tests, 314 assertions; strict worker types pass. Wrong-descriptor and startup-failure cases fail closed without retaining a permanent lock. Adversarial review found no must-fix in this limited scope. No runtime allocation or catalog mutation was used.

Next: direct provisioner death can still orphan a Docker CLI or leave daemon-side effects in progress. Establish bounded local descendant containment and explicit effect-state reconciliation before declaring active provisioning crash recovery complete.

## Provisioning replay gate, latest checkpoint

[Durable effect receipts](PROVISIONING-RECEIPTS.md) are published before effects and settled before runtime startup under the worker lock. Unknown outcomes block automatic recovery. Successful or proven capacity-refused outcomes settle the exact claim; durable outcome rows make consumption idempotent even after an API retry. Startup/provision guards close direct runtime bypasses.

Latest evidence: 72 Python tests, 65 Bun tests with 354 assertions, strict worker types and 11 live known-refusal integration checks. The existing rejected-capacity metadata was retried, with no new runtime allocation; all owned runtimes stopped afterward. Reviewer fixes for startup bypass and historical-receipt retry race are included. No pending receipt remains after the live check.

Next: build explicit reconciliation for a pending receipt and bounded descendant containment. Do not clear a receipt based on age or process disappearance. Earlier automatic interrupted-job recovery evidence does not establish recovery under this new uncertainty gate. Inspect legacy interrupted jobs without receipts explicitly.

## Effect guardian, latest checkpoint

A parent-bound guardian in its own session supervises each effect in a separate owned group. Worker shutdown forwards SIGTERM; worker death triggers the parent binding. Default 180-second deadline, TERM/KILL cleanup with unreaped leader identity, and retained worker ownership now cover catchable cancellation. Interrupted effects remain pending even if the child exits zero. See EFFECT-GUARDIAN.md for precise limits.

Latest evidence: 74 Python tests, 65 Bun tests/354 assertions, strict worker types, independent review and another 11-check real known-refusal integration pass. Test fixtures observe non-cooperative grandchild termination; production cleanup only waits for the direct leader. No new runtime allocated; all owned containers stopped. No pending receipt remains from the live check.

Next: explicit pending-receipt inspection and Docker/database outcome reconciliation. Do not infer rollback from process termination. Escaped groups, guardian SIGKILL and uninterruptible kernel work remain outside the local containment guarantee.

## Native outcome recovery, latest checkpoint

Native provisioners now publish immutable, fsynced outcome witnesses. Replacement-worker startup can settle a matching known outcome without replay; a surviving worker must exit before native recovery. Every worker fresh-opens effect.lock and retains it through guardians/native children. Read NATIVE-OUTCOME-RECOVERY.md.

The first live verification exposed installed Bun descriptor swap aliasing and recorded runtime_failed before any receipt was published. Source descriptors are now duplicated above child slots; an actual inode-mapping regression passes. The same failed fixture was explicitly retried and now returns capacity_exceeded with an exact native witness.

Latest evidence: 76 Python tests, 67 Bun tests/377 assertions, strict types and 12 real known-refusal integration checks. A temporary native-success fixture survives SIGKILL after witness persistence without replay. All owned runtimes stopped; current refused fixture has a settled failure, no pending receipt, and its private native witness is retained. Unknown partial effects remain blocked. Next build explicit read-only inspection and stage-specific reconciliation for outcomes lacking such a witness.
