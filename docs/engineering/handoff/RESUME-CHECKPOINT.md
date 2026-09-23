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

## Isolated HBA authority registry checkpoint

The draft is now an isolated tested prototype. Eight real host shell tests and 13 pinned-image filesystem checks pass. Tombstones, whole-registry CAS and same-lock authority/HBA checks reject delayed old operations. Missing initialization markers now refuse updates; a real failing test found and fixed an AND-list/set-e mistake. Registry parsing rejects duplicate keys. No runtime integration or PostgreSQL activation was performed. The disposable probe was removed; retained installation resources were not used.

Read HBA-OPERATION-AUTHORITY-DESIGN.md for remaining requirements: exact host journal identity, startup/container-generation reconciliation, interruption tests and migration of every managed writer. This supersedes the prior untested-draft status, not the unresolved recovery gates.

All 129 Python tests pass. Adversarial review found an uncertain-container-create cleanup gap in the new probe; a private cidfile now recovers the captured ID after lost run acknowledgment, with ID/name/owner/image checks before cleanup.

## HBA registry interruption checkpoint

The pinned-image authority probe now passes 17 checks, including actual helper SIGKILL immediately before and after registry rename. Before rename, the complete active version remains; after rename, the complete revoked version remains. The killed helper releases its lock, a new operation registers, and the persisted tombstone refuses resurrection. Failed revocation before publication is not cancellation.

This uses a disposable shell container, with no running PostgreSQL and no retained resources. It does not test machine power loss, host journal recovery, registry cloning or startup integration. Next persist exact host operation journals before dispatch and define reconciliation before integrating runtime writers. The unchanged full Python checkpoint remains 129 tests.

## Immutable host HBA journal checkpoint

lab/hba_journal.py now creates a fixed-name exclusive private journal and syncs file/parent before one registry registration dispatch. Exact token, container, generation, original registry snapshot and prepared content/identity stay discoverable after an uncertain acknowledgment. Existing or invalid journals block replacement. Read-only inspection reports observed authority, never application or activation. An absent token does not exclude delayed registration.

All137 Python tests and 20 pinned-image checks pass. No runtime wiring or retained installation mutation occurred. Next test host process interruption around dispatch, bind live ownership and receipt/startup identities, and design conservative settlement/container-generation reconciliation before integrating managed writers. Structural identity validation alone does not confer authority. No automatic replay or journal deletion is implemented.

## Host journal SIGKILL checkpoint

The isolated authority probe now passes 30 checks. A real host child is stopped and killed after fsynced journal publication before register dispatch, and after register return before the outer call completes. Fresh reads preserve exact token/content/original registry identity and observe absent versus active authority respectively. Both interrupted journals reject replacement before dispatch. Explicit fixture revocation leaves tombstones; exact container cleanup passes.

No retained runtime mutation or production recovery was performed. The unchanged Python suite checkpoint is 137. Next bind journal operations to verified host ownership and exact live startup/worker identity, then settle authority conservatively before wiring managed writers. In-flight Docker RPC interruption and container-generation replacement remain separate gates.

## Worker ownership entry gate checkpoint

The isolated hba_ownership.begin_worker now requires distinct matching worker/effect/operation lock inodes and real exclusive flock ownership, then validates the exact services receipt/stage/current catalog claim. The journal receives derived worker identity. A dedicated hbaProtocol: 1 receipt field rejects legacy records; the real guardian does not yet emit it and no runtime writer uses this helper.

Nine real subprocess/flock/SQLite tests cover success and stale/missing/competing/aliased/symlink/legacy rejection before journal publication or dispatch. All 146 Python tests pass. No retained resources were changed. Existing image evidence remains 30 checks, not a combined real worker/Docker proof. Next design startup ownership and conservative settlement, then exercise the new protocol through a disposable real worker before all-writer integration. Inherited ownership is not fresh recovery ownership.

## Configured HBA database target checkpoint

The worker entry gate now requires an immutable captured target from trusted name/owner/image policy. It rechecks only the captured full Docker ID and matches prepared/snapshot IDs before journal creation. Wrong target metadata, stopped state or another container are refused without name-based recapture. All 150 Python tests and 34 pinned-image checks pass, with independent review finding no must-fix in the bounded scope. Exact disposable cleanup passed; retained resources were untouched.

Next complete startup authority and conservative settlement/container-generation rules, then wire a disposable real worker and all managed writers. The target gate supplies no database-readiness or automatic recovery guarantee, and expected installation policy must remain trusted.

## Startup ownership gate checkpoint

