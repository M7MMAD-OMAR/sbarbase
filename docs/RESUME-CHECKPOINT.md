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

## Unresolved-effect inspection, latest checkpoint

`/usr/bin/python3 lab/inspect-provisioning.py` now acquires existing locks independently and reports sanitized receipt/witness/catalog/Docker evidence. It never mutates logical runtime state or authorizes replay. Read PROVISIONING-INSPECTION.md for interpretation and SQLite sidecar limitations.

All 82 Python tests pass. Seven live checks observe 19 owned containers, all stopped, no pending receipt, and unchanged catalog/journal/endpoint/native-witness hashes. The live fixture did not run PostgreSQL queries; the bounded metadata-query branch and pending evidence combinations have isolated coverage. Adversarial review corrected strict version validation and unobserved-owned-source wording.

Next: stage-specific recovery for partial effects without a native witness. Do not treat this observation report as permission to clear a receipt or replay a command.

## Documentation handoff and unfinished preflight recovery

The current entry point is HANDOFF.md, with saved diagrams, decision rationale, research links and remaining gates. PREFLIGHT-RECOVERY-WIP.md records the uncommitted stage protocol, exact source files, targeted test evidence and missing review/integration. Preserve that working tree when switching assistants. This documentation update does not certify or complete the implementation. No new live runtime check was performed for the handoff.

## Bounded preflight recovery, latest checkpoint

Read PREFLIGHT-RECOVERY.md. New native stage records allow exact pre-mutation recovery under fresh worker/effect ownership and the operation lock, with at most two automatic requeues. Later stages remain blocked without native completion. Review exposed and fixed retained-credential admission bypass and startup provisioning of reservations before authorization. Startup now resumes only published environments.

Validation: 87 Python tests, 73 Bun tests/408 assertions, strict worker/receipt types and 13 live known-refusal integration checks. Temporary native SIGKILL tests cover both sides of the stage boundary, not a full active-supervisor crash. No new runtime allocated; all owned runtimes stopped and receipt consumed after the live check. Next: stage-aware read-only inspection and a full active-provisioning supervisor crash rehearsal.

## Active preflight supervisor crash, latest checkpoint

Read docs/ACTIVE-PREFLIGHT-CRASH.md. Twenty-five live checks pass through actual supervisor SIGKILL with the native provisioner paused after durable preflight. Fresh startup requeues the exact claim once; the next attempt is capacity-refused with a matching native witness. No runtime allocation or credential change. Four existing environments respond, then all owned runtimes stop. No pending receipt remains. The fixture has consumed one lifetime automatic preflight requeue; do not reset history.

The inspector now reports sanitized exact stage evidence and recommends fresh bounded evaluation only with a missing native witness and current matching claim. All 88 Python tests pass; unchanged Bun checkpoint is 73 tests/408 assertions. Review added ownership-aware cleanup and capacity preconditions before the live run. Later-stage effects and arbitrary crash timing remain unproven. Next design isolated database/service fault fixtures and explicit reconciliation, preserving all retained environments.

## Partial database interruption and closed bootstrap, latest checkpoint

Read docs/PARTIAL-DATABASE-CRASH.md. A disposable pinned PostgreSQL 17 container exercised actual provision_environment SQL at roles, database, permissions and injected transaction failure. Fifty-nine checks pass: partial SQL persists, pending receipts and exact claims remain unchanged, automatic replay is blocked, scoped access works after permissions, and neighbor data remains unchanged. The isolated container was removed by exact identity; retained volumes/catalogs were not used by this probe.

Measured default PUBLIC CONNECT before the permission step motivated hardening: CREATE DATABASE now starts with ALLOW_CONNECTIONS false. Revoke/grant/reopen commit together, only for a database created by that invocation. Existing closed databases are refused before writes. Two regression tests fail against the previous bootstrap; all 91 Python tests pass now. Adversarial review found no remaining must-fix in this scope. The separate retained integration passes 13 checks and stops all owned runtimes. Bun code is unchanged at the recorded 73 tests/408 assertions.

