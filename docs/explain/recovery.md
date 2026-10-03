[العربية](recovery.ar.md)

# Recovery

## What it is

The operator workflow provides daily environment backups, restore and optional encrypted S3-compatible offsite copy/fetch through [backup and restore](../guides/backup-and-restore.md). It is implemented in [lab/backup.py](../../lab/backup.py) and [lab/offsite.py](../../lab/offsite.py). Disposable local MinIO copy/fetch/restore evidence is recorded in [docker-offsite-checks.json](../evidence/docker-offsite-checks.json); this does not establish operation with a real external provider or complete lost-server recovery.

A separate historical cutover experiment stops one environment's writes, exports it encrypted, restores into an independent engine, checks the copy and switches traffic. The sequence and limits below describe that experiment, not every operator backup. Experimental native-dedicated recovery has additional engine/application/maintenance identity requirements and remains unaccepted; see the [native placement contract](../engineering/plans/2026-10-03-native-placement-identity.md) and [ledger](../engineering/gauntlet-ledger.json).

## Why the historical cutover uses a separate target

**Choice: restore into an isolated target, verify, then switch.** A restore that overwrites the live database in place leaves nothing to fall back to when it goes wrong. Restoring into a fresh engine keeps the source untouched (but fenced) until the copy has been checked, including identities, row-level security and old signed file URLs.

**Rejected alternatives.**

- **Replication as backup.** A replica copies mistakes and deletions immediately; it is not a backup.
- **Cluster-level point-in-time recovery.** PostgreSQL's physical recovery works on the whole engine, so restoring one environment would roll back every neighbour. Per-environment recovery needs a logical export of that environment's database plus its files and configuration.

## Historical cutover sequence

![Five steps: stop writes, encrypted export, restore into an independent engine, verify, switch the route; if verification fails the source data is untouched and stays fenced until an operator reopens it, and exporting stops shared Storage for every environment on the engine](../diagrams/restore-flow.svg)

*Verify before switching. Exporting still stops shared Storage for every environment on the engine.*


1. **Fence.** The environment's routing record is put in maintenance, so the gateway answers `503` instead of forwarding. Its three scoped service logins are switched to `NOLOGIN` and their original state is written to a journal, while the operator can still read and dump.
2. **Export.** `lab/cutover-export.py` captures the database dump, the scoped logins and their memberships, database grants and settings, file bytes and extended attributes, the Storage tenant configuration and its URL-signing keys. The bundle is encrypted with AES-256-GCM; the key is stored separately, both mode 0600 and out of Git.
3. **Close.** After the encrypted artifact is saved, the source database is set to refuse all connections. This is PostgreSQL state, so it survives restarts.
4. **Restore.** A fresh, pinned PostgreSQL engine with a new administrator password, its own network and its own volume receives the dump in one transaction. Storage metadata and files are rebuilt, and signing keys are re-encrypted under the target's own key while keeping their key IDs.
5. **Verify.** Table counts and content hashes are compared, scoped roles and grants are checked, original Auth and REST start against the copy, the original user logs in with the original password, reads rows protected by row-level security, and a signed URL issued before the export still downloads the same bytes.
6. **Switch.** The new placement is staged on the routing record and routing is resumed with a revision check.
7. **Neighbours.** Because shared Storage had to stop for the export, every environment on the source was offline during it. Unaffected environments now restart on the source while the moved environment stays pointed at the target.

Code: [lab/source_fence.py](../../lab/source_fence.py), [lab/cutover-export.py](../../lab/cutover-export.py), [lab/recovery_bundle.py](../../lab/recovery_bundle.py), [lab/recovery-restore-db.py](../../lab/recovery-restore-db.py), [lab/recovery-check-services.py](../../lab/recovery-check-services.py), [lab/recovery_reconcile.py](../../lab/recovery_reconcile.py), [lab/target_runtime.py](../../lab/target_runtime.py), [src/control/placement.ts](../../src/control/placement.ts). The operator procedure is in [backup and restore](../guides/backup-and-restore.md).

## Cutover fixture limits and remaining recovery gaps

- This independent-engine cutover was exercised on one host with a retained fixture. Its scripts are fixture-specific and do not define the general operator backup workflow.
- Daily backups and S3-compatible copies exist in the operator workflow. Point-in-time recovery and HA remain unavailable; those capabilities are not established by either path.
- It is a downtime procedure, and not only for the environment being recovered: a consistent export stops the shared Storage process and the other services of the whole source placement, so every environment on that engine, and the console login, is offline until the neighbours are restarted.
- Maintenance stops new requests, but requests already admitted by a gateway process can still be cut off when services stop.
- The historical cutover export bundle is held in memory and capped at 32 MiB; this fixture limit does not apply to every operator backup path.
- Once the target has accepted writes, switching back to the older source is unsafe and needs reconciliation. Automatic resume after the controller itself dies mid-operation is not proven.
- The cutover fixture does not cover Vault contents, function artifacts or external object stores.
- Complete recovery still requires coherent database/object capture under concurrent writes, installation members, keys and configuration, feature state and independently verified fresh-host recovery. Scoped copy/restore checks do not establish that complete contract.

## Go deeper

- [Independent restore](../engineering/INDEPENDENT-RESTORE.md): the full chronological record and its check counts.
- [Recovery export](../engineering/RECOVERY-EXPORT.md) and [source fencing](../engineering/SOURCE-FENCING.md).
- [Target lifecycle](../engineering/TARGET-LIFECYCLE.md), [persistent routing](../engineering/PERSISTENT-ROUTING.md) and the [storage recovery review](../engineering/reviews/storage-recovery.md).
