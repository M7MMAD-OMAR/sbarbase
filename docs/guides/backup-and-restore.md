[العربية](backup-and-restore.ar.md)

# Backup and restore

## Daily backups of every environment

Sbarbase backs up every environment once a day, while it keeps serving, and keeps the last seven backups of each. Nothing needs to be set up. Each backup holds the environment's database (users, password hashes, tables, Storage metadata) and its own Storage files, with a digest of each. A failed backup sends a notification through the operator channels.

| Setting | Default | Meaning |
|---|---|---|
| `SBARBASE_BACKUP_HOUR` | `3` | UTC hour of the daily run; `off` turns it off |
| `SBARBASE_BACKUP_KEEP` | `7` | Backups kept per environment |

Set them in `compose.yaml` (Docker) or in the unit's environment (systemd).

Commands (with Docker, prefix `docker compose exec sbarbase`):

| Task | Command |
|---|---|
| Back up one environment now | `python3 lab/backup.py create <environment>` |
| Back up every environment now | `python3 lab/backup.py create all` |
| List backups | `python3 lab/backup.py list` |
| Restore an environment | `python3 lab/backup.py restore <environment> <backup>` |
| Drop what a restore set aside | `python3 lab/backup.py discard-previous <environment>` |

`<environment>` is the environment id from the console, or its runtime id (`e_...`, the path in its API URL). `<backup>` is the time shown by `list`, such as `20260924T030000Z`.

**Restore** puts one environment back exactly as it was in that backup: rows, users and files written afterwards are gone. Only that environment's Auth and REST pause for the restore; every other environment and the console keep working. The state being replaced is kept aside, not deleted, and any failure during the restore puts it back automatically. When you are satisfied, `discard-previous` removes it.

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

CI runs the full cycle on every change: back up while serving, change rows, users and files, restore, check that everything matches the backup, and discard the set-aside state.

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

- No schedule, no retention policy, no off-host transfer, no point-in-time recovery.
- Every backup path above means downtime for all environments on the engine, not only the one being saved.
- Restore has been rehearsed on one host with a test fixture, not on a server with real client data.
- Once a restored target has accepted writes, going back to the old source is unsafe without reconciliation.

The full record, with check counts, is [independent restore](../engineering/INDEPENDENT-RESTORE.md).