This is interruption between completed SQL calls, not a daemon failure or in-flight query cancellation guarantee. New full Supabase provisioning after this change remains a separate integration gate. Next establish explicit database-stage reconciliation and disposable upstream-distribution coverage before permitting recovery of partial allocations. Do not un-fence the moved source or clear an unknown receipt.

## Upstream SQL boundary and fresh services, latest checkpoint

The partial-database probe now supports an explicit upstream profile with the pinned Supabase PostgreSQL distribution. Sixty-five checks pass there; 64 pass on the component profile. Each verifies unprivileged neighbors and initially absent fixtures. The upstream profile uses 1 GiB memory, 1 CPU, 512 MiB tmpfs and 4 GiB required host headroom, without network or published ports. Both remove only their exact disposable resource.

Separately, fresh two-environment original Auth/REST/shared Storage integration passes 122 checks after the closed-bootstrap change. Sanitized evidence is docs/evidence/upstream-closed-bootstrap-checks.json. Probe containers/networks are removed, and retained runtime state is unchanged by these experiments. No new unit-test claim: the prior full-suite checkpoint remains 91 Python and 73 Bun tests/408 assertions.

Adversarial review found no remaining must-fix in profile implementation. It rejected a proposed single-control-database advisory lock as whole-operation fencing: advisory locks are database-local. Read docs/DATABASE-OPERATION-FENCING-DESIGN.md. Next prove a same-database SQL revocation barrier and explicitly handle target-database statements before enabling any database-stage recovery. No such SQL barrier is implemented yet; unknown partial effects remain blocked.

## Database-local SQL revocation prototype, latest checkpoint

Read docs/SQL-OPERATION-FENCE.md. Experimental scripts and a disposable upstream probe now prove a same-database token revocation barrier: active SQL completes before queued revocation; delayed old SQL rechecks after the lock and fails; cancelled revocation leaves authority active. Thirty-one live checks cover tombstones, stale identities, private registry access despite default grants, reconnects and CREATE DATABASE. Identical lock keys in separate databases are explicitly shown not to exclude each other.

The full Python checkpoint is 94 tests; unchanged Bun checkpoint is 73 tests/408 assertions. Exact disposable cleanup passed. No retained runtime or catalog mutation. This is not integrated into Runtime.sql or worker recovery, and no operator revoke endpoint exists. Next design and test target-database barriers and registration/coordinator crash order before claiming whole-operation fencing or enabling database-stage recovery.

## Sequential SQL revocation and handoff, latest checkpoint

Read docs/SQL-PAIR-REVOCATION.md. The experimental coordinator passes 28 upstream live checks, including actual SIGKILL between control and target commits, target writes during the gap, retry, tombstones, absent/closed targets and replacement detection. Registry bootstrap is now atomic with privilege revocation; the separate same-database probe passes 34 checks. All 98 Python tests pass; recorded unchanged Bun checkpoint remains 73 tests/408 assertions. Exact disposable cleanup passed.

These prototypes are not integrated into runtime SQL or worker recovery and do not authorize replay. Next inventory and guard runtime SQL effects, then address services/Storage and durable reconciliation. Keep unknown later-stage receipts blocked. HANDOFF.md remains the concise entry point, with decisions, research, saved diagrams and a continuation prompt for either assistant.

## Runtime mutation audit and generation counterexample, latest checkpoint

Read docs/PROVISIONING-MUTATION-MAP.md before integration. Independent review found that an absent-target barrier cannot stop delayed old registration after a newer claim creates the database. The real upstream pair probe now reproduces this counterexample among 29 checks. It remains unresolved; do not treat the count as a complete safety result. HBA file writes and service-driven migrations are additional unguarded boundaries.

A separate live failing regression exposed guard lock output contaminating scalar query results. DO/PERFORM now suppresses guard rows; the same-database probe passes 35 checks. Both disposable probes removed their exact containers. No retained runtime changes or new replay permissions. Next implement generation-bound target admission before connecting SQL evidence to recovery.

