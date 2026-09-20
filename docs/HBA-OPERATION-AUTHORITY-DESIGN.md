# HBA operation authority: reviewed next gate

An isolated registry prototype now exists in `lab/hba_authority.py`; it is not integrated into runtime or startup. Prepared-request revision checks are integrated; full runtime operation revocation is not.

## Protocol to prove

Persist an immutable host journal before register dispatch. Bind operation kind, exact worker claim or startup identity, operation token, captured Docker container ID and prepared revision/payload digest. Under the existing global stable container lock, register one exact active operation, apply only while that exact identity is active, and revoke by atomically persisting a tombstone even if registration never arrived.

Registration cannot reactivate a revoked token or admit another token while an unresolved operation is active. Never infer corrupt, unreadable or unexpectedly missing registry state is empty. No TTL, automatic takeover or tombstone deletion. Every managed writer, including startup, must use the same protocol. An old operation must retain its captured container ID rather than resolving a replacement by name.

## Integration blockers

- Startup must reconcile its prior journal under fresh host ownership before issuing new authority. A startup crash has no worker receipt by default, so journal discoverability is required.
- Registry initialization and loss across container replacement need explicit generation rules. Restored/cloned filesystems cannot silently resurrect authority.
- Unknown register, apply, release or revoke acknowledgments require exact-token reconciliation. New tokens are not a retry mechanism.
- Durable registry snapshots, tombstones and the immutable lock inode need real interruption tests. Garbage collection requires a separately proven generation barrier.
- File application, parser validity, reload signaling, enforcement on new connections and existing sessions are separate states. Revocation does not roll back an already published file.

Independent adversarial review identified these constraints. Do not integrate a token registry without this startup and recovery design merely to claim whole-operation fencing.

## Isolated implementation evidence

The prototype captures the exact container ID, validates a checksummed registry snapshot, and compares the whole previous registry hash under the stable HBA lock before atomically updating it. Revocation leaves permanent tombstones, including for operations that have not registered. Applying a prepared file checks registry and HBA revisions under that same lock. Registry deletion is not treated as an empty registry; a separate initialization marker prevents silently recreating a lost registry.

Eight host shell tests, eight journal tests and 30 checks using the pinned Supabase PostgreSQL image pass. The image probe starts only a bounded shell container, not PostgreSQL. It covers stale permits, stale snapshots, re-registration, fresh preparation after revocation, competing authority, complete application, truncated registry input, lost registry and exact cleanup. Host tests also cover lost acknowledgment without retry, duplicate JSON keys, checksum corruption and generation mismatch. A failing marker-loss test exposed shell `set -e` behavior with an AND-list; separate mandatory checks fix it.

Run `/usr/bin/python3 -m unittest discover -s lab -p 'test_hba_authority.py'` and `/usr/bin/python3 lab/hba-authority-check.py`. Sanitized output is in [evidence](evidence/hba-authority.json).

These are trusted-host protocol helpers, not an API accepting arbitrary permits or operation identities. Live receipt/startup authority validation and integration of the host journal with existing ownership leases are still required. Legacy runtime writers do not consult this registry. Real helper SIGKILL immediately before rename preserves the complete active registry; SIGKILL after rename preserves the complete revoked registry. A failed pre-rename revocation therefore leaves authority active and must not be treated as successful cancellation. A new operation acquires the released lock after helper death, and the persisted tombstone refuses re-registration. These are process interruption checks, not power-loss, clone/rollback, PostgreSQL activation or full operation-recovery guarantees. Do not integrate or enable later-stage replay until the blockers above are resolved.

## Immutable host journal prototype

`lab/hba_journal.py` persists `hba-operation.json` before the first registry registration. The fixed filename must reside in the installation's authoritative private state directory; choosing a new directory is not a retry protocol. It binds the token, registry generation and original snapshot hash, captured container ID, exact prepared content/revision and a structurally validated startup or worker identity. The private checksum envelope is created exclusively, then file and parent directory are synced before dispatch. Publication failures retain the blocking file. The initial registry snapshot is never refreshed to retry registration.

Reads require a regular non-symlink file owned by the caller with mode 0600, bounded to 4 MiB, valid checksum and exact fields. Incomplete, malformed, conflicting or existing journals block new intent. The inspector reports only observed absent/active/revoked registry authority and leaves application/activation unknown. Absent token is not proof that a queued registration cannot arrive later, and does not authorize deletion or replay. Missing/corrupt registry state raises an error instead of reporting absence.

