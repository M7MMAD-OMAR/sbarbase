# Whole-cluster restore onto a chosen image

Design note for upstream currency P3 ([plan](plans/2026-10-14-upstream-currency.md), section 3). Nothing here is built yet; this note fixes the approach before the code, and each part below is its own reviewed checkpoint.

## What it is for

Two situations need the installation's database cluster rebuilt from backups, on an image the operator chooses, on the same host:

- The way back from the PostgreSQL step (A7, 17.6.1.166 to 17.11.0.004) when the newer image changed extension versions or catalogs, so the old image cannot run on the volume.
- A database move (`lab/upgrade.py database`, P1) that removed the old container and whose new image never becomes ready: every exit refuses until this exists ([upgrades guide](../guides/upgrades.md#moving-to-another-postgresql-image)).

Out of scope: moving to another host. That needs the credentials and the control state carried across, which is the fresh-host recovery work (`lab/installation_recovery.py`), not P3.

## Approach

The export is the supported backup run, not a new format. `lab/backup.py create all` already writes, in one run, every environment database (`pg_dump -Fc` inside one snapshot), `storage_metadata`, the management database and an installation manifest that records every image pin, each with sizes and SHA-256 digests. What a run lacks for a fresh cluster is the cluster level: the roles. P3 does not export role passwords: on the same host the runtime creates every scoped login from its own stored values (`lab/durable_runtime.py` `provision_database`, `storage_control`, `management_auth`, the Realtime and direct logins), so a fresh cluster provisioned by the runtime's own code has the same logins with the same passwords. What the run must record is which roles exist and their attributes, so the restore can prove the rebuilt cluster matches.

The rebuilt cluster lives on a new volume. Retained volumes are never removed, and the runtime today names the database volume as a constant (`sbarbase-durable-pgdata`), so the volume becomes state the runtime reads, and the generation migration learns to move onto another volume as well as another image. The old volume stays, untouched, as the way back from the restore itself.

## Parts

Each is a checkpoint with unit tests and an independent review; none is accepted under the plan's section 10 until the rehearsal VM runs it.

1. **The database volume as state.** The runtime reads the database volume from a private record (absent: the current constant, so existing installations change nothing), checks a retained container against it, and refuses a record it cannot read. The generation migration intent can name a new volume (an intent version, like the image in P1), and the replacement is created on it; `validate_replacement` and the adoption check require that volume.
2. **The cluster inventory in each backup run.** The run records the roles the runtime manages, per environment kind (auth, rest, storage, studio, realtime, developer) and the cluster roles (`storage_control`, `management_auth`, `supabase_realtime_admin`), with their attributes, memberships and role-wide settings, and the database image id the cluster ran. An allow-list rejects anything else, and attributes are checked per kind: REPLICATION only for Realtime, BYPASSRLS only where the runtime grants it. Whether `pg_restore --create` already carries database ACLs and per-database role settings for these dumps is proven by a test over a dump's table of contents before the inventory leaves them out.
3. **Rebuild into a fresh cluster.** From one backup run and a chosen image (the current distribution lock, or the lock of a named commit, proven on the local daemon and never pulled), a restore target (`sbarbase-restore-<12 hex>`, owner `recovery-target`) is created on a new volume, provisioned by the runtime's own functions, its HBA published through `TargetHBA`, then the management database, `storage_metadata` and every environment database are restored and checked against the run's digests, row counts and cluster inventory. It works from the backup files alone, with the installation's database stopped or gone: it must not need `lab/backup.py start-database`, which refuses while a migration record waits.
4. **Taking it into use.** An attended generation migration onto the target's volume and image replaces the installation's database container under its durable name; the old container's record and evidence are archived. A waiting P1 migration record (the stuck move) is retired by this step with its evidence archived, never deleted to get past a refusal. The old volume is kept.

## Limits

- Writes after the backup run are lost, which is why the A7 step and the move pause writes before the run (P5).
- Object files are not part of it: neither A7 nor the move touches the objects volume.
- Unit tested only until the rehearsal VM runs it; the host does not have the memory for it yet.
