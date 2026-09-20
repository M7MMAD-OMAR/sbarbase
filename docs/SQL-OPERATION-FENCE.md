# Experimental database-local SQL operation fence

Implemented as an isolated prototype on 2026-09-20. Not wired into runtime provisioning, worker settlement or automatic recovery. Read [cross-database design constraints](DATABASE-OPERATION-FENCING-DESIGN.md) before extending it.

## Protocol

`lab/sql_operation_fence.py` builds scripts for a fresh psql backend using stdin, -X and ON_ERROR_STOP. Each script uses READ COMMITTED, a five-second lock timeout and ten-second statement timeout. A per-runtime session advisory lock precedes a fresh token check and all fixed native SQL in that batch. CREATE DATABASE is a separate command outside a transaction block.

An admin registry binds token, runtime, claim, attempt and active/revoked state. One active token is permitted per runtime in that database. Registration is idempotent only for the exact active identity, rejects older or duplicate attempts and cannot resurrect a revoked token. Revocation records an immutable identity tombstone even before registration. It only revokes the exact supplied token and preserves a different newer active operation.

Registry schema/table privileges are revoked from PUBLIC and the canonical anon, authenticated and service_role roles, including grants inherited from defaults at object creation. Schema, table, index and privilege revocations commit in one transaction. A paused-initializer test proves the namespace is invisible before that commit. Host/database administrators remain trusted.

Revocation is considered acknowledged only after the complete psql script succeeds. Cancellation, timeout or lost acknowledgment is not proof of a committed revocation. Registry changes use atomic DO statements; the session lock survives transaction boundaries and is released when the one-shot backend exits. Connections must not be pooled or silently reconnected.

This accepts trusted fixed native SQL, not user SQL. Rejecting psql metacommands prevents accidental reconnect/shell commands but does not make arbitrary SQL safe. Callers must not release the guard lock, mutate its registry, change isolation or hand it uncontrolled migration text. Such integration has not been implemented.

## Live evidence

Run `/usr/bin/python3 lab/partial-database-crash-check.py --upstream --sql-fence`.

[35 checks](evidence/upstream-sql-fence-checks.json) pass on the pinned Supabase PostgreSQL image in the disposable, network-disabled container. A separate test gate holds a real guarded backend while a revoker queues. One revoker is cancelled and leaves authority active. A subsequent revoker queues before an old delayed batch. Releasing the gate lets the admitted mutation finish, commits revocation, and causes the delayed batch to recheck and fail without writing.

Other checks cover private registry access despite adversarial default grants, token/claim mismatches, revoked registration, revocation before registration, stale attempts, preservation of newer authority, fresh reconnect rejection and guarded CREATE DATABASE. The identical lock key is simultaneously acquired in another database, explicitly demonstrating the limited scope. The exact disposable container is removed afterward.

Four isolated builder tests pass; the full Python checkpoint is 106 tests. Adversarial review found no must-fix in the declared experimental scope and prompted specific error validation plus a wider bounded lock timeout. No retained environment or production-like catalog was modified.

Guard lock/unlock now use DO/PERFORM to avoid contaminating scalar query output. A live regression failed before this correction and passes afterward. Executor integration must additionally use psql quiet mode to suppress command tags.

## Remaining integration gates

The registry is database-local. It does not stop target-database migrations, Auth/REST startup, Storage registration or delayed Docker container creation. There is no operator revoke API, no catalog settlement transition based on this prototype, and no automatic partial-operation replay.

A separate [two-database prototype](SQL-PAIR-REVOCATION.md) now tests sequential revocation and coordinator interruption. Neither prototype is integrated into runtime provisioning. Keep all unknown later-stage receipts blocked until the whole relevant mutation path is covered.