Eight unit tests cover file/directory sync ordering and failure, lost register acknowledgment, immutable intent, invalid identities, torn/symlink/public/oversized records and conservative inspection. The image probe now uses a real private journal before registration and observes its exact token after revocation. Thirty image checks and all137 Python tests pass. Host process SIGKILL now covers after durable journal publication before registration, and after registration returns before the outer begin call completes. Live lease/receipt validation, journal settlement and container replacement remain unimplemented. No journal cleanup or automatic replay API is provided.

## Host process interruption evidence

`lab/hba_journal_crash_check.py`, called by the isolated image probe, stops the actual host Python child at a profiled return boundary. The parent confirms its own unreaped child is stopped using waitid/WNOWAIT, kills it and reaps it. Fresh inspection observes absent authority before dispatch and active authority after registration; both cases retain the exact immutable journal and reject replacement before any dispatch. HBA content stays unchanged because these checkpoints register authority only. Explicit fixture teardown persists a tombstone before removing the disposable host directory.

The 30-check image probe includes both host interruption cases. This does not test killing during an in-flight Docker RPC, machine shutdown, live worker leases or automatic reconciliation. Observed absence alone still cannot rule out delayed registration in general. No retained installation state is used.

## Worker ownership entry gate

`lab/hba_ownership.py` now offers an isolated `begin_worker` entry point. It validates opened descriptors against regular nonsymlink worker/effect/operation lock files, requires three distinct inodes, and obtains or retains each exclusive nonblocking flock. It then derives journal identity from the exact pending native receipt, durable services-stage record and current running SQLite claim. A dedicated exact-integer `hbaProtocol: 1` is required; legacy receipts are refused. The caller must keep descriptors held through dispatch and subsequent effects.

Nine subprocess tests use actual inherited file descriptions, exclusive flocks and a temporary SQLite catalog. They prove acceptance of the exact identity and rejection of competing independent file descriptions, wrong/symlink/aliased lock files, stale claims, missing receipts, wrong stages and legacy/invalid protocol. These tests record dispatch locally; they do not exercise a real worker guardian against Docker. All 146 Python tests pass; the unchanged image probe checkpoint remains 30.

This is authority for the executing native worker, not fresh recovery ownership. Inherited descriptions may be shared with living holders. Recovery must acquire independent descriptions. The real guardian does not yet advertise the new protocol and Runtime.hba does not call this entry point. Startup authority, full-operation settlement, state-directory/container-generation binding and all-writer migration are still required before production integration. The shared native identity validator preserves SQL's preflight/database-only wrapper.

Adversarial review found no must-fix in the isolated gate and identified an additional integration boundary: valid host locks and receipt do not prove that the supplied captured container belongs to this installation. The runtime caller must verify the exact owned database target and expected image before granting configuration authority.

## Configured database target gate

`lab/hba_target.py` captures a target once from trusted installation name, owner label and pinned image. The frozen record contains the full Docker ID. Worker entry now requires this record, rechecks the captured ID's exact name/owner/image and running state, and requires prepared content and registry snapshot to identify that same container before journal publication. A mismatch cannot trigger name-based recapture.

Three target tests plus one worker rejection test cover immutability, wrong metadata, stopped state, changed IDs, different intent targets and rejection before journal creation. Four real pinned-container checks validate the configured target and reject wrong name/owner/image. All 150 Python tests and 34 image checks pass. Independent review found no must-fix in this scope. The image fixture runs a shell, not PostgreSQL, so this is target identity evidence rather than database readiness evidence.

Expected policy still must originate from trusted installation configuration when production wiring is added. This does not create an installation-generation registry, authorize hostile host administrators, or settle old journals after container replacement. Existing runtime writers and startup integration remain pending.

## Startup ownership gate

`lab/hba_startup.py` acquires worker, effect and operation ownership in that order. Effect and operation always use fresh open descriptions. The supervisor may explicitly supply its inherited worker descriptor; the gate duplicates it and only closes the duplicate, never unlocking the shared flock. A surviving worker/guardian's effect lock still blocks startup.

Only genuinely absent journal and worker-receipt entries are clear. Existing, malformed or dangling entries and lookup errors block new startup. The context creates its own UUID after acquiring ownership, requires exactly three distinct validated lock descriptors, and permits one begin attempt only. Begin rechecks ownership, pending records and target identity. An expired or fork-inherited context cannot begin. It does not clear, settle or replay a prior journal.