The isolated startup context now acquires worker/effect/operation ownership, with fresh effect/operation descriptions and optional explicitly inherited supervisor worker ownership. Closing never unlocks the supervisor's shared flock. Pending or unreadable records block startup, and one context permits only one attempt. Identity is generated under ownership and cannot be used after exit or across fork. Exact descriptor count and distinctness are required.

Ten local tests cover actual flocks/fork and negative paths. The pinned-image probe now performs its first registration through this context and refuses a fresh startup while its journal remains. Full Python 160 and image 36 checks pass. No actual supervisor integration or retained resource changes. Next implement conservative reconciliation that retires exact authority under fresh ownership without silently replaying HBA writes, then address generation initialization and all-writer wiring.

## Exact-token retirement checkpoint

hba_reconcile.retire now uses fresh independent existing worker/effect/operation locks, validates the captured target and generation, and retires only the journal's exact token/binding. It rechecks the tombstone before reporting a point-in-time HBA digest observation. Missing/conflicting authority refuses; lost acknowledgments propagate. A later call can confirm an already persisted tombstone without repeating the update.

All 166 Python tests and 42 live checks pass. Real lost-ack and host SIGKILL fixtures confirm retirement, unchanged pending journals, blocked old permits and rejected delayed registration. No HBA apply/reload, job settlement, receipt/catalog mutation, journal removal or startup permission occurs. Independent review found no must-fix. Next implement explicit generation initialization and durable settlement rules before actual supervisor/worker and all-writer integration. Retained resources remain untouched.

## Immutable generation binding checkpoint

The isolated protocol now persists hba-generation.json before backend initialization and requires its exact target/generation in startup, worker and retirement paths. Startup initialization and begin are separate one-shot allowances. Uncertain initialization retains the pin; read_existing only observes established state. Missing registry or replaced container cannot trigger automatic reset.

All 174 Python tests and 47 live checks pass, including lost real INIT acknowledgment and read-only generation recovery. Independent review found no must-fix. No retained runtime was changed. Next define durable operation outcomes and journal settlement, then exercise real worker/supervisor wiring after quiescing legacy writers. Container-generation migration and simultaneous host/backend rollback protection remain unresolved; container recreation intentionally blocks.

## Baseline-only cancellation settlement checkpoint

hba_settlement.cancel_baseline now archives an exact retired HBA attempt only when current bytes match the distinct original baseline. Locks span retirement through private deterministic archive durability, exact raw journal recheck, unlink and state-directory sync. Existing archives must match and are resynced; partial or changed artifacts block release. Changed/desired/ambiguous HBA content stays pending. Worker receipts/catalog are untouched and no job success or activation is inferred.

All 182 Python tests and 56 image checks pass. Real host-SIGKILL cases archive their baseline-only attempt without changing HBA; a changed-file case refuses. Next implement successful applied-file/reload completion with durable evidence and conservative crash handling, then integrate actual supervisor/worker and all managed writers after quiescing legacy effects. Retained installation resources were not used.

## Native applied HBA witness checkpoint

hba_apply.execute now persists an exclusive attempt before actual publication, verifies desired bytes, checks parser errors and reload signal acknowledgment on the captured CID, rechecks exact authority/journal and writes a durable completion witness with activation unknown. Startup execution requires its originating live context; worker execution revalidates the services receipt. Failed/uncertain attempts stay blocked without automatic replay.

Full Python 188 and a separate real PostgreSQL 17-check applied-path probe pass. Valid and invalid HBA both retain journals; only the valid path produces the exact witness. Prior registry 56 evidence is separate and unchanged. Independent review found no must-fix. Next validate and settle successful witnesses conservatively before actual supervisor/guardian/all-writer integration. No retained runtime mutation occurred.

## Successful applied-witness settlement checkpoint

Strict private attempt/completion readers now bind exact JSON types, phases, full journal/raw digest and desired content before successful HBA-slot settlement. Under fresh locks, exact authority retires, desired bytes are rechecked, deterministic witness-bearing archive is synced before journal unlink. Worker receipt/catalog and activation remain outside this completion claim.

All 196 Python tests and 24 real PostgreSQL checks pass. The probe kills an actual host child only after normal completion publication returns, then fresh recovery settles it without apply or SQL calls. Missing/invalid proof and content drift stay blocked. Archive retry and final sync uncertainty are covered. Independent review found no must-fix.

Next implement normal completion under the originating live owner, because fresh recovery locks intentionally refuse while the worker/startup owner still holds them. Then wire actual supervisor/guardian and repeated managed HBA writers after protocol rollout and quiescing legacy effects. Retained installation resources were untouched; prior registry-only 56 evidence remains separate.


## Live-owner HBA completion scaffold, 2026-09-20

