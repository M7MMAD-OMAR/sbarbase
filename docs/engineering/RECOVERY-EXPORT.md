# Independent recovery: export checkpoint

This prepares the input for a separate-cluster rehearsal. No target restore has run yet and the archive is not a production backup product.

## Captured and verified

`lab/recovery-export.py` selects the first ready durable probe environment through the local catalog. It captures the database dump, three scoped login definitions and memberships, database ownership/ACL/settings, canonical API role defaults, environment credentials, pinned image identities, file bytes/xattrs, tenant configuration and that tenant's URL-signing keys.

Signing keys are decrypted inside the pinned source Storage process and enclosed in the new AES-256-GCM artifact. The shared platform encryption key and administrator credentials are not intentionally added to the configuration payload. This is not a scan proving those values cannot be embedded inside application database contents.

[29 live export checks](../evidence/recovery-export-checks.json) passed: 3 roles, 2 objects and 1 signing key, a 167,743-byte database dump and a 307,077-byte encrypted artifact. The archive round trip matched its input. Thirty-four Python tests pass, including wrong-key/corruption rejection, strict envelope format and subprocess output-size rejection.

## Consistency and resource scope

The operation lock excludes concurrent durable CLI lifecycle changes. Auth, REST, management Auth and Storage are stopped before the database dump and read-only file snapshot. Source tenant/signing state is checked across that stop. Selected-database client sessions, prepared transactions, replication slots, enabled subscriptions and active cron jobs must be absent. Unsupported S3 credentials and Iceberg/shard state are checked before and after quiescence.

These are checks for the trusted local fixture, not a general write fence against a host operator or a new direct SQL connection. Files are read by a temporary network-disabled helper limited to 128 MiB and 0.25 CPU. Only the source database remains running during capture. The helper is cleaned up by exact name and ownership, and the source runtime is stopped afterward with volumes retained.

Command stdout is bounded while reading. The encrypted JSON payload is limited to 32 MiB after serialization; this is still an in-memory small-fixture format, not a scalable streaming backup. Vault, function artifacts and external object-store state are not covered.

## Private artifacts and next consumer

Start the owned durable runtime, then run `/usr/bin/python3 lab/recovery-export.py`. It leaves the source stopped. `.lab/upstream/recovery-latest.json` points to the latest ciphertext and separately stored key. Both are private, Git-ignored and mode 0600. They are excluded from the handoff ZIP. Earlier encrypted attempts are retained privately.

The next consumer must authenticate/decrypt before creating resources, validate the source identity and supported dependencies, then restore onto a fresh pinned PostgreSQL cluster. Do not assume three exported roles cover arbitrary custom grantors, owners or extensions. Reconstruct restricted HBA and connection settings rather than copying a broad cluster configuration. Rebind source database addresses, re-encrypt signing keys under a fresh target Storage key and preserve their key IDs. Verify old signed URLs, identities, RLS, objects and source/neighbor invariants before calling the rehearsal successful.

A read-only adversarial review identified partial-stop cleanup, quiescence, post-allocation size checks and overly broad secret-exclusion wording. Those were corrected or explicitly scoped. The earlier empty-environment selection error was fixed by selecting the catalog's populated durable fixture.

## Optional cutover fence

`cutover-export.py` adds the two-stage database fence described in SOURCE-FENCING.md. It journals original scoped login intent, disables service logins before snapshotting and closes all database connections after saving the encrypted artifact. Source service shutdown still affects the complete shared local stack. The coordinated entry point now sets catalog maintenance and has passed a full source export; direct standalone cutover mode was removed after review. Default 29-check export evidence predates this option. Preserve journals and explicitly reconcile interrupted operations; never silently reopen source writes.
