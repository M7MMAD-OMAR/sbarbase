[العربية](backup-and-restore.ar.md)

# Backup and restore

## Daily backups of every environment

Sbarbase backs up every environment once a day, while it keeps serving, and keeps the last seven backups of each. Nothing needs to be set up. Each backup holds the environment's database (users, password hashes, tables, Storage metadata) and its own Storage files, with a digest of each. A failed backup sends a notification through the operator channels.

| Setting | Default | Meaning |
|---|---|---|
| `SBARBASE_BACKUP_HOUR` | `3` | UTC hour of the daily run; `off` turns it off |
| `SBARBASE_BACKUP_KEEP` | `7` | Backups kept per environment. Backups taken for an upgrade are not counted: those of the last 3 upgrades that moved the checkout are kept ([upgrades](upgrades.md)) |

Set them in `compose.yaml` (Docker) or in the unit's environment (systemd).

Commands (with Docker, prefix `docker compose exec sbarbase`):

| Task | Command |
|---|---|
| Back up one environment now | `python3 lab/backup.py create <environment>` |
| Back up every environment now | `python3 lab/backup.py create all` |
| List backups | `python3 lab/backup.py list` |
| Restore an environment | `python3 lab/backup.py restore <environment> <backup>` |
| Drop what a restore set aside | `python3 lab/backup.py discard-previous <environment>` |
| Restore Storage's shared metadata | `python3 lab/backup.py restore-storage <backup>` |
| Drop what that restore set aside | `python3 lab/backup.py discard-previous storage` |
| List the sets on the off-host target | `python3 lab/backup.py offsite-list` |
| Bring one set back from the target | `python3 lab/backup.py offsite-fetch <backup>` |
| Restore from the target | `python3 lab/backup.py restore <environment> <backup> --offsite` |

`<environment>` is the environment id from the console, or its runtime id (`e_...`, the path in its API URL). `<backup>` is the time shown by `list`, such as `20260924T030000Z`.

**Restore** puts one environment back exactly as it was in that backup: rows, users and files written afterwards are gone. Only that environment's Auth and REST pause for the restore; every other environment and the console keep working. The state being replaced is kept aside, not deleted, and any failure during the restore puts it back automatically. When you are satisfied, `discard-previous` removes it.

CI runs the full cycle on every change: back up while serving, change rows, users and files, restore, check that everything matches the backup, and discard the set-aside state.

**The installation manifest.** Each daily run also writes `.lab/backups/installation/<backup>/`: the pinned images, the routing of every environment, the catalog's clients, projects, environments, memberships and jobs, the operator settings, and the names of the files in `.secrets/`. It holds no secret value: it is built from lists of allowed fields, and a webhook address is reduced to its host. It carries a digest and is kept as long as the environment backups. On a new server it tells you which pins and secrets the backups need.

**Storage's shared metadata.** Every `create all` run, the daily one and the one before each upgrade, also backs up `storage_metadata`, the one database Storage keeps for every environment: each environment's Storage registration, its signing keys and the migration state Storage records for it. It goes to `.lab/backups/storage/<backup>/`: a dump taken inside one snapshot while Storage serves, and its own manifest with a digest and the ids of the environments it registers, nothing secret. A failed dump fails the run. `restore-storage <backup>` replaces the whole database with it. Storage stops for every environment while it runs; the current database is kept aside until `discard-previous storage`, and any failure puts it back. It refuses a backup that does not register an environment published now, because that environment's Storage would lose its registration. Restore it with the environment backups of the same run, environments first: the migration state it records has to match their databases ([upgrades](upgrades.md#releases-that-need-your-confirmation)). It travels in the encrypted run set below; the per-environment copies of `lab/offsite.py` do not include it.

There are two independent ways to copy backups off the server, described below. Configure one of them.

## Copies off the server

Backups are written to `.lab/backups/` on this server. To survive losing the server, let Sbarbase copy each new backup, encrypted, to S3-compatible storage: Cloudflare R2 (10 GB free), Backblaze B2, AWS S3, Wasabi or your own MinIO.

1. Create a bucket and an access key that can read, write, list and delete in it.
2. Give Sbarbase the settings once. They are read from stdin, never from the command line, and kept in `.secrets/offsite.json`:

```bash
python3 lab/offsite.py configure <<'JSON'
{"endpoint": "https://<account>.r2.cloudflarestorage.com", "bucket": "my-backups", "region": "auto",
 "access_key_id": "…", "secret_access_key": "…", "passphrase": "a long phrase only you know", "keep": 30}
JSON
```

It proves the settings by writing, reading and deleting a test object. From then on the daily backup copies each new backup by itself and keeps the newest 30 per environment in the bucket; a failed copy is reported like a failed backup.

Every file is encrypted on this server before it leaves (AES-256-GCM, the key derived from your passphrase), so the storage provider never sees your data. **Keep the passphrase somewhere other than this server**: without it the copies cannot be read, by you or anyone.

