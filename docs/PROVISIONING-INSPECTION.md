# Inspecting unresolved provisioning

Run `/usr/bin/python3 lab/inspect-provisioning.py` from the repository. It prints a sanitized JSON observation of the upstream installation. It never starts or stops containers, changes catalog jobs, settles receipts, or authorizes replay. It does not construct the runtime or read credential files.

The inspector independently opens the existing worker, effect and operation lock files, in that order, with no creation and no wait. Busy or missing locks produce an unavailable snapshot rather than evidence of inactivity. Receipt and witness records are validated, their full job identity is compared against a read-only SQLite transaction, and historical outcomes are distinguished from the current claim. Raw claim/token material is omitted from output.

Docker inventory is selected by exact owner labels and verified against full inspected container IDs. Output is restricted to identity, ownership, running/OOM state and the number of reported exec IDs. Environment variables and error bodies are not returned. Failed Docker calls mean unavailable observations, not absent resources. Lack of a label-matched source means the owned source was not observed, not proof that no foreign container occupies its name.

If a valid receipt identifies an environment and the owned source database is already running, the inspector reads only database existence, connection permission, session count and scoped-role count. SQL runs in a READ ONLY transaction with a two-second statement timeout and half-second lock timeout. A stopped database is not started. Docker calls have a ten-second timeout.

## Interpretation

- `busy` or unavailable evidence: obtain a new snapshot later; do not infer completion.
- `settle_known_outcome_under_fresh_lease`: a matching known outcome is observable. The existing settlement path must still acquire fresh ownership and validate it again.
- `inspect_unresolved_effects`: there is no sufficient known completion proof. Resources may reflect partial work; do not delete the receipt or requeue the job.
- `safe_to_replay` is always false. This report is not mutation authority or a reusable approval token.

Host operators and direct Docker/SQL clients are outside these locks. PostgreSQL statistics and daemon state may change during observation. Healthy containers, zero observed sessions and matching metadata do not prove an unknown daemon-side effect has ended.

SQLite uses `mode=ro`, query-only mode and a read transaction, not `immutable=1`. This prevents logical catalog writes but can involve SQLite WAL/shared-memory sidecars for a WAL catalog. The unchanged-file tests and live hashes use the current DELETE-journal catalog and do not promise filesystem immutability for every SQLite configuration.

## Verification

Six isolated tests cover unchanged fixture bytes, exact evidence binding, absent/busy locks, redaction, unavailable Docker, ownership collision, bounded read-only SQL, malformed evidence and strict integer protocol versions. Adversarial review corrected Python's boolean-version acceptance and an overstrong resource-absence description.

[Seven live checks](evidence/provisioning-inspection-checks.json) observe 19 owned containers, all stopped, with no pending receipt. Catalog, journals, endpoints and native-witness hashes remain unchanged. No database query was attempted in that stopped fixture. Pending-state and live-database query branches are covered by isolated tests, not this live check. All 82 Python tests pass; unchanged Bun checkpoint remains 67 tests and 377 assertions.

Next: define stage-specific evidence and recovery actions for genuinely partial effects. Inspection alone does not resolve them.
