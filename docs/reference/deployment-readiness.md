[العربية](deployment-readiness.ar.md)

# Deployment readiness

Updated 2026-11-13. The owner console update is confirmed; full production qualification remains open. [Status](status.md) identifies the public release and the separately installed revision.

## Current acceptance boundaries

| Area | Observed result | What remains open |
|---|---|---|
| Existing owner installation | Guarded console-only update confirmed; console and native Studio answered on their public addresses | A fresh public installation and other host profiles |
| Environment access | Auth health and REST returned 200 for all 4 environments with existing publishable keys | Representative application workload and sustained capacity |
| UI | Direct switching, draft protection, theme-aware menus, persistent navigation and tab identity checked | Browser-specific native-picker fallback and broader usability acceptance |
| Database and images | Existing database container retained; running image identities unchanged | PostgreSQL 17.11 and a newly qualified Supabase image set |
| Recovery | Fresh backups for 6 scopes, checksums and archive readability verified; upgrade guard confirmed the source revision | Full restore from these backups, complete offsite/fresh-host recovery and disaster drills |
| Studio email settings | Pinned native form save/readback, applied Auth configuration and one accepted email submission | Other Studio images, unrelated hosted auth settings and inbox placement |
| PGMQ | 0.13.1 defers the repair; legacy queue backup, supplement restore and the qualified repair pass on a disposable database | `repair-pgmq` and a supplement restore on a real installation with queues; partitioned queues |

The [deployment observation](../evidence/server-console-2026-11-13.json) records only the console revision and the observations it names. It is not a claim that all original Supabase services or experimental native placement are qualified for production.

## Before applying another update

1. Confirm the running commit, a clean tracked checkout, settled operations and adequate capacity.
2. Verify the target's trusted signature and actual diff. Check database, image, dependency and service changes separately.
3. Take the required backups and verify their manifests, hashes and archive readability. A readable archive is not a completed restore rehearsal.
4. Follow the update class and its documented path. A manual migration cannot be made safe by removing a manifest entry.
5. Keep the previous source and guard snapshot available, apply through the supported operator workflow, and observe confirmation or recovery.
6. Check the console, original Studio, environment authorization and application endpoints after startup.

See [upgrades](../guides/upgrades.md), [backup and restore](../guides/backup-and-restore.md) and [server deployment](../guides/server-deployment.md).

## Storage migration qualification

The [private Storage credential workflow](../guides/backup-and-restore.md#private-storage-credentials) and native-profile work qualify bounded runtime roles, migration custody and installed-version restore behavior. They do not accept serving cutover, full-host recovery, a recovery-point objective, load capacity or retirement of another installation. Those require their own evidence.

<a id="private-storage-migration-qualification-2026-11-08"></a>
<a id="current-checkpoint-2026-10-19"></a>
<a id="checkpoint-2026-10-09"></a>
<a id="previous-integration-checkpoint-2026-10-07"></a>
<a id="historical-evidence-before-the-current-integration"></a>
<a id="historical-readiness-matrix"></a>
<a id="rehearsal-order-on-a-selected-linux-server"></a>
<a id="historical-legacy-rehearsal-gaps-documentation-checkpoint-2026-10-06"></a>
<a id="preserved-october-verification-history"></a>

## Historical evidence

Earlier dated checklists are retained in [readiness history](deployment-readiness-history-2026-11-13.md), [status history](status-history-2026-11-13.md) and [engineering checkpoints](../engineering/checkpoints.md). Their source revisions, environments and omissions remain part of each result.
