# Guarded provisioning SQL adapter

The experimental `lab/guarded_sql_executor.py` now adapts the actual `run.provision_environment` SQL path. It is not wired into the retained Runtime, worker settlement or automatic replay.

## Execution contract

The constructor observes the control database and registers the exact operation with cluster/OID pins. Every later control batch checks those pins under its advisory lock. The first target call obtains control-authorized target registration with the same control pins. Subsequent target calls retain that binding and check it before SQL. Only `postgres` and the exact environment database are accepted.

The transport runs fresh quiet psql backends with stdin, ON_ERROR_STOP and acknowledged success. Return objects and scalar query output are preserved. Guard acquisition/release emit no rows. A newline and SQL terminator separate native query text from guard cleanup, including native queries lacking a terminator or ending with a line comment. Fixed trusted SQL remains required; this is not a user-SQL sandbox.

Unchecked execution is rejected. Any exception or interruption poisons the executor instance, including KeyboardInterrupt and SystemExit. It cannot dispatch again or silently obtain a new target identity. Construction failure does not return a usable executor. Recovery requires a separate explicitly authorized protocol, not creating another executor to retry an unknown operation.

## Evidence and review

The disposable upstream pair probe exercises actual role creation, closed CREATE DATABASE, atomic connection grants/reopening, target Auth schema permissions and role search-path changes through this adapter. It then revokes the operation and verifies a target CREATE TABLE is denied with no table created. The rejected executor cannot execute again.

The first real bootstrap run exposed a syntax error from a native SELECT without a terminator. The adapter's query separator was corrected and a trailing-comment case was added. Independent review also found and corrected executor reuse after BaseException interruption. Unit coverage checks exact scopes, pinned target batches, output preservation, uncertain registration and interruption poisoning.

The upstream pair probe passes 43 checks with exact disposable cleanup. The full Python suite passes 104 tests. Sanitized evidence is [stored here](evidence/upstream-sql-pair-fence-checks.json). The unchanged recorded Bun checkpoint remains 73 tests/408 assertions.

## Remaining integration

Route the complete durable provisioning path through this contract, including helper calls and extensions, and separate existing-environment resume from new provisioning. Persist exact operation identity and respect pending receipts. Move HBA file writes outside the SQL-only stage and define their own recovery. Auth and Storage migrations use independent service connections and are not fenced by this adapter. No new replay permission follows from these tests.
