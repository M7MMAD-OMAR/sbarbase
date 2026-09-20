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
