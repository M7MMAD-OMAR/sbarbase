# Preflight recovery: unfinished handoff

Snapshot: 2026-09-20. Last completed implementation commit: `996d212`.
This source is present in the working tree and handoff ZIP but is not a completed or approved recovery guarantee.

## Intended decision

Automatically recover an interrupted provisioning attempt only with exact, durable evidence that it stopped before the first external mutation. Keep uncertain database, service, Storage and publication stages blocked. A native completion witness takes precedence over stage evidence.

The new protocol records these stages: preflight, database, services, storage, publication. The database marker must be persisted before dispatching the first database mutation. Only a genuinely absent native outcome can fall back to an exact preflight record; malformed or unreadable evidence blocks recovery. Startup acquires fresh effect ownership and the operation lock. A catalog transaction records the exact claim and recovery decision before consuming the receipt. At most two automatic preflight requeues are allowed per environment; further interrupted preflight attempts fail for explicit intervention.

Reason: process disappearance does not prove rollback. Blind retries may repeat partial external effects. Keeping every early interruption permanently blocked is unnecessarily restrictive when a write-ahead boundary can prove that external mutation never began. This is a process-crash design, not a host power-loss guarantee.

## Source in progress

- `lab/effect_receipt.py`: durable stage records and stage-dependent native witnesses.
- `lab/durable_runtime.py`: markers around native provisioning stages.
- `lab/worker_lock_exec.py`: protocol advertisement in new durable receipts.
- `lab/worker.py`, `lab/worker.ts`: operation lock during startup settlement.
- `lab/worker-receipt.ts`: exact preflight evidence and receipt settlement.
- `src/control/catalog.ts`: transactional recovery decisions and retry budget.
- `lab/test_native_stages.py`, `tests/worker-receipt.test.ts`: targeted coverage.

Recorded targeted results: three Python stage tests passed; ten Bun receipt tests passed with 80 assertions. These are not a full-suite or live integration result for this change. The last completed baseline remains 82 Python tests and 67 Bun tests with 377 assertions. No new runtime was allocated for this work.

## Resume next

1. Inspect the current diff and confirm every helper before the database marker avoids external mutation.
2. Fix the test title claiming authorization revalidation, or add the missing revocation scenario.
3. Review descriptor inheritance and operation-lock release through the real supervisor.
4. Add stage evidence to the read-only inspector without making inspection permission to replay.
5. Obtain adversarial implementation review; test crash windows before and after the boundary.
6. Run full Python/Bun suites and strict types, then the existing bounded known-refusal integration probe. Reuse its fixture, preserve all retained volumes, and stop owned runtimes afterward.
7. Update evidence and documentation before committing this implementation.

Do not clear pending receipts, un-fence the moved source, or infer production readiness from the targeted tests. The last recorded runtime snapshot had all 19 owned containers stopped; recheck before any runtime operation.