Added `hba_settlement.complete_owned` using the same originating owner validation as publication, without fresh recovery lock acquisition. Recovery and live completion share strict evidence, retirement and archive handling. Two added tests cover completion under held startup locks without another apply/reload and rejection of expired startup ownership before retirement. Full Python suite: 198 tests passed. This remains an isolated prototype. Next: adversarial worker/descriptor cases, real PostgreSQL live completion probe and independent review before runtime integration. Retained containers were not changed.


## Live completion validated and duplicate startup write removed, 2026-09-20

Validated originating worker completion with actual locks and SQLite claims, rejecting stale claims, missing/legacy receipts and competing descriptors before backend retirement. Fresh/expired startup contexts are also refused. All 203 Python tests pass. The real pinned PostgreSQL HBA probe passes 26 checks, independently testing all three held locks; host-death recovery remains covered. Independent review found no helper bypass and prompted the stronger per-lock check.

Removed the redundant `Runtime.start` HBA call after `management()` has already published the complete inventory. The real fresh worker/SDK lifecycle passes 57 checks with exact disposable cleanup. Thus current startup needs one operation, not reusable one-shot contexts. See the concrete integration map at the end of HBA-OPERATION-AUTHORITY-DESIGN.md. Next wire explicit ownership and receipt protocol into source writers, with an explicit legacy-adoption gate. The new authority protocol remains unintegrated; retained source/targets were not started or changed.


## Source HBA runtime integration, 2026-09-20

The source now uses SourceHBA for startup and services-stage publication, completion and retirement. Startup entry points hold ordered ownership; dev forwards supervisor worker ownership. New guardian receipts emit hbaProtocol1, and native preflight validates it before credentials/database effects. Existing pinned restart reads the same generation; legacy missing-pin startup refuses. Docker inspection failure needs successful absence verification and fresh initialization additionally requires exact positive creation evidence. Pending HBA journals block worker startup before receipt recovery.

217 Python tests and 73 Bun tests/408 assertions pass. The expanded fresh worker/parent-bound installation restart passes 76 real checks, including exact HBA archives, revoked tokens, missing-pin refusal, same generation after restart, SDK isolation and cleanup. Independent review found no remaining must-fix in source HBA scope. See SOURCE-HBA-INTEGRATION.md for exact guarantees and exclusions.

The retained legacy source and both recovery targets were not changed. Legacy source startup is intentionally blocked pending quiesced adoption. The historical durable-check.ts recreation probe refuses before catalog/Docker effects until explicit generation migration exists. Next perform actual worker HBA interruption testing, implement adoption/migration, then cover recovery-target writers. Do not reset pins or delete authority to bypass these gates.

Read-only final inventory verified all 11 source and 8 recovery-target containers remain stopped, and the retained source has no HBA generation pin. No adoption or retained restart was performed.


## Actual native worker HBA interruption, 2026-09-20

Both fresh-worker-check.py --hba-crash after-intent and --hba-crash after-witness pass 90 checks, each including the overlapping healthy 76-check baseline. The private profiling hook matches exact native function return and services receipt identity; the guardian confirms its reaped child's SIGKILL status. Fresh ownership, exact active authority, pending receipt/claim preservation, no HBA apply/SQL during reconciliation and specific services-stage refusal afterward are verified. No production execution code changed. Full Python suite still passes 217 tests. Independent review found no must-fix in the scoped experiment. See WORKER-HBA-CRASH.md.

The retained source and recovery targets were not used. Next implement the durable adoption operation outlined in HBA-LEGACY-ADOPTION-DESIGN.md and test disposable legacy fixtures first. Review identified the operational quiescence assumption, pre-start durable intent, exact IDs/generation, preserved HBA content and crash-safe stopped-state completion requirements. Same-CID adoption must not claim to fence raw legacy writers or all queued daemon requests.


## Durable legacy HBA adoption implemented, 2026-09-20

Read docs/HBA-LEGACY-ADOPTION.md. `lab/hba_adoption.py` implements the durable
adoption operation: exclusive fsynced intent capture of the stopped source
(exact CID, trusted policy, pgdata mount identity, stopped inventory), fresh
ownership via the startup gate, conflicting-pin refusal before start, one INIT
on the intent's exact generation, preserved HBA rules published through the
owned apply/reload pipeline, durable checkpoints separating database start,
generation initialization, owned HBA completion, exact stopped observation and
completion, and resume-only-from-checkpoints recovery. Stable-readiness waiting
covers the entrypoint's re-initialization restart window.

Evidence: 224 Python tests, 73 Bun tests/408 assertions, and a 35-check live
probe over four phases (healthy, adopter SIGKILL after database-started, after
hba-completed checkpoint, and after the durable witness with pending journal
recovery) on disposable pinned Supabase PostgreSQL containers:
docs/evidence/hba-adoption-crash-checks.json. Exact disposable cleanup passed;
retained source, recovery targets and volumes untouched.

