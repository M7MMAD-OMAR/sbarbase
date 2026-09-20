# Experimental sequential SQL revocation

Recorded 2026-09-20. This is a bounded prototype, not runtime recovery or permission to replay partial provisioning.

## Decision and reason

PostgreSQL advisory locks are database-local. Revoke the exact operation in `postgres` first, then in the target database. A successful first commit does not stop target SQL. Only acknowledged completion of both phases, with unchanged cluster identity and database OIDs, returns evidence covering guarded SQL in both databases. This avoids pretending there is an atomic cross-database cutoff.

`lab/sql_operation_revoke.py` uses fresh one-shot backends. The caller must hold exclusive host worker/effect/operation ownership and prevent new work. Every old control mutation, including target creation/replacement, and every target mutation must use the relevant guard. Arbitrary SQL, asynchronous work, services and Storage are outside this protocol.

Retry reobserves both databases and preserves revoked tombstones. A missing target is accepted only after the control barrier prevents delayed guarded creation. A closed target is refused, never reopened or declared safe. Cluster or database replacement, malformed observations, timeout and uncertain acknowledgment produce no completed evidence. There is no durable coordinator journal or production operator API yet.

Registry bootstrap now creates metadata and removes default grants in one transaction. Review exposed the previous intermediate visibility window; a real paused initializer reproduced it before the fix and verifies invisibility afterward.

## Evidence

Run `/usr/bin/python3 lab/partial-database-crash-check.py --upstream --cross-fence`.

[29 live checks](evidence/upstream-sql-pair-fence-checks.json) pass in an isolated, network-disabled pinned Supabase PostgreSQL container. They cover actual coordinator SIGKILL after the control commit, target writes during that gap, successful retry, delayed SQL/registration rejection, absent and closed targets, cluster mismatch and real target replacement. Native OIDs are explicitly normalized to JSON integers before validation. Exact disposable cleanup passed.

The separate database-local suite passes 35 live checks. The full Python suite passes 98 tests. The unchanged recorded Bun checkpoint is 73 tests and 408 assertions. These counts describe different scopes, not cumulative security coverage. Unit failure injection covers uncertain target outcomes; this pair probe does not claim a real target-lock timeout rehearsal.

## Newly verified limitation

A live counterexample shows that after an absent-target barrier, a newer claim can create the database and a delayed old lazy registration can then establish old authority in it. The 29-check result includes reproducing this limitation, not proving it fixed. Database-generation binding on revocation alone is insufficient; target admission must also reject stale registration across generations. Do not interpret the result as safe admission of subsequent jobs. See [the mutation map](PROVISIONING-MUTATION-MAP.md).

## Next gate

Inventory and guard every runtime SQL mutation before connecting this evidence to recovery. Then handle service and Storage effects, durable coordinator state, and later-stage reconciliation explicitly. Unknown later-stage receipts remain blocked. Retained environments and volumes were not used or modified by these disposable experiments.