| Task | Command |
|---|---|
| Copy what is not copied yet | `python3 lab/offsite.py push` |
| List the copies in the bucket | `python3 lab/offsite.py list` |
| Bring a copy back to this server | `python3 lab/offsite.py fetch <environment> <backup>` |

A fetched backup is checked against its manifest and then restored with `backup.py restore` as usual. An upgrade's own safety backup stays local (`backup.py create all --local-only`), so storage that cannot be reached never blocks an upgrade.

CI runs this cycle on a clean machine with every change against a throwaway MinIO: a backup copied by itself, only ciphertext in the bucket, a wrong passphrase refused, the copy fetched and restored ([evidence](../evidence/docker-offsite-checks.json)).

## Encrypted run sets off the server

Backups are written to `.lab/backups/` on this server. To keep a copy elsewhere, point Sbarbase at one S3-compatible bucket (Amazon S3, Cloudflare R2, Backblaze B2, MinIO and others). After each daily run, the run's backups and its installation manifest are packed into one file, encrypted with AES-256-GCM, and uploaded as `<prefix><backup>.sbb`. The target keeps as many sets as `SBARBASE_BACKUP_KEEP`; older sets under the same prefix are deleted, and other objects are never touched.

1. Create the encryption key. Sbarbase never creates one on its own:

   ```bash
   python3 lab/backup.py offsite-key .secrets/upstream/offsite-key.json
   ```

   The file is mode 0600. **Keep a copy of it away from this server**: without it no copy can be decrypted, and anyone with it and the bucket can read every backup.
2. Put the bucket's access key in a 0600 file, for example `.secrets/upstream/offsite-s3.json`:

   ```json
   {"schema": 1, "accessKeyId": "<access key id>", "secretAccessKey": "<secret access key>"}
   ```

   Give that key only list, read, write and delete rights on the bucket.
3. Write `.lab/upstream/backup-offsite.json`:

   ```json
   {
     "schema": 1,
     "s3": {"endpoint": "https://s3.eu-central-1.amazonaws.com", "region": "eu-central-1",
            "bucket": "example-backups", "prefix": "sbarbase/",
            "credentialsFile": ".secrets/upstream/offsite-s3.json"},
     "keyFile": ".secrets/upstream/offsite-key.json"
   }
   ```

   The endpoint must use `https`. Paths are relative to the checkout.

The next daily run copies itself. A copy that fails (the bucket is unreachable, the key file is missing or readable by others) sends a `backup.failed` notification and prints the reason; the local backups are never changed or removed, and nothing is deleted on the target. The run can then report both a completed backup and a failed copy.

Only the daily run (`create all`) is copied; a backup of one environment taken by hand stays on this server.

**Restore from the target**, on this server or on a new one with the same environment published. A new server needs the three files above first: the configuration, the credentials file and your copy of the key.

```bash
python3 lab/backup.py offsite-list
python3 lab/backup.py restore <environment> <backup> --offsite
```

The set is downloaded, its authentication tag is checked before anything is unpacked, and each backup is checked against its digests before `restore` uses it. A backup already on this server is kept as it is.

## Restore on a new installation

A backup restores into an environment that exists and is published. On a new server, after losing the old one, that environment does not exist yet. Each backup records the ids of its client, project and environment (`ownership` in `manifest.json`, since 2026-09-23), so the new installation can recreate them exactly and then restore into them:

1. Install Sbarbase on the new server ([operator setup](operator-setup.md)) at the same release, so the pinned images match; the installation manifest lists them.
2. Put the backup under `.lab/backups/<runtime>/<backup>/`: copy the directory from wherever you keep it, or fetch it from the off-host target as described above.
3. Run the three steps below as an installation operator. `relink` recreates the client, project and environment with their original ids and runtime id, and the worker provisions an empty environment of that name. When `environments` shows it `succeeded`, `restore` fills it from the backup.

```bash
sbarbase relink <runtime> <backup>
sbarbase environments
sbarbase restore <runtime> <backup>
```

`relink` is careful on purpose. It never attaches a backup to a client or project by name: when a client with the recorded name but another id exists here, it refuses, and so it does when the recorded project or environment id already means something else here, or the runtime id belongs to another environment or to one deleted on this installation. A refusal changes nothing. You become the owner of a client it creates; if the recorded client already exists here, you must already be one of its owners. Asking again after a success changes nothing.

What a backup does not carry, and what you do after the restore:

- **Members.** Account ids belong to the old installation's sign-in, so nobody is added. Invite people again.
- **API keys.** Issue new publishable keys and update your apps.
- **The JWT signing key.** The new environment has its own. Users sign in again with their old passwords, which the backup keeps; existing sessions and signed Storage URLs stop working.
- **Direct database access, Studio, Realtime and Edge Functions.** Turn them on again where you used them; database access gets a new password.