Next: reconcile retained adoption (the legacy source still has no generation pin
and its startup still refuses), then recovery-target writers. Quiescence of
legacy host clients remains an explicit operational assumption; raw legacy
writers are not fenced.

### Adversarial review and fixes, 2026-09-20

Independent adversarial review (docs/reviews/legacy-adoption-review.md) found
five must-fix defects; all five are fixed with regression tests: resume past the
source-stopped checkpoint now performs no database work, a preexisting backend
authority marker refuses adoption before any pin is written, a pin with no
committed backend marker resolves by one same-generation INIT, checkpoint
existence and reads are strict (lstat for absence, O_NOFOLLOW plus owner, mode,
size and checksum for content), and the captured mount identity is revalidated
before start and after the final stop. Evidence after the fixes: 230 Python
tests, 73 Bun tests/408 assertions and a 44-check live probe over five phases,
including the source-stopped window:
docs/evidence/hba-adoption-crash-checks.json. The review worktree was merged
into main and removed.

## Retained source adopted, 2026-09-20 (Hermes)

The retained `sbarbase-durable-db` source is now adopted into HBA authority.
Only that container was started and stopped again; no application service and no
recovery target was started. The operation published a private intent in the
retained state directory, wrote immutable checkpoints, published the intent's
exact generation pin and published the preserved rules through the owned
apply/reload pipeline with exactly one fresh revision marker.

Verified from durable evidence only (docs/evidence/retained-source-adoption.json,
12 checks): the live file digest equals the recorded applied content, the
pre-adoption bytes are preserved byte for byte against the journal expected
digest, exactly one revision marker exists, inventory rules exist for every
catalog environment, no pending journal or worker effect remains, and the source
is stopped by exact identity. Runners: lab/adopt-retained.py (source|target) and
lab/verify_retained.py (source|target).

Explicit assumption: legacy host clients and queued Docker requests were
quiesced (all source containers stopped for hours, locks free, no receipt, no
journal). Raw legacy writers are still not fenced by this operation.

Next: the retained source now carries a generation pin, so its startup no longer
refuses on that gate. A source lifecycle rehearsal (start, four environment
routes, stop) is the natural next verification, followed by recovery-target
writers and container-generation migration.

## Recovery-target writers addressed, 2026-09-20 (Hermes)

Read [TARGET-HBA-WRITERS](TARGET-HBA-WRITERS.md). Every managed database
container now has its own authority state: the source uses the installation
state root, each recovery target uses `<installation state>/targets/<prefix>`
with its own locks, generation pin, journal and evidence. `lab/hba_runtime.TargetHBA`
is the same owned protocol plus `before_create`, which requires explicit
creation evidence, refuses a preexisting volume and refuses a pending journal.

The retained recovery-target database (`sbarbase-restore-d2f9e9f091e1-db`) was
adopted too: byte-for-byte rule preservation, one fresh revision marker, stopped
by exact identity, 12 verification checks in
docs/evidence/retained-target-adoption.json. The restore path
(`lab/recovery-restore-db.py`) now publishes through the owned writer instead of
a raw shell write. Fresh-target writer evidence: 20 live checks on a disposable
pinned PostgreSQL container, docs/evidence/target-hba-creation-checks.json.

Not done: a full live recovery restore re-run with the owned writer, conversion
of the storage check probe and the legacy bootstrap scripts, and
container-generation migration. The remaining raw writers are inventoried with
explicit dispositions in TARGET-HBA-WRITERS.md. The migration design (not
implemented) is in [CONTAINER-GENERATION-MIGRATION](CONTAINER-GENERATION-MIGRATION.md).

## Server deployment path written, 2026-09-20 (Hermes)

Read [SERVER-DEPLOYMENT](SERVER-DEPLOYMENT.md). `lab/install_server.py` provides
`check` (read-only preflight), `plan`, `install` and `smoke`; `deploy/sbarbase.service`
supervises the installation with an `ExecStartPre` preflight; the runbook covers
prerequisites, HTTPS termination, backup, upgrade per the upstream policy,
rollback and explicit limits. Preflight proof on this host: it reports exactly
one blocker, `Host headroom insufficient: 6898 MiB available, plan needs 8448
MiB`, plus the retained-installation actions and the historical-target note.
That blocker is a host capacity condition, not a code defect.

Suites after the change: 248 Python tests, 73 Bun tests/408 assertions.
Independent adversarial review of the new target and deployment code is
running; its findings will be recorded under docs/reviews/.

## Target placement rehearsal passed, 2026-09-20 (Hermes)

