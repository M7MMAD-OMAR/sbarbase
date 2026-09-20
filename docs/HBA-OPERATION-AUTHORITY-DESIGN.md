# HBA operation authority: reviewed next gate

Design only, not implemented. Prepared-request revision checks already exist; full operation revocation does not.

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
