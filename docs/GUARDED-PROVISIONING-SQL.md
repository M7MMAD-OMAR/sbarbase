# Guarded provisioning SQL adapter

The experimental `lab/guarded_sql_executor.py` now adapts the complete `Runtime.provision_database` SQL path, including `run.provision_environment`. It is now wired into durable worker provisioning through [exact receipt identity](RECEIPT-BOUND-SQL.md). It does not authorize automatic replay.

## Execution contract

The constructor observes the control database and registers the exact operation with cluster/OID pins. Every later control batch checks those pins under its advisory lock. The first target call obtains control-authorized target registration with the same control pins. Subsequent target calls retain that binding and check it before SQL. Only `postgres` and the exact environment database are accepted.

The transport runs fresh quiet psql backends with stdin, ON_ERROR_STOP and acknowledged success. Return objects and scalar query output are preserved. Guard acquisition/release emit no rows. A newline and SQL terminator separate native query text from guard cleanup, including native queries lacking a terminator or ending with a line comment. Fixed trusted SQL remains required; this is not a user-SQL sandbox.

Unchecked execution is rejected. Any exception or interruption poisons the executor instance, including KeyboardInterrupt and SystemExit. It cannot dispatch again or silently obtain a new target identity. Construction failure does not return a usable executor. Recovery requires a separate explicitly authorized protocol, not creating another executor to retry an unknown operation.

## Evidence and review

The disposable upstream pair probe exercises actual role creation, closed CREATE DATABASE, atomic connection grants/reopening, target Auth schema permissions and role search-path changes through this adapter. It then revokes the operation and verifies a target CREATE TABLE is denied with no table created. The rejected executor cannot execute again.

The first real bootstrap run exposed a syntax error from a native SELECT without a terminator. The adapter's query separator was corrected and a trailing-comment case was added. Independent review also found and corrected executor reuse after BaseException interruption. Unit coverage checks exact scopes, pinned target batches, output preservation, uncertain registration and interruption poisoning.

The upstream pair probe passes 51 checks with exact disposable cleanup. The full Python suite passes 118 tests. Sanitized evidence is [stored here](evidence/upstream-sql-pair-fence-checks.json). The unchanged recorded Bun checkpoint remains 73 tests/408 assertions.

## Full durable SQL boundary

Runtime.provision_database now contains all native environment SQL: initial bootstrap, extensions, Storage role/schema privileges, connection limits and REST deadlines. Its executor is propagated into deadline reads and writes. The disposable fixture invokes this exact method with the guarded executor and verifies Storage schema and deadline settings. Only the REST-container inspection is replaced with an absent-container fixture; database SQL is real.

Normal Runtime.provision now constructs the guarded executor from the pending worker receipt and closes its SQL authority before services. The services write-ahead marker now precedes HBA file replacement and reload. A regression proves marker persistence failure prevents that shared write. This improves phase accounting but does not fence HBA or recover it. Independent review found no missing SQL or behavior drift in the extraction.

## Remaining integration

The [fresh worker lifecycle](FRESH-WORKER-LIFECYCLE.md) now validates the newly wired guard with original services and SDK access. Existing-environment resume is already separate. Persist exact operation identity and respect pending receipts. HBA file writes are now outside the SQL-only stage; define their own recovery. Auth and Storage migrations use independent service connections and are not fenced by this adapter. No new replay permission follows from these tests.