`lab/target_placement_rehearsal.py` passes 12 checks on the retained recovery
target after adoption: the target starts through its own lifecycle (which
health-checks Auth, REST and Storage and re-stages routing), Auth and REST
answer independently, routing is resumed with a persisted placement, the target
carries its adopted generation pin, and the lifecycle stop leaves no container
running with routing paused and the source untouched.
Evidence: docs/evidence/target-placement-rehearsal.json.

`lab/deployment_rehearsal.py` runs the whole deployment verification in one
command (preflight, install, supervisor, console and management reachability,
environment routes, supervised shutdown, no owned container left running). On
this host it records the preflight refusal, `Host headroom insufficient`,
without starting anything: docs/evidence/deployment-rehearsal.json.

## Reference TLS termination proven, 2026-09-20 (Hermes)

The repository now ships `deploy/console-tls-proxy.ts`, a TLS termination that
needs only Bun and a certificate, and `lab/tls_termination_check.py` proves it on
this host: fifteen checks (`docs/evidence/tls-termination.json`). A generated
self-signed certificate terminates in front of a stub upstream that serves the
real built console page through the same static layer the console uses, so the
page and its assets arrive over HTTPS byte for byte, a management route is
reachable through the proxy, `Strict-Transport-Security` and
`X-Content-Type-Options` are set, the original protocol is forwarded as https,
plain HTTP is answered with a 308 redirect, and the proxy refuses to start with a
group or world readable key, a non-loopback upstream, or no certificate argument.
Shutdown releases both ports.

The check also found a real trap: with certificates in play, an HTTP client that
follows redirects turns the redirect probe into a TLS failure, so the check
observes redirects instead of following them. What remains for a server: a public
certificate and whichever proxy the operator prefers (the checks state which
behaviour any replacement must keep).

## Supervised path proven under systemd, 2026-09-20 (Hermes)

`lab/supervised_run_check.py` writes a temporary systemd *user* unit that mirrors
the shipped unit's directives (`ExecStartPre` preflight gate, `ExecStart` the
supervisor, PATH, no restart), starts it with `systemctl --user`, probes the
console and the management realm, stops it, and removes the unit. All ten checks
pass (`docs/evidence/supervised-run.json`, 17:08). This proves the supervision
mechanics and the gate as systemd runs them; the shipped unit installed at
`/etc/systemd/system/sbarbase.service` remains for the server acceptance run,
which passes `--require-unit`.

The first attempt failed for a real reason worth remembering: under systemd the
unit reported the Docker daemon as unreachable, because this host's docker
context points at a Docker Desktop socket that does not exist and only the
interactive shell's `DOCKER_HOST` made the daemon visible. A service inherits
neither. Two fixes: the check forwards `DOCKER_HOST` and `DOCKER_CONTEXT` into the
temporary unit and records that it did, and the preflight now names the endpoint
it tried (`Docker daemon unreachable from this process (tried ...); a system
service must reach the socket its docker context resolves to`) instead of a bare
"unreachable". On a server whose context resolves to the native
`/var/run/docker.sock`, neither is needed.

## Full lifecycle rehearsal passed locally, 2026-09-20 (Hermes)

The complete source plus target rehearsal now passes on this host:
`docs/evidence/deployment-rehearsal.json`, ten of ten checks, 16:08:16 to
16:09:19 (63 s). Preflight passed, the built page was intact, the supervisor
started and owned the console (it needed a second attempt while the host was
under memory pressure), the console served the built page, the management
identity realm answered 200, all eight recorded environment routes answered 200,
fourteen combined gateway checks passed, the supervisor shut down cleanly with
exit 0, and no owned container was left running. The combined admission that the
same run exercised recorded 5888 MiB of container limits and 5.75 CPUs admitted
at 8943 MiB available, with the target started
(`docs/evidence/combined-runtime-admission.json`).

What made it possible, after several refusals caused by the shared host:

- the preflight states the real requirement (placement plus reserve plus the
  measured running-stage cost, 8731 MiB) and refuses before creating containers,
  so a doomed start no longer creates eleven containers and rolls back;
- the console build is reused when it is newer than every input, removing the
  few hundred MiB and the delay of rebuilding it on every start;
- the rehearsal retries a pressure-refused start (`--attempts`) and records each
  attempt's reason;
- the systemd unit check is informational unless the run is a server acceptance
  run (`--require-unit`, which `deploy/server-acceptance.sh` passes), because a
  development host runs the supervisor directly.

Still not proven on this host: a run with `sbarbase.service` installed and
enabled, HTTPS termination, and the install path from an empty host. Those are
what the server acceptance run adds.

## Combined rehearsal headroom correction, 2026-09-20 (Hermes)