This path is covered by unit tests of the catalog, the API and the command, and it was rehearsed end to end on 2026-09-25: two backups taken in one VM while both environments served traffic were restored onto a second, freshly installed VM with these three commands, and a user from each old environment signed in with their old password through a new key ([evidence](../evidence/vm-restore-drill.json)). Those environments had never used Studio, Realtime or direct database access. One risk stays untested: such an environment has logins for those features that its database grants access to, a new installation does not have them until the feature is turned on, and the restore may refuse. Turn the feature on for the new environment before you restore into it.

## Whole-server cold backup

The simplest backup is a cold copy of everything, taken with all services stopped:

1. Stop everything: the supervisor (`systemctl stop sbarbase.service` on a server, Ctrl+C for `lab/dev.py`), a runtime started by hand (`/usr/bin/python3 lab/durable_runtime.py stop`) and, if an environment has been moved to a recovery target, that target (`/usr/bin/python3 lab/target_runtime.py stop`).
2. Confirm with `docker ps` that no `sbarbase-durable-*` and no `sbarbase-restore-*` container is running.
3. Copy the Docker volumes of **both** the `sbarbase-durable-*` containers and the `sbarbase-restore-*` containers (find them with `docker inspect`), plus the checkout's private state directories `.lab/` and `.secrets/`. A moved environment's data lives only on the `sbarbase-restore-*` volumes, so copying the durable volumes alone misses it. Keep the copy encrypted and off the server; `.secrets/` holds every generated credential.
4. Start the supervisor again.

This copies the whole server at once. Restoring it restores every environment to that moment; it cannot restore one environment alone. This procedure has not been rehearsed as a restore on a clean host.

## One environment: encrypted export

`lab/recovery-export.py` writes an encrypted bundle of one environment: its database dump, scoped logins, grants and settings, files with extended attributes, Storage tenant configuration and URL-signing keys. Start the owned runtime first (`/usr/bin/python3 lab/durable_runtime.py up`), then run `/usr/bin/python3 lab/recovery-export.py`.

**Exporting one environment currently takes every environment on that engine offline.** To get a consistent snapshot, the export stops Auth, REST, the management Auth realm and the shared Storage process for the whole source placement, not only for the environment being exported, and it leaves them stopped afterwards. Every app on the server and the console login are unavailable until you start the runtime again. Plan it as a maintenance window. The ciphertext and its separately stored key are written under `.lab/upstream/` with mode 0600, and `.lab/upstream/recovery-latest.json` points to them.

Be aware:

- It currently selects the first ready environment of the durable lab fixture; it is not yet a general "export environment X" command.
- The bundle is held in memory and capped at 32 MiB.
- `lab/cutover-export.py` is the fenced variant used when an environment is being **moved**. It puts the environment in maintenance, disables its service logins and closes its source database afterwards. It leaves the source fenced on purpose, and reopening it is a deliberate reconciliation step. Do not use it as a routine backup command.

## Restore into a separate engine

The restore path is a chain of scripts that were built against the lab fixture:

```
/usr/bin/python3 lab/recovery-restore-db.py          # fresh pinned engine, transactional restore, verification
/usr/bin/python3 lab/recovery-check-services.py      # original Auth and REST against the restored database
/usr/bin/python3 lab/recovery-check-storage.py       # Storage metadata, files and signing keys
```

The target descriptor is private (`.lab/upstream/recovery-target.json`). Moving traffic to the restored copy is done through the routing record with `lab/target_runtime.py`; see [target lifecycle](../engineering/TARGET-LIFECYCLE.md).

## If a restore is interrupted

A failed restore leaves its descriptor behind, and a plain rerun refuses. Do not edit that state by hand:

```
/usr/bin/python3 lab/recovery_reconcile.py                     # stop the retained target containers
/usr/bin/python3 lab/retire_recovery_target.py --reason "why"  # archive the descriptor, keep containers and volumes
/usr/bin/python3 lab/recovery-restore-db.py                    # a fresh restore may now run
```

## Limits

- The daily backup, its retention and the off-host copy are described at the top of this page. The limits in this list apply to it too: no point-in-time recovery.
- The cold backup and the encrypted export above mean downtime for all environments on the engine, not only the one being saved.
- The off-host copy speaks S3 only; SSH or rsync targets are not built. It is tested against a local fake bucket, not yet against a real provider.
- One set is one upload, so a set larger than the provider's single upload limit (5 GiB on Amazon S3) fails. The set is written encrypted to this server before the upload, so the disk needs room for it.
- Retention on the target is by count, not by age.
- Restoring needs the environment published on that server. On a new installation, `sbarbase relink` creates it first ([above](#restore-on-a-new-installation)); a backup taken before 2026-09-23 records no ownership and must be restored into an environment created by hand. Database roles shared by the whole engine are not in the set.
- Restore has been rehearsed on one host with a test fixture, not on a server with real client data. The `storage_metadata` backup and `restore-storage` have unit tests only; neither has run against a live Storage yet.
- Once a restored target has accepted writes, going back to the old source is unsafe without reconciliation.

The full record, with check counts, is [independent restore](../engineering/INDEPENDENT-RESTORE.md).
