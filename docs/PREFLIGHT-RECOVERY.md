# Bounded preflight recovery

Implemented and verified locally on 2026-09-20. This is process-crash recovery before external mutation, not general provisioning rollback or host power-loss recovery.

## Protocol and decision

New durable provisioning receipts advertise stageProtocol 1. Under the operation lock, the native provisioner persists per-token stages: preflight, database, services, storage, publication. The database marker is fsynced before the first external database mutation. Stage writes only advance; publication of a matching native completion witness remains the preferred settlement path.

Replacement-worker startup holds fresh effect ownership and the operation lock. Only ENOENT for the native outcome allows fallback to stage evidence. Exact protocol, token, native entry point and full job identity must match a preflight record. Corrupt, unreadable, legacy, missing-stage or later-stage evidence blocks automatic recovery.

The catalog commits an exact-claim recovery decision and requeue together before receipt consumption. Replaying that decision after a crash cannot alter a newer claim or spend another retry. Each environment has at most two automatic preflight requeues over its lifetime; subsequent interrupted preflight attempts become runtime_failed for explicit intervention. An explicit retry does not reset that budget.

Retained credentials remain stable but do not prove admission. Every worker dispatch repeats count, resource, pressure and connection admission before crossing the database boundary. Startup resumes only endpoint-published, unfenced environments. Credential-only reservations remain for the worker, whose claim rechecks current actor and organization authority.

## Evidence and review

- All 87 Python tests pass, including write-ahead ordering, failed boundary persistence, retained-credential admission and startup reservation exclusion.
- All 73 Bun tests pass with 408 assertions. Real temporary native processes are killed at preflight and database markers: only preflight requeues. These fixtures do not execute Docker provisioning or establish the entire supervisor crash path.
- Exact claim, commit-before-consumption, bounded retries, historical decisions, revoked authorization, malformed evidence and later stages are covered.
- Worker and receipt strict TypeScript checks pass.
- Thirteen live checks reuse the existing failed capacity fixture through the real combined supervisor. A native refusal preserves the matching preflight record, no environment or container is allocated, all four existing environments respond, and all owned runtimes stop afterward. See evidence/worker-receipt-checks.json.
- Adversarial review found admission bypass through retained credentials and startup dispatch outside the authorized worker. Both are fixed; follow-up code review found no remaining must-fix in this scope. Review did not rerun tests.

## Limits and next work

Database, services, Storage and publication stages remain blocked without a matching completion witness. The read-only inspector does not yet describe stage-specific recovery decisions; inspect this protocol before interpreting an unresolved report. No generic clear-receipt operation exists.

Next extend the inspector with exact stage evidence and exercise a full supervisor crash during active provisioning without touching retained production-like fixtures. Multi-host coordination, external backup storage, production upgrades and measured 10/100-project capacity remain separate unfinished gates.