The first full rehearsal that got past the preflight exposed a real defect: it
started the source placement, then refused at the combined admission and rolled
back, because the preflight figure (5888 MiB of container limits plus a 2560 MiB
reserve) is measured on an idle host while the combined admission measures the
host with the source stage already running. The preflight therefore understated
what a combined start needs, and the failure landed after eleven containers had
been created.

Fixes, all tested:

- `lab/installation_runtime.py` samples the running source stage with
  `docker stats` immediately before the combined check and records it in
  `docs/evidence/source-stage-footprint.json`. Measured on the retained
  nine-container source layout: 283 MiB total (database 96, storage 116,
  management Auth 14, per-environment Auth and REST 8 to 10 each).
- `lab/install_server.py` adds that measurement to the requirement on an
  installation that has been moved and prints the composition, so a refusal
  names every term: `5888 MiB placement + 2560 MiB reserve + 283 MiB measured
  for the running source stage` = 8731 MiB. Without a measurement it says so
  instead of implying the figure is complete.
- Startup refusals now state their cause: `lab/installation_runtime.py` writes
  the traceback to `.lab/upstream/diagnostics/` (0700 directory, 0600 file) and
  prints the path, `lab/dev.py` names the failing stage, and
  `lab/deployment_rehearsal.py` captures the supervisor to
  `.lab/upstream/rehearsal-supervisor.log` and records its tail in a failed
  check instead of crashing. A rehearsal that fails now carries the reason.

Consequence for a server: provision roughly 9 GiB free (8731 MiB plus churn)
before a combined start, or the preflight will refuse before touching anything.

## Adversarial review and fixes, 2026-09-20 (Hermes)

Independent adversarial review:
[docs/reviews/target-and-deployment-review.md](reviews/target-and-deployment-review.md).
Twelve must-fix findings were established and all twelve are fixed, each with a
regression test where the finding is testable without live containers:

- the installer's image step read a top-level `id` from a lock file that has
  none (a `KeyError` on the documented happy path) and pulled a bare digest; it
  now walks every nested pin and pulls the `repository@digest` reference;
- `smoke` reported success with the console down; it now checks the console pid
  is alive and folds that into the result;
- the restore path leaked its target ownership descriptors on failure and bound
  the created container by name; it now closes the stack in `finally` and passes
  the identity captured from `docker run`;
- any failure after generation initialization left a durable pin that adoption
  could never bind; `publish_intent` now adopts an existing pin that targets the
  same container instead of minting a new generation;
- `adopt-retained.py` did not verify the whole placement was stopped, so a
  running service container could observe the database disappearing; it now
  refuses when any container of that owner is running;
- `verify_retained.py` opened the source catalog read-write from a target-scoped
  check; the catalog is now read-only and skipped entirely for a target;
- the systemd unit did not put Bun on the service `PATH`;
- evidence files merged checks across code revisions while recomputing `passed`;
  each script now replaces its own section wholesale and stamps it with the
  producing script digest (the migration design has no checks yet: it is not
  implemented);
- `install` took no operation lock, did not validate the bootstrap file's owner
  and mode, and `prepare_target_state` could create the installation root with
  umask permissions;
- a failure after the target descriptor existed was a dead end: the restore
  refused to resume and the docs forbade clearing state by hand. Two commands
  now close it. `publish_intent` binds an existing pin for the same container,
  so `lab/adopt-retained.py target` can adopt a half-restored target, and
  `lab/retire_recovery_target.py` archives an interrupted or failed descriptor
  into `recovery-target-history/` (recording it in the cutover journal) so a
  fresh restore can start. Only `interrupted`, `failed` and `cleanup-failed`
  descriptors may be retired; a running container, a pending per-target HBA
  operation, or a verified target all refuse, and containers and volumes are
  never deleted;
- the fresh-target probe contained a tautological check and a label that
  overstated what it tested; the probe and its evidence were regenerated.

Suites after the fixes: 271 Python tests, 73 Bun tests/408 assertions. Claims
that the review judged overstated were corrected in
[TARGET-HBA-WRITERS](TARGET-HBA-WRITERS.md), [SERVER-DEPLOYMENT](SERVER-DEPLOYMENT.md)
and [DEPLOYMENT-READINESS](DEPLOYMENT-READINESS.md).

## User stop and Hermes handoff, 2026-09-20

The user explicitly stopped this Codex implementation to continue with another Hermes agent/model. All reviews are completed; no test process remains active. Read-only inventory verified 11 source and 8 recovery-target containers stopped, no fresh fixture containers remaining, and no retained source generation pin. Saved the completed crash-probe changes and evidence, without beginning adoption or another experiment. HERMES-HANDOFF.md is the concise continuation entry point. The wider platform goal remains unfinished; this is a user-requested stop, not goal completion.

