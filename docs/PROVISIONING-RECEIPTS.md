# Provisioning uncertainty and replay protection

Before a worker-driven provisioning effect starts, its wrapper writes `.lab/upstream/worker-effect.json` (or the component equivalent), flushes the file and directory, and retains the worker flock. The receipt records a version, random token and exact environment/runtime/claim/attempt identity. It contains no credentials or command payload. An existing receipt is never overwritten.

The wrapper now supervises the direct child instead of replacing itself. Both retain the worker lock. Successful exit publishes a completed receipt atomically. Exit 75 is classified as a known pre-mutation capacity refusal only for the exact durable provisioner command. Every other failure, signal death, invalid receipt or incomplete publication remains unresolved. A process exiting normally with an error does not establish that its external effects were rolled back.

## Restart ordering

1. Hold the exclusive worker lock.
2. Settle any completed receipt against the exact original catalog claim. Atomically record its outcome with job completion.
3. Consume the receipt only after that commit, then flush the directory.
4. Only then start runtimes or recover queued work.

`dev.py` invokes `worker.py --upstream --settle-only` before building or starting services, passing its existing lock descriptor. Installation startup and internal runtime start/provision methods also check the receipt. An unresolved receipt blocks startup and worker replay. A normal public retry cannot clear it. A valid already-committed historical receipt can be consumed without modifying a later retry attempt.

The outcome table makes a crash between catalog commit and receipt removal idempotent. A job claimed before receipt publication can still be requeued because this wrapper has not yet launched its effect. Legacy interrupted jobs created before receipt support do not have this evidence; inspect them explicitly when adopting this version rather than treating missing receipts as retroactive proof.

## Evidence

- Real harmless subprocesses: nonzero exit after a marker mutation and direct-child SIGKILL both retain a pending receipt; a second launch is refused without repeating the mutation.
- Publication fsync, existing-file refusal, partial publication, corrupt records and startup bypass guards are tested.
- Catalog tests cover exact completion, restart after commit, identity/attempt mismatch, refusal and a concurrent explicit retry before receipt consumption.
- [Eleven live integration checks](evidence/worker-receipt-checks.json) reuse the existing rejected-capacity fixture through the actual combined supervisor. Refusal is committed and consumed; no runtime allocation or private allocation change occurs; all four environments remain reachable and all owned containers stop afterward.
- Full suites: 72 Python tests; 65 Bun tests, 354 assertions. Worker/receipt strict typing passes. Adversarial review found and closed a runtime-start bypass and a historical-receipt retry race.

The live check verifies known refusal settlement, not a real Docker daemon crash. Earlier automatic recovery evidence predates this stricter gate and does not imply unresolved receipts are automatically resumed now.

## Remaining work

This is replay prevention, not complete automatic recovery. A later [effect guardian](EFFECT-GUARDIAN.md) adds a local deadline and owned-group termination protocol; complete descendant containment is not claimed. Killing the direct provisioner or wrapper may leave a Docker client or daemon-side effect running, but a pending receipt prevents worker replay and normal startup.

Do not delete a pending receipt, edit a job to queued, or clear it based on age/PID alone. A bounded operator reconciliation workflow must verify actual Docker/database effects, preserve resource identity, and record a specific safe result before allowing recovery. That workflow remains to be implemented. Trusted low-level operator code and direct Docker/SQL access are outside this worker gate; this is not protection from a hostile host operator.

## Native completion recovery

A later [native witness protocol](NATIVE-OUTCOME-RECOVERY.md) can resolve a pending guardian receipt only when the fixed provisioner durably reported the exact known outcome. Recovery occurs at worker startup after an independently acquired effect lease. A surviving worker cannot use a witness to bypass a still-running native process. Unknown or incomplete native outcomes remain blocked.
