# Recovering a lost provisioning acknowledgment

Fixed native provisioners now publish an immutable completion witness after synchronous provisioning finishes, before exiting. The durable entry point also publishes its proven preflight capacity refusal. The witness is flushed with its directory and binds protocol version, receipt token, native implementation, environment, runtime, claim, attempt and outcome.

If the guardian dies before completing its receipt, a replacement worker can settle the exact native outcome without executing provisioning again. Missing, partial, conflicting or mismatched witnesses remain blocked. This is recovery of known completion, not inference from container appearance or process disappearance.

## Ownership and ordering

Every worker invocation independently opens and acquires `effect.lock`, even when its supervisor supplied the existing worker-lock description. It retains that fresh lease for its whole lifetime; guardian and native process inherit it. This prevents a replacement worker from using the supervisor's shared worker flock to bypass an old guardian or native process.

A surviving worker cannot recover a native witness immediately after guardian death. It exits on the pending receipt. Only the startup settlement path, after fresh lease acquisition, permits native witness recovery. Catalog outcome commit still precedes receipt consumption, and existing exact-claim idempotency rules apply.

Witnesses remain private under `.lab/upstream/effect-outcomes/<token>.json` or the component equivalent. They are retained as local recovery evidence, excluded from the handoff ZIP. They contain operational identity, not credentials or command payloads.

## Descriptor mapping defect found by live verification

The first live run stopped before effect publication, recording `runtime_failed`. On installed Bun 1.3.14, mapping source worker FD 4 and effect FD 3 to child FDs 3 and 4 aliased both outputs to the worker file. A harmless inode probe reproduced this exact failure.

`worker.py` now duplicates source descriptors to numbers at least 10 before invoking Bun. Child mapping slots 3 and 4 cannot overwrite those sources. The helper also rejects the unsafe overlap. An actual Bun subprocess regression verifies both file identities. The retained failed fixture was explicitly retried after this fix; no new environment was allocated.

## Verification and limits

- A real native-writer fixture publishes and fsyncs success, then is killed with SIGKILL before guardian receipt completion. Startup settlement records success with the original attempt count, without replay. A surviving-worker settlement attempt is refused first.
- Missing, partial and mismatched native evidence cannot settle a running claim.
- Independent lease tests reject a new open description while an inherited holder remains. The actual worker-death cleanup fixture also verifies refusal during guardian cleanup.
- All 76 Python tests, 67 Bun tests with 377 assertions, and worker/receipt strict types pass.
- [Twelve live checks](evidence/worker-receipt-checks.json) exercise the real supervisor and native capacity-refusal witness, verifying exact claim identity and unchanged allocation. All four environments respond; all owned runtimes stop afterward. The live Docker check covers refusal, while native success/crash recovery uses a harmless temporary fixture.

This proves process-crash recovery of a native reported outcome. Fsyncing its witness alone does not prove earlier configuration files or database/storage changes survive host power loss. Unknown native failures and daemon-side effects still require explicit inspection and reconciliation. No generic receipt-clearing command or automatic replay of uncertain effects has been added.