## Second deployment-tooling review and the defects it led to, 2026-09-20 (Hermes)

An independent adversarial review of the deployment tooling
([review 2](reviews/deployment-tooling-review-2.md)) reported 20 must-fix
defects and falsified 17 claims in the status documents. All 20 are fixed and
every falsified claim is corrected, with regression tests: the proxy strips
hop-by-hop, framing and client-supplied forwarding headers and validates its
upstream whichever way it was supplied; the supervisor-unit renderer validates
the account and paths that reach a root-owned unit and fails when an installed
unit never becomes active; the checks that could not fail now can (TLS stub and
port announcements, literal and encoded traversal over a raw socket plus a
direct allow-list probe, `vite.config.mts` in the freshness gate, the gate's own
`Preflight: 0 blocker(s)` line, console state written after the start); the
rehearsal keeps attempts and unit status out of the green checks, verifies the
unit where systemd runs it, and redacts the bootstrap path it recorded; and
`deploy/server-acceptance.sh` runs under `set -euo pipefail` with no swallowed
step and stops the unit for the rehearsal and restores it afterwards.

Running the documented acceptance command then exposed two defects no review
had found, both fixed here:

- `install_server.install()` held the installation operation lock across
  `lab/installation_runtime.py up`, which takes that same lock for its own
  lifetime, so the installer refused its own runtime on every host. The lock is
  released after the console-build step; `lab/test_install_lock.py` pins the
  handoff.
- A refused install left the rehearsal without evidence, while the wrapper
  pointed at a file that did not exist. The refusal is now a recorded finding
  with the diagnostic path, the rehearsal always writes its evidence, and the
  wrapper says whether the file was written.

Verified on this host after the fixes: the rehearsal 11 of 11 at 17:21
(`docs/evidence/deployment-rehearsal.json`), the supervised path 10 of 10 at
17:08 (`docs/evidence/supervised-run.json`), TLS 21 checks and console static
serving 17 checks, and the acceptance run 11 of its 12 rows
(`docs/evidence/server-acceptance-rehearsal.json`), the red row being the unit
gate that needs root. Suites: 381 Python tests, 73 Bun tests/408 assertions.

Capacity, measured tonight: the combined runtime needs 8761 MiB of free memory,
so a server must keep 9 GiB clear of other work; this host fell under that line
repeatedly while other agents used it, and the admission refused with
`host_memory_headroom` instead of half-starting.

## Supervised installation exercised for real, 2026-09-20 (Hermes)

Installing and starting `sbarbase.service` on this host, which no earlier run had
done (every rehearsal used `--skip-install` and every unit check ran as a
per-user unit), exposed three more defects:

- the unit demanded `ReadWritePaths=$HOME/.secrets`; nothing in the project
  creates or uses that directory, and a `ReadWritePaths` entry for a missing path
  makes systemd fail the unit with `226/NAMESPACE` before the first command runs,
  so the unit could never start on a fresh host. The unit now names the checkout
  only, and a test refuses any write path outside it;
- a system service does not inherit the operator's shell, so a docker context
  pointing at a desktop socket is unreachable from the unit. The preflight already
  refused, naming the endpoint it tried, but then reported the pinned images as
  "not local; install will pull" and the host as a "fresh install" from a process
  that could not see the daemon at all. It now reports one honest line instead:
  images not inspected and containers not enumerated;
- the installer refused a missing service account only by failing later at
  `systemctl enable --now`. It now validates the account before installing, and
  the acceptance script forwards `--service-user`, `--home` and `--bun-dir`.

With the path fixed and `DOCKER_HOST` pointed at the system socket, the unit
installed, enabled and reached `active` under systemd on this host at 17:59. The
rehearsal that follows in the acceptance flow then refused on host memory
(8663 MiB available against an 8810 MiB plan), which is this workstation's
condition, not the product's: a server must keep about 9 GiB clear.

### The unit gate on this host

The host freed its memory at 18:08 and the rehearsal ran green with the unit
required: 12 of 12 checks,
`docs/evidence/server-acceptance-rehearsal.json`, verifying the installed
`/etc/systemd/system/sbarbase.service` itself (`verify_source: installed`) with
18 live bootstrap checks, 14 combined gateway checks and no owned container left
running. Before that window, with memory below the gate, the same three facts
were verified directly with the unit installed as root on 2026-09-20:

```
$ systemctl is-enabled sbarbase.service          -> enabled
$ systemd-analyze verify .lab/rendered-sbarbase.service -> exit 0
$ ls -l /etc/systemd/system/sbarbase.service      -> root:root, 1318 bytes, 18:01
$ ls -l /etc/systemd/system/sbarbase.service.d/docker.conf -> DOCKER_HOST drop-in, 62 bytes
```

