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

Eight host shell tests and 17 checks using the pinned Supabase PostgreSQL image pass. The image probe starts only a bounded shell container, not PostgreSQL. It covers stale permits, stale snapshots, re-registration, fresh preparation after revocation, competing authority, complete application, truncated registry input, lost registry and exact cleanup. Host tests also cover lost acknowledgment without retry, duplicate JSON keys, checksum corruption and generation mismatch. A failing marker-loss test exposed shell `set -e` behavior with an AND-list; separate mandatory checks fix it.

Run `/usr/bin/python3 -m unittest discover -s lab -p 'test_hba_authority.py'` and `/usr/bin/python3 lab/hba-authority-check.py`. Sanitized output is in [evidence](evidence/hba-authority.json).

These are trusted-host protocol helpers, not an API accepting arbitrary permits or operation identities. Exact receipt/startup identity validation and host journal persistence are still required. Legacy runtime writers do not consult this registry. Real helper SIGKILL immediately before rename preserves the complete active registry; SIGKILL after rename preserves the complete revoked registry. A failed pre-rename revocation therefore leaves authority active and must not be treated as successful cancellation. A new operation acquires the released lock after helper death, and the persisted tombstone refuses re-registration. These are process interruption checks, not power-loss, clone/rollback, PostgreSQL activation or full operation-recovery guarantees. Do not integrate or enable later-stage replay until the blockers above are resolved.