Ten tests exercise real local flocks, inherited ownership, surviving effect exclusion, pending/dangling records, lookup errors, failed attempts, malformed descriptor counts, expired context and a real fork. The image probe now registers through this gate and demonstrates that a pending journal blocks a fresh startup context. All 160 Python tests and 36 image checks pass. Tests do not establish integration with the actual supervisor.

Review prompted explicit descriptor-count validation: empty/short contexts already failed the three-distinct-lock check, while extra descriptors were previously ignored by zip. Constructor and begin now both reject any count other than three. Production startup wiring, registry initialization/generation reconciliation, conservative settlement and all-writer migration remain open.

## Exact authority retirement under fresh ownership

`lab/hba_reconcile.py` opens existing worker/effect/operation lock files independently, without creating missing files or accepting inherited descriptors. Matching distinct exclusive locks remain held throughout journal load, target verification, tombstone confirmation and HBA digest observation. Missing/corrupt authority, wrong generation, conflicting bindings and another active token refuse reconciliation. An absent or active exact token is revoked; an already matching revoked token is read without another registry update.

The immutable journal remains pending. Neither worker receipt nor control catalog is modified. HBA content is only hashed and classified as matching before, matching desired, matching both or different. This is a point-in-time byte observation, not proof of application history or PostgreSQL activation. The helper does not apply or reload HBA, clear journals, resume startup, settle jobs or authorize replay.

Six unit tests exercise fresh-lock contention, absent/active/revoked paths, binding/missing-registry/conflicting-operation rejection, uncertain acknowledgment and conservative content reporting. The live image probe loses an acknowledgment after an actual registry commit, then confirms exact retirement on a subsequent invocation. Host SIGKILL cases now use this retirement path and verify delayed old registrations fail. A pre-issued apply permit fails after retirement; journals remain unchanged. All 166 Python tests and 42 live checks pass. Independent review found no must-fix in the bounded scope. This is not actual supervisor recovery or a full operation outcome.

## Immutable registry generation binding

`lab/hba_generation.py` exclusively publishes a private checksummed `hba-generation.json`, binding a UUID and exact configured container/name/owner/image before backend initialization. Startup initialization is explicit and has a separate one-shot allowance from begin. A failed or uncertain initialization retains the host pin. `read_existing` verifies trusted caller policy and captured container, then reads the existing matching backend marker/registry; it never initializes anything.

Worker/startup begin and exact-token retirement now require the host pin to match the supplied target and registry generation. Missing, corrupt, public, symlink or mismatched host state refuses progress. Recreating a container therefore blocks until an explicit migration protocol exists. There is no automatic replacement, unpinning, tombstone deletion or host/container rollback guarantee. Simultaneous rollback of both state stores remains outside this protocol's evidence.

Six generation tests verify durable ordering, either fsync failing before dispatch, lost initialization acknowledgment, read-only recovery, missing backend refusal, changed target/policy/generation and malformed/private-file checks. Additional startup and retirement tests refuse missing pins before intent or backend access. All 174 Python tests and 47 pinned-image checks pass. The live probe loses initialization acknowledgment after actual registry creation, recovers the exact generation by reading, refuses repeated initialization, and refuses recreating a missing established registry. Independent review found no must-fix in this scope.

Existing runtime adoption must quiesce legacy writers before introducing this protocol. Actual supervisor integration, receipt protocol rollout, durable operation settlement, activation handling and migration between container generations remain required. This does not make legacy queued writes safe automatically.

## Durable baseline cancellation

`lab/hba_settlement.py` can release only the pending HBA journal slot after exact authority retirement and a distinct before-content match. Desired-content, identical before/desired or different-content observations stay pending. The outcome kind is `retired-baseline-observed`; application history and activation remain unknown. It does not claim that the file was never changed in the past.

Fresh worker/effect/operation locks remain held through retirement, private deterministic outcome publication, pending-byte revalidation, unlink and final state-directory sync. The outcomes directory's parent is synced before archive publication, and the archive file and outcomes directory are synced before removing the pending journal. Existing exact archives are verified and resynced on retry; partial, conflicting, nonprivate or symlink archives refuse settlement. Outcomes retain the complete validated journal under its token. A final sync failure after unlink reports uncertainty; the already durable archive remains available for read-only inspection.

