[العربية](upgrades.ar.md)

# Upgrades

## Upgrade an installation

One command moves an installation to a newer Sbarbase version, and a restart starts it. If the new version does not start, Sbarbase moves back to the previous one by itself. With Docker, put `docker compose exec sbarbase` before each `python3` command.

| Task | Command |
|---|---|
| See what would change, without changing anything | `python3 lab/upgrade.py check` |
| Upgrade to the latest version | `python3 lab/upgrade.py start` |
| Then restart onto it | `docker compose up -d --build` (or `sudo systemctl restart sbarbase`) |
| See the outcome | `python3 lab/upgrade.py status` |
| Go back to the version before | `python3 lab/upgrade.py rollback`, then restart the same way |

`start` goes to `origin/main` by default; `--to <tag or commit>` picks another version. Before it moves anything it:

1. refuses if the checkout has local changes, or if the new version changes the PostgreSQL image (that is a database migration, not an upgrade; see below);
2. pulls every image the new version pins, so a missing download never stops a running installation;
3. backs up every environment ([backup and restore](backup-and-restore.md)); the backups stay afterwards.

On the restart, only Auth, REST and Storage containers whose pinned image or configuration changed are replaced. They keep no data of their own: users, rows and files stay in the database and the file volume, which are never touched. If that start fails, the supervisor moves the checkout back, exits, and the restart policy brings up the previous version with its previous images. `status` then says `rolled_back`, and the backups taken before the upgrade are there if you need them.

CI runs this on a clean machine with every change: a real upgrade to a newer PostgREST, then a broken version that never starts, which Sbarbase moves back from by itself, with users, files and buckets compared before and after.

## Changing a pinned upstream version

This section is for maintainers. Sbarbase runs pinned upstream Supabase images. Changing one pin is deliberate, with a written review and the full test gate. **No upstream release has been adopted through this process yet.** The binding rules are in the [upstream update policy](../engineering/UPSTREAM-UPDATE-POLICY.md).

## What is pinned

Every upstream image is pinned by tag and digest in four lock files: `lab/distro-image.lock.json` (the Supabase PostgreSQL image), `lab/images.lock.json` (Auth, PostgREST and the stock PostgreSQL used by fixtures), `lab/storage-image.lock.json` (Storage) and `lab/studio-image.lock.json` (Studio and postgres-meta). Print the whole set and check that nothing floats:

```
/usr/bin/python3 lab/pin_update.py show
/usr/bin/python3 lab/pin_update.py verify
```

## Changing one component

1. Read the upstream release notes for the candidate version.
2. Stage the change. This writes a dated review entry under `docs/upstream/`, records the previous digest as the rollback pin and changes exactly one component:

   ```
   /usr/bin/python3 lab/pin_update.py stage --file lab/images.lock.json --component rest \
       --tag public.ecr.aws/supabase/postgrest:vX.Y --digest sha256:<64 hex> --note "why"
   ```

3. Complete the review entry: what changed, which Sbarbase surfaces it touches, breaking changes and migrations, and the adopt or defer decision.
4. Pass the adoption gate: the complete Python and Bun suites, and the live integration checks against the new version. No suite, no adoption.
5. Adopt one component at a time, so a failure points to one version change.

## Rolling back a pin

Restore the previous pin recorded in the review entry and ship it as a new version; installations reach it with `lab/upgrade.py`. If the newer version migrated data, rolling back may need a data migration of its own; record it in the same entry.

## Database containers are special

Startup never recreates a database container as an implicit upgrade. A managed database container is pinned by its exact identity, and replacing it (for a new image, for example) goes through `lab/migrate-generation.py`, which is journaled and crash-tested on disposable fixtures. Its first attended run on retained data has not happened yet; see [status](../reference/status.md).

## Limits

- There is no upgrade rehearsal on a server with real client data.
- Going back does not undo a database change a newer Auth or Storage made at start. Upstream migrations add to the schema, so the previous version normally runs on it; if it does not, restore the backups taken before the upgrade.
- A new version that fails in a way the supervisor cannot see (it starts, but misbehaves) is not moved back by itself. Use `rollback`.
