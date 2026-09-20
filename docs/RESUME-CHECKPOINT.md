# Resume checkpoint

Current state after the chronological cutover rehearsal, 2026-09-20. This file supersedes older next-step paragraphs. Read HANDOFF.md and PROJECT.md for broader product scope.

## Decisions

Keep original Supabase services and PostgreSQL. Hierarchy: installation > organization > project > environment. Ownership is separate from runtime placement. Shared PostgreSQL with separate databases remains an experimental candidate; no production certification or 10/100-project capacity claim exists.

## Actual runtime state

- Source and both recovery targets are stopped with all volumes retained.
- All four ready source runtime routes are in persistent maintenance.
- Selected source database has ALLOW_CONNECTIONS=false and its three scoped service logins have NOLOGIN. Do not clear these automatically.
- Current target was restored from a new, fenced export. Previous target descriptor is retained under `.lab/upstream/recovery-target-history/`, with its path recorded in the operation journal.
- `.lab/upstream/cutover-operation.json` phase is `target-services-verified-routing-paused`. Current target descriptor: `.lab/upstream/recovery-target.json`. New encrypted export pointer: `.lab/upstream/recovery-latest.json`.
- None of these private paths or credentials belong in the development ZIP. Load programmatically without printing secrets.

## Verified evidence

- New export: 34 checks, including pre-export signed URL capture, service-login fence and complete database fence afterward.
- Fresh target: 49 database checks, 11 Auth/REST checks, nine Storage checks and 14 end-user Storage checks. Original identity/password, table contents, scoped roles, files/xattrs, signing material and the unchanged URL issued before export work on the target.
- Prior recovery failure work: backend termination rolls back pg_restore and the same dump restores cleanly; cleanup failures do not skip DB shutdown or falsely report success. Controller SIGKILL and automatic stage resume remain unproven.
- Routing: durable maintenance and revision-checked pause/stage/resume exist, plus an in-process pause/drain lease. Latest Bun suite: 60 tests, 314 assertions. Python: 51 tests. Broader application strict typing has an existing custom-fetch/preconnect mismatch in control/auth.ts; targeted routing types pass.

## Next work

1. Continue the retained operation, not another export/allocation. Reinspect live state and journals under operation.lock.
2. Build controlled target startup, publish its current endpoints while maintenance remains set, then verify real managed SDK traffic before declaring cutover.
3. Restore unaffected source environments deliberately while preserving the selected source fence. Shared source Storage startup must not reopen selected database writes.
4. Coordinate live gateway drain for graceful cutover. Current rehearsal stops services after maintenance and can abort admitted uploads.
5. Define recovery after controller death and rollback rules. Once target writes are accepted, routing back to a stale source is unsafe.

Realtime/functions/pooler/cron, production installer, upgrades, complete ownership transfer, multi-host coordination and sustained capacity remain outside completed scope.
