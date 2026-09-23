# Backup and restore

What exists today is a manual, attended procedure. **Scheduled backups and off-host copies are not built.** Nothing in Sbarbase copies your data anywhere on its own; if you need an off-host backup now, you have to arrange it yourself from the steps below. For the design behind this, read [recovery](../explain/recovery.md).

## Whole-server cold backup

The simplest backup is a cold copy of everything, taken with all services stopped:

1. Stop the supervisor (`systemctl stop sbarbase.service` on a server, Ctrl+C for `lab/dev.py`), and confirm no `sbarbase-durable-*` container is running with `docker ps`.
2. Copy the Docker volumes used by the `sbarbase-durable-*` containers (find them with `docker inspect`), plus the checkout's private state directories `.lab/` and `.secrets/`. Keep the copy encrypted and off the server; `.secrets/` holds every generated credential.
3. Start the supervisor again.

This copies the whole server at once. Restoring it restores every environment to that moment; it cannot restore one environment alone. This procedure has not been rehearsed as a restore on a clean host.

## One environment: encrypted export

`lab/recovery-export.py` writes an encrypted bundle of one environment: its database dump, scoped logins, grants and settings, files with extended attributes, Storage tenant configuration and URL-signing keys. Start the owned runtime first (`/usr/bin/python3 lab/durable_runtime.py up`), then run `/usr/bin/python3 lab/recovery-export.py`. It stops the source services while it captures and leaves them stopped. The ciphertext and its separately stored key are written under `.lab/upstream/` with mode 0600, and `.lab/upstream/recovery-latest.json` points to them.

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
- Restore has been rehearsed on one host with a test fixture, not on a server with real client data.
- Once a restored target has accepted writes, going back to the old source is unsafe without reconciliation.

The full record, with check counts, is [independent restore](../engineering/INDEPENDENT-RESTORE.md).