and the install that produced them recorded `applied: true`, `running_as_root:
true`, `service_account: present`, `verify: passed` in
`docs/evidence/supervisor-unit.json` after the unit reached `active`. The unit,
its drop-in and the rendered copy are removed from this workstation once the
background attempt finishes; nothing of it is left enabled on boot.

### The documented command under sudo, 2026-09-20

Running the runbook's own `sudo deploy/server-acceptance.sh --rehearse
--install-unit` form exposed one more defect: `sudo` replaces `PATH` with a
secure default, so a Bun installed under the invoking user's home is invisible to
the script and to the unit it installs, and the run stopped at the first
prerequisite with a bare `bun is not on PATH`. `--bun-dir` now adds the directory
to `PATH` for every step as well as rendering it into the unit, and the failure
names the flag and the path to pass. Verified from a sudo run: without the flag
the message names `/home/$SUDO_USER/.bun/bin`; with it the prerequisites pass and
the run reaches the preflight, which then refused honestly on host memory
(8502 MiB available against an 8789 MiB plan) instead of half-starting. The
runbook examples carry the flag and say why.

## Third adversarial review, of tonight's fixes (2026-09-20)

A second independent review attacked the changes that closed review 2 and the
defects found by running the documented command. It reported nine must-fix
defects; all nine were reproduced here first and then fixed:

- the unit-state probe printed `unknown` on top of systemctl's own word whenever
  the unit was not active, so the stop gate could never compare equal: every
  `--install-unit` run waited out its timeout and aborted with a false "did not
  stop" while leaving the installation stopped. The probe now prints only
  systemctl's word, and a test runs the real definition against a stubbed
  systemctl;
- the unit was released only under `--install-unit`, so a plain `--rehearse`
  could run a second supervisor against a live supervised installation, and no
  failure path restored it. The release now happens whenever the unit is active,
  and a trap starts it again on any exit;
- the renderer refused `/opt/sbarbase`, the layout the shipped unit itself names,
  because it checked for the absence of that literal. It now checks the values the
  unit must carry, so the shipped layout renders and a moved directive still
  refuses;
- the TLS proxy's body cap was a Content-Length pre-check plus a full buffer, so a
  chunked body was read whole before the 413. The body is now read incrementally
  and refused the moment it passes the cap;
- the supervised gate row grepped a 60-line journal window that could contain an
  earlier run's `Preflight: 0 blocker(s)`, so it could not fail. It is now bounded
  to the current start with `journalctl --since`;
- the recorded-command redaction missed the `--bootstrap-file=PATH` form, which
  leaked the private path into committed evidence;
- a crash in the TLS or console check could leave the previous run's evidence in
  place while the acceptance script pointed the operator at it. Both checks now
  clear the file before they start, the TLS check writes a failure record if it
  raises, and every step message states whether evidence was written;
- the row that reported a green `supervised path exercised through systemd` is
  renamed to `the shipped supervisor unit is not required for this run`, and the
  acceptance variant's red row to `the shipped supervisor unit is installed for
  an acceptance run`.

Numeric claims this document and the readiness matrix carried from older runs
(admission 8943, source-stage footprint 313, plan 8761, 10 acceptance tests) were
wrong against the committed artifacts and are corrected to what those files
actually record (8766, 341, 8789, 18).

Suites after the fixes: 399 Python tests, 73 Bun tests, `tsc --project
tsconfig.json` clean.

## Administration surface redirect, latest checkpoint

Read [the integration specification](STUDIO-INTEGRATION.md). The per-environment
administration surface is the original upstream Supabase Studio (pinned, one
Studio and one postgres-meta per environment), and the home-grown console keeps
only the platform layer: organizations, projects, environments, connection
details, keys and provisioning status. The withdrawn goal is parity with Studio's
design system.

What this invalidates: the design-parity goal, the look-alike framing of the
console concept and its captures, and any sentence that presents the console as
the administration surface for an environment. The hierarchy, the isolation model
and every packaged-backend document are unaffected.

What exists today: a live probe against one retained environment started the real
Studio and its Table Editor edited that environment's own table. Test counts for
this checkpoint: 78 Bun tests, 472 assertions, including three that guard the
console palette and refuse a literal colour outside its token blocks; frontend
typecheck and build pass.

What does not exist: Studio serving, its route, the fourth scoped login, any
measured Studio footprint, and the answer to whether the gateway admits Studio's
own server-side admin calls. The two configured ceilings predate the new
containers, and the placement name list does not count them.
