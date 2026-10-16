# Whole-cluster restore onto a chosen image

Design note for upstream currency P3 ([plan](plans/2026-10-14-upstream-currency.md), section 3). This note fixed the approach before the code; each part below is its own reviewed checkpoint. Parts 1 (`lab/database_volume.py`, `--to-volume`) and 2 (`lab/cluster_inventory.py`) are built; parts 3 and 4 are not.

## What it is for

Two situations need the installation's database cluster rebuilt from backups, on an image the operator chooses, on the same host:

- The way back from the PostgreSQL step (A7, 17.6.1.166 to 17.11.0.004) when the newer image changed extension versions or catalogs, so the old image cannot run on the volume.
- A database move (`lab/upgrade.py database`, P1) that removed the old container and whose new image never becomes ready: every exit refuses until this exists ([upgrades guide](../guides/upgrades.md#moving-to-another-postgresql-image)).

Out of scope: moving to another host. That needs the credentials and the control state carried across, which is the fresh-host recovery work (`lab/installation_recovery.py`), not P3.

## Approach

The export is the supported backup run, not a new format. `lab/backup.py create all` already writes, in one run, every environment database (`pg_dump -Fc` inside one snapshot), `storage_metadata`, the management database and an installation manifest that records every image pin, each with sizes and SHA-256 digests. What a run lacked for a fresh cluster was the cluster level, the roles; part 2 adds it. P3 does not export role passwords: on the same host the runtime creates every scoped login from its own stored values (`lab/durable_runtime.py` `provision_database`, `storage_control`, `management_auth`, the Realtime and direct logins), so a fresh cluster provisioned by the runtime's own code has the same logins with the same passwords. What the run must record is which roles exist and their attributes, so the restore can prove the rebuilt cluster matches.

The rebuilt cluster lives on a new volume. Retained volumes are never removed, and the runtime named the database volume as a constant (`sbarbase-durable-pgdata`), so the volume became state the runtime reads (part 1), and the generation migration learns to move onto another volume as well as another image. The old volume stays, untouched, as the way back from the restore itself.

## Parts

Each is a checkpoint with unit tests and an independent review; none is accepted under the plan's section 10 until the rehearsal VM runs it.

1. **The database volume as state.** The runtime reads the database volume from a private record (absent: the current constant, so existing installations change nothing), checks a retained container against it, and refuses a record it cannot read. The generation migration intent can name a new volume (an intent version, like the image in P1), and the replacement is created on it; `create_replacement` and the adoption check (`replacement_identity`) require that volume. The runtime mounts only volumes it owns, so a rebuild creates its volume as `sbarbase-durable-pgdata-<12 hex>` with the runtime's owner label (part 3), and the move refuses any other, or one that holds no PostgreSQL data directory, before it touches the old container.
2. **The cluster inventory in each backup run.** The run records the roles the runtime manages, per environment kind (auth, rest, storage, studio, realtime, developer) and the cluster roles (`storage_control`, `management_auth`, `supabase_realtime_admin`), with their attributes, login state, connection limit, role-wide settings and the roles they are granted (with each grant's INHERIT, SET and ADMIN options, which the runtime relies on: Realtime gets the API roles WITH INHERIT FALSE), the parameter privileges granted to them (`GRANT SET ON PARAMETER log_min_messages`, cluster-level, which no dump carries), and the database image id the cluster ran. An allow-list rejects anything else, and attributes are checked per kind: REPLICATION only for Realtime, BYPASSRLS only where the runtime grants it. It is recorded in the run's installation manifest (`cluster`, from `lab/cluster_inventory.py`); a role that breaks its rule, or an inventory that could not be taken, is listed under `refusals` and the run still completes, so a wrong assumption here never stops backups, it only makes that run unusable for a rebuild. Roles the image creates are not listed (the image brings them back), and neither is a role someone created by hand. A run taken while Realtime migrates, when its login is briefly a superuser, records that as a refusal. Only role-wide settings are recorded: the PostgreSQL documentation for `pg_dump --create` says the archive carries the database's own access privileges and its `ALTER DATABASE ... SET` and `ALTER ROLE ... IN DATABASE ... SET` settings, and the restores here use `pg_restore --create` (`lab/restore_cutover.py`). That is taken from the documentation, not tested; the rebuild checks it.
3. **Rebuild into a fresh cluster.** From one backup run and a chosen image (the current distribution lock, or the lock of a named commit, proven on the local daemon and never pulled), a restore target (`sbarbase-restore-<12 hex>`, owner `recovery-target`) is created on a new volume, provisioned by the runtime's own functions, its HBA published through `TargetHBA`, then the management database, `storage_metadata` and every environment database are restored and checked against the run's digests, row counts and cluster inventory. It works from the backup files alone, with the installation's database stopped or gone: it must not need `lab/backup.py start-database`, which refuses while a migration record waits.
4. **Taking it into use.** An attended generation migration onto the target's volume and image replaces the installation's database container under its durable name; the old container's record and evidence are archived. A waiting P1 migration record (the stuck move) is retired by this step with its evidence archived, never deleted to get past a refusal. The old volume is kept.

## Limits

- Writes after the backup run are lost, which is why the A7 step and the move pause writes before the run (P5).
- Object files are not part of it: neither A7 nor the move touches the objects volume.
- Unit tested only until the rehearsal VM runs it; the host does not have the memory for it yet.