## Bound target registration, latest checkpoint

The isolated register_target coordinator now acquires target metadata through the exact active control guard, refuses absent/closed targets and pins cluster/OID checks before target bootstrap. Real delayed dispatch tests reject revocation and DROP/CREATE after binding. The pair probe passes 37 checks, including the retained deliberate counterexample for unsafe low-level registration. All 100 Python tests pass. Read SQL-PAIR-REVOCATION.md for assumptions and exclusions.

Not integrated into runtime provisioning. Next build the explicit guarded executor and target registration transition, separating routine resume from creation and moving HBA out of the SQL-only boundary. Service migration, Storage and durable recovery remain open. OID reuse and restored clones are not covered by this prototype.

## Actual provisioning SQL adapter, latest checkpoint

Read docs/GUARDED-PROVISIONING-SQL.md. The scoped executor runs real run.provision_environment SQL in a disposable upstream fixture with control and target identity pins, rejects writes after revocation and refuses reuse after any uncertain failure/interruption. A native unterminated SELECT exposed a syntax error; explicit query separation fixes it, including trailing comments. Review corrected BaseException poisoning.

The pair probe now passes 43 checks; all 104 Python tests pass. Exact disposable cleanup passed and no retained runtime was changed. Next integrate the full durable SQL path with exact receipt identities and explicit resume semantics. HBA and service migrations remain separate unresolved effects. The guard does not authorize partial replay.

## Full native SQL boundary extraction, latest checkpoint

Runtime.provision_database now collects the complete durable SQL path and propagates the injected executor into REST deadline checks/writes. The real disposable upstream fixture executes this whole method through GuardedSQL, including Storage setup and limits. Forty-five live checks and 106 Python tests pass. Independent review found no missing SQL or behavior drift.

The services write-ahead marker now precedes shared HBA overwrite/reload; a regression confirms failed marker persistence prevents HBA changes. Normal provisioning still defaults to the original executor. Next bind worker receipt identity and split resume semantics before enabling runtime fencing. No retained runtime mutation occurred.

## Published resume separated from provisioning, latest checkpoint

Read docs/PUBLISHED-ENVIRONMENT-RESUME.md. Routine published startup now validates existing state and resumes retained containers without rerunning native environment SQL, rewriting REST deadlines or creating missing Storage tenants. Missing containers cannot fall back to creation. Outer shared infrastructure startup and service-owned migrations still have effects.

All 110 Python tests pass. The retained combined supervisor passes 13 receipt integration checks, including all four environment routes, exact capacity refusal without allocation, unchanged credential reservation file and complete owned shutdown. No pending receipt remains. Independent review found no new must-fix. Next bind exact worker receipt identities to guarded SQL for creation; unknown later-stage replay remains blocked.

## Receipt-bound durable SQL, latest checkpoint

Read docs/RECEIPT-BOUND-SQL.md. Durable provisioning now validates the exact worker receipt, durable preflight/database stage and current running catalog claim, then routes native environment SQL through GuardedSQL. Close pins captured control/target identities and retires both tokens before the services marker. Direct native provisioning without a receipt is refused. Legacy durable-check now invokes the worker rather than manually bypassing receipts.

All 115 Python tests and 51 disposable upstream checks pass. Actual Runtime.provision wiring uses real temporary records/SQL with simulated host ownership/admission and stops before HBA. The separate retained supervisor rehearsal passes 13 checks, with all four environments responding, no new allocation, no pending receipt and all owned runtimes stopped. Review found no remaining must-fix.

Next verify a fresh full worker-driven Auth/REST/Storage lifecycle with this guard, then address HBA/service-effect recovery. No generic partial replay is authorized.

## Fresh real worker and services, latest checkpoint