Eight settlement tests cover all pre-unlink sync failures, final sync uncertainty, exact archive retry, raw pending-byte changes, partial archives, nonbaseline refusal and real lock ownership during unlink. Worker receipt and control-state bytes are preserved. The live host-SIGKILL fixtures now retire and archive their baseline-only attempts, observe unchanged HBA content and reacquire startup ownership after the HBA slot clears. A changed-file live case remains pending. All 182 Python tests and 56 image checks pass.

This does not settle provisioning jobs or broader startup work. Actual integration must retain the surrounding lifecycle's own uncertainty markers. Successful applied-file/reload outcomes, their crash reconciliation, quiesced legacy-writer migration and real supervisor/worker wiring are still required. Power-loss behavior is not certified by injected fsync failures or process interruption tests.

Adversarial review exposed newline normalization in the raw-journal comparison. A new LF-to-CRLF rewrite test failed before the fix. Journal reads now preserve line endings, so that changed pending file is retained rather than unlinked.

## Native applied-file evidence

`lab/hba_apply.py` publishes one exclusive durable attempt record before dispatching a prepared HBA write. It requires the exact held worker/effect/operation locks, generation/target/journal authority, and either the live worker services receipt or the originating live startup context with matching PID, generated UUID and descriptors. Fresh startup ownership cannot apply an interrupted startup journal. Any existing attempt blocks repeat application.

After application, the native path verifies exact desired file bytes, queries `pg_hba_file_rules` for zero errors and requires `pg_reload_conf()` to return true, using fixed SQL against the captured container ID. It then rechecks bytes, target, active token and raw journal before publishing an immutable `applied-reload-acknowledged` witness. The witness binds full journal/raw digest, content digest, parser result and signal acknowledgment. Activation remains explicitly unknown. Failure retains journal/attempt and does not restore or reload alternative content.

Six unit tests cover ordering, parser refusal without a reload request, false reload acknowledgment, uncertain application, originating-context rejection and attempt fsync failure. All 188 Python tests pass. The separate command `/usr/bin/python3 lab/partial-database-crash-check.py --upstream --hba-apply` passes 17 checks on real pinned PostgreSQL, including valid and invalid HBA, durable witness binding, repeated-apply refusal, retained journals and exact disposable cleanup. [Evidence](evidence/upstream-hba-apply-checks.json). The earlier registry-only checkpoint remains 56 and has a different scope.

Independent review found no must-fix in this applied-path scope. A witness is not PostgreSQL activation proof, a successful job result or permission to delete the journal. Next implement strict witness validation and successful outcome settlement under ownership, then wire the real guardian/supervisor and all managed writers after quiescing legacy effects. Retained installation resources were not used.

## Applied-witness settlement and host death

`hba_apply.read_completion` requires both private bounded checksummed attempt and completion records. Canonical exact JSON comparison rejects unknown fields, bool/int/float substitutions, wrong phases, mismatched full journal/raw digest, false reload acknowledgment or wrong desired digest. Successful settlement validates this evidence before any backend mutation, retires the exact token under fresh ownership, and requires current desired bytes before archiving `retired-applied-reload-acknowledged` and removing the pending journal. The archive embeds the validated witness; activation remains unknown and whole-job success is not implied.

Eight completion tests cover exact archive binding, missing/malformed/public/symlink evidence, strict JSON types, raw-journal changes, changed current content, archive-before-unlink retry and final-directory-sync uncertainty. Worker receipt/control-state bytes remain unchanged. All 196 Python tests pass.

The real PostgreSQL applied-path probe now passes 24 checks. Its child performs the whole startup-owned publication and stops in straight-line code only after `execute` returns normally, including completion file/directory fsync. The parent confirms its own stopped unreaped child, sends SIGKILL, then acquires fresh ownership to settle the exact witness. Tracing proves recovery makes no HBA apply or PostgreSQL SQL call, file bytes remain unchanged, and the historical archive remains readable. Invalid-file attempts without completion evidence stay pending. Independent review found no must-fix. [Updated evidence](evidence/upstream-hba-apply-checks.json).

Next add completion under the still-live originating ownership for normal worker/startup execution; fresh recovery locks deliberately cannot be acquired while that live owner remains. Then integrate guardian protocol/version rollout and every managed runtime writer, handling repeated legitimate startup updates without bypassing one-attempt uncertainty. Actual supervisor/worker integration, activation policy and container-generation migration remain open.