Read docs/FRESH-WORKER-LIFECYCLE.md. A private source snapshot with only literal Docker identity replacements now runs the real worker, guardian ownership, receipts, catalog settlement and guarded native SQL through original Auth/REST/Storage. Fifty-six live checks pass, including actual SDK signup, owner data access, rejected wrong-owner writes, hidden second-user reads and private Storage isolation. Both SQL tokens retire before service startup and the native witness binds the exact settled claim.

Five fixture containers have combined configured ceilings of 2304 MiB/2.25 CPUs, require 6 GiB initial host headroom and publish no container ports. Fresh worker/effect/operation locks precede exact-ID cleanup; all fixture containers/volumes/network are removed. Private diagnostic snapshots remain ignored. Retained installation resources are not used. Strict SDK probe types pass; prior Python checkpoint115 and unchanged Bun73/408 remain.

The fresh worker lifecycle gate is now covered. Next address shared HBA and service-effect interruption/reconciliation; unknown later-stage replay remains blocked. This is not 10/100 capacity evidence or arbitrary crash recovery.

## Complete-file HBA replacement, latest checkpoint

Read docs/ATOMIC-HBA-REPLACEMENT.md. Shared HBA updates now validate stdin byte length and SHA256 in a unique same-directory temporary file, preserve metadata, sync and rename before the separate reload. This closes producer EOF truncation; rename alone was insufficient. Commands are verified against the pinned image's BusyBox tools.

Sixteen upstream fault checks pass, including legacy truncation, rejected short/corrupt input and helper SIGKILL before/after rename. The fresh actual worker/SDK lifecycle passes 57 checks after the change. Full Python remains115 passing tests. Exact disposable cleanup passed and no retained runtime was used for these probes. Independent review found no must-fix in this scope.

Next fence stale complete HBA writers and define activation/reconciliation semantics. Post-rename failure remains uncertain; no automatic later-stage replay or power-loss guarantee is introduced.

## Immutable HBA revisions, latest checkpoint

Read docs/ATOMIC-HBA-REPLACEMENT.md. Managed HBA writes now capture full Docker CID and expected whole-file hash into a frozen request, add a fresh UUID header, and compare under a stable container-local nonblocking flock before atomic replacement. Requests are never recaptured/retried after failure. Twenty-six real checks cover competing prepared writes, replay rejection, identical-rule generations and lock-holder death as well as truncation.

This fences already-prepared stale writes, not an old operation preparing anew after a newer update. Next bind full configuration-operation authority and reconcile activation; unknown service-stage replay stays blocked. Administrator lock replacement/filesystem rollback remain excluded.

Validation: all 118 Python tests and the fresh 57-check real worker/SDK lifecycle pass. Exact isolated cleanup passed; retained installation was not used. Independent review found no must-fix within prepared-request revision protection.

## HBA activation and existing-session boundary, latest checkpoint

The upstream HBA probe passes36 checks: restrictive file publication alone does not affect new connections; after reload fresh connections are rejected, but an existing session remains usable. Invalid-file parser errors coexist with a true reload-signal acknowledgment. Runtime now checks parser errors and signal success explicitly. These are not full activation or session-revocation guarantees.

All121 Python tests pass. Read docs/ATOMIC-HBA-REPLACEMENT.md and the reviewed, unimplemented HBA-OPERATION-AUTHORITY-DESIGN.md. Next implement immutable operation journals and registry/tombstone semantics only after specifying startup and container-generation reconciliation. Unknown services-stage replay stays blocked.

Fresh real worker/SDK lifecycle also passes57 checks after reload validation, with exact isolated cleanup. Independent review found no runtime must-fix and tightened invalid-reload evidence wording: immediate continued rejection does not confirm SIGHUP processing.

## Documentation handoff checkpoint

The concise entry point is now docs/START-HERE.md, linking decisions, reasons, research, saved visuals, measured evidence and open gates. Verified implementation baseline remains155e230. lab/hba_authority.py was started but has not been tested or integrated; preserve and review it as a draft. No runtime work or new validation run occurred during this documentation update. Continue with one active writer in the same checkout, whether Codex or Hermes.
