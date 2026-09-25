[العربية](upgrades.ar.md)

# Upgrades

Sbarbase tells you in the console when a newer signed release exists, and a safe release installs with one click. Every upgrade backs up your environments first, holds application traffic until the new version passes its health checks, and returns to the previous version by itself if it does not. You can also let safe releases install themselves inside a maintenance window. That is off by default.

Nothing here is production ready. The update channel (the release check, the console page, one-click install and automatic updates) was built on 2026-09-25 and so far is covered by unit tests only. **The live CI cases and the VM rehearsal for it have not run yet.** The upgrade runs recorded in CI and in the rehearsal VM used the command line path (`start --to`), not the channel, the console or automatic updates; see [what has been run](#what-has-been-run).

## Where you see an update

Only the installation operator (an owner or admin of the organization created at setup) sees anything about updates. Other members see nothing.

- **The notice.** When a check finds a newer release, a notice at the top of the console names the version and its class, for example "Sbarbase 0.2.0 ready to install". **View updates** opens the Updates page. The notice can be dismissed; it comes back for the next version.
- **The Updates page.** It shows the installed version and when the last check ran, with **Check now**. For an available release it shows the tag, the commit, whether it is signed, its class, the release notes, why it has that class, which pinned images change, and why it cannot be installed now if something blocks it. Below that: the last update and its outcome, and the update settings.
- **Notifications.** If [operator notifications](../reference/configuration.md) are set up, you also get `update.available` once per version, `update.applied` when an update is confirmed, `update.rolled_back` when a new version failed and the installation went back by itself, and `update.rollback_failed` (critical) when the way back failed too.

Sbarbase checks every 6 hours, starting 5 minutes after it starts. A check that fails (an offline host, for example) is retried after 30 minutes, then 1, 2 and 4 hours, then every 6 hours again. Checking never installs anything.

## Install a safe release from the console

1. Open **Updates** and read the release notes and the list of what changes.
2. Press **Install update**, then **Install now**. The button is available only for a release that is safe, signed and has nothing blocking it.
3. Stay on the page. It shows each stage: waiting for the server, backup and move, restart, health checks. The console disconnects for a few minutes while Sbarbase restarts and reconnects by itself.
4. When it says the update is installed and healthy, press **Reload console** so the page loads the new version.

If the new version does not become healthy, the page says so and Sbarbase is back on the version you had. If the page has no final outcome after 10 minutes, run `python3 lab/upgrade.py status` on the server.

## What happens, step by step

1. **The request.** The console cannot upgrade itself: it writes a request, and the supervisor (the process that runs everything) carries it out. The supervisor checks the request again and waits for a daily backup that is running. There is only ever one request at a time; a request nobody picked up within an hour is dropped.
2. **Checks.** `lab/upgrade.py start --release <tag>` fetches the release tag again, verifies its signature and computes its class again from the source. It refuses, with nothing changed, when the checkout has local changes to tracked files, when the release changes the PostgreSQL image, when an earlier upgrade has not finished starting, when a provisioning receipt or connection rule journal still needs settling, or when a backup or restore is running. Evidence the acceptance and the live checks wrote under `docs/evidence/` is not a local change: it is copied to `.lab/upgrades/evidence-<time>/` before the checkout moves.
3. **Images and backup.** It pulls every image the new version pins, so a missing download never stops a running installation, then backs up every environment on this server ([backup and restore](backup-and-restore.md)). These backups stay afterwards.
4. **Control snapshot.** It copies the control state (the control catalog, every SQLite store directly under `.lab/upstream/`, and the key store `.secrets/upstream/managed-keys.sqlite`) into `.lab/upgrades/snapshots/`, to prove it can be copied.
5. **The move.** It moves the checkout to the release commit and installs its dependencies.
6. **Restart.** The supervisor stops the console, the worker and the owned containers cleanly, then exits with code 42. systemd (`RestartForceExitStatus=42` in the unit) and Docker (`restart: unless-stopped`) start it again on the new version. From the command line you restart it yourself.
7. **Hold.** Before anything opens the control catalog, the new version takes a fresh snapshot of the control state; this is the one the way back restores. Until the health checks pass, application traffic waits: requests through the gateway get `503` with `Retry-After: 5`, and Realtime sockets and direct database access are refused. The console, its sign-in and `/health` still answer. The provisioning worker, the daily backup, Studio starts and sign-in changes wait too, and so does turning Realtime, Edge Functions or direct database access on or off, or rotating a signing key.
8. **Health checks.** Once the console process runs, one round of checks must all answer within 120 seconds: the console's own `/health` (which reads the control catalog), the management Auth, and for every environment in service its Auth, REST and Storage. When a round passes, the update is confirmed: traffic flows, the worker starts, and `update.applied` is sent.
9. **The way back.** If a start stage fails, or no round passes within 120 seconds, the supervisor restores the snapshot from step 7, moves the checkout back and exits with an error. The restart policy starts the previous version with its previous images. It goes through the same hold and health checks before it is recorded as `rolled_back`, and `update.rolled_back` is sent. If the previous version does not start either, the state is `rollback_failed`, `update.rollback_failed` is sent, and the way forward is to restore the backups from step 3.

On the restart only Auth, REST and Storage containers whose pinned image or configuration changed are replaced. They keep no data of their own: users, rows and files stay in the database and the file volume, which the upgrade never touches.

## The three classes

The class is computed from the difference between the commit you run and the release commit, not taken from what the release says about itself. A release can only make itself stricter, by declaring a data migration.

| Class | What decides it | What to do |
|---|---|---|
| Safe | Nothing below changes. Code, dependencies and the Auth, REST and Storage pins may change: a restart picks them up | Install from the console, from the command line, or let automatic updates do it |
| Needs a rebuild | `Dockerfile`, `.dockerignore`, `deploy/container/start.sh`, `compose.yaml` or `deploy/sbarbase.service` changes. A restart does not read these again | Install on the server, then rebuild (below). Never from the console or automatically |
| Needs a migration | The PostgreSQL image changes, or the release declares a data migration | Not an upgrade. Never from the console, automatically or with `start --release` |

For a release that needs a rebuild, with Docker:

```
docker compose exec sbarbase python3 lab/upgrade.py start --release vX.Y.Z --allow-class rebuild
docker compose up -d --build
```

With the systemd service, give `supervise --apply` the same options the service was installed with, so it installs the new unit, then restart:

```
/usr/bin/python3 lab/upgrade.py start --release vX.Y.Z --allow-class rebuild
sudo /usr/bin/python3 lab/install_server.py supervise --apply
sudo systemctl restart sbarbase
```

The restart then goes through the same snapshot, hold, health checks and way back as any upgrade.

For a release that needs a migration: a PostgreSQL image change replaces the database container, which is `lab/migrate-generation.py`'s job ([database containers are special](#database-containers-are-special)). For a declared data migration, follow what the release notes name. Sbarbase has no tool that applies either from the Updates page.

## Automatic updates

Off by default. Turn them on in the settings at the bottom of the Updates page: tick **Install safe updates automatically**, choose the maintenance window, and save. The window is in the server's local time, shown in 12-hour form, and defaults to 3:00 AM to 5:00 AM. A window whose end is earlier than its start crosses midnight.

An update installs by itself only when all of these hold:

- checking for new releases is on;
- the time is inside the window;
- the release is safe, signed, newer, and was found by a check made on the version running now, with nothing blocking it;
- no backup, restore, other upgrade or unsettled operation record is under way (it waits these out);
- this version was never tried automatically before and never rolled back.

Each version gets one automatic attempt. It is recorded before the request is made, so an attempt that fails for any reason is not repeated; you can still install that version from the console. A version that rolled back, by itself or on request, is never installed automatically.

The daily backup hour (`SBARBASE_BACKUP_HOUR`) is in UTC, while the window is in local time. On a server whose clock is UTC they overlap by default; an update then waits until the backup finishes.

## Turning the check off

For an offline or private host, untick **Check for new releases** and save. Sbarbase then never contacts the release source by itself, and automatic updates are off with it. **Check now** still runs one check when you press it.

The check reads the canonical repository over HTTPS, not your `origin`. To read another copy (a mirror your host can reach), set `SBARBASE_RELEASE_SOURCE` to its Git URL or path in the service environment. Signatures are still checked against the keys your checkout lists. `compose.yaml` does not pass this variable into the container: with Docker, add it to the `environment` list there.

## Roll back

**Before confirmation** the way back is automatic, as described above. There is nothing to press.

**After confirmation**, the Updates page offers **Roll back** under the last update. It moves the checkout back to the version you ran before and restarts, through the same hold and health checks. Its limits:

- It keeps the control state as it is now: everything written since the update stays. It does not restore the snapshot.
- It is offered only when the previous version can still open the control catalog. If the update migrated the catalog to a schema the previous version does not know, it is refused with the reason, and the way forward is a newer version, or the backups taken before the upgrade.
- It is offered only for the last update, and not while another request is under way.
- Going back does not undo a database change a newer Auth or Storage made when it started. Upstream migrations add to the schema, so the previous version normally runs on it; if it does not, restore the backups taken before the upgrade.

`python3 lab/upgrade.py rollback --check` says whether a rollback would go ahead, and why not, without changing anything.

## What is kept and what can be lost

- **Before confirmation the way back is complete.** Application traffic is held, so no application write lands on the new version, and the control state goes back to the snapshot the new version took when it started.
- **One exception:** management changes made in the console while the new version is being checked (at most about two minutes from the console's start) are in that window only. If the way back runs, the snapshot restore drops them. That includes projects, environments, keys and members created in that window, and provisioning jobs requested then.
- **After confirmation the fix is forward only.** The backups taken before the upgrade stay in `.lab/backups/`. A rollback keeps what was written since confirmation, or refuses when it cannot.
- **Database image changes never go through an upgrade.** They are refused, and belong to `lab/migrate-generation.py`.
- **Local edits are never discarded.** A local change to a tracked file outside `docs/evidence/` blocks the upgrade until you commit or discard it yourself.

## From the command line

With Docker, put `docker compose exec sbarbase` before each `python3` command.

| Task | Command |
|---|---|
| The newest signed release, its class and what it changes | `python3 lab/upgrade.py channel` (`--json` for the full result, `--preview` to include pre-releases) |
| Install a signed release | `python3 lab/upgrade.py start --release vX.Y.Z` |
| Install one that needs a rebuild | add `--allow-class rebuild`, then rebuild as above |
| Move to any commit or tag, without signature check or class | `python3 lab/upgrade.py start --to <tag or commit>` (default `origin/main`) |
| See what `--to` would change, without changing anything | `python3 lab/upgrade.py check --to <tag or commit>` |
| Restart onto it | `docker compose up -d --build` (or `sudo systemctl restart sbarbase`) |
| See the outcome | `python3 lab/upgrade.py status` |
| Would a rollback go ahead? | `python3 lab/upgrade.py rollback --check` |
| Go back to the version before | `python3 lab/upgrade.py rollback`, then restart the same way |

`start --to` is an operator's explicit choice: it is not signature checked and not classified. It still refuses a PostgreSQL image change, backs up first and has the same way back.

A `rollback` from the command line while a new version is still waiting for its health checks restores the control snapshot, so it needs Sbarbase stopped first (`sudo systemctl stop sbarbase`, or `docker compose stop`); it says so if it is not.

To go back to earlier pins after the automatic way back, when `rollback` says there is no upgrade to roll back, upgrade to the earlier version: `python3 lab/upgrade.py start --to <earlier commit>`, then restart. It backs up first, like any upgrade.

The `sbarbase` command runs `check`, `start`, `status` and `rollback` with `--to` ([the sbarbase command](cli.md)); it does not offer `channel` or `--release` yet.

## Moving onto the first version with the update channel

A version without the update channel (its `lab/upgrade.py` has no `channel` command) cannot see or verify a release. The move onto the first version that has one changes the `Dockerfile`, `compose.yaml` and `deploy/sbarbase.service`, so it is a rebuild and cannot be one click:

1. `python3 lab/upgrade.py start --to <tag or commit>` (this path is not signature checked).
2. With Docker, `docker compose up -d --build`. Without the rebuild the container has no `ssh-keygen`, and every release is refused with "ssh-keygen is not installed, so no release signature can be checked; rebuild the container image".
3. With systemd, `sudo /usr/bin/python3 lab/install_server.py supervise --apply` with the service's options, then `sudo systemctl restart sbarbase`.

The new version's first start already takes the control snapshot, holds traffic and runs the health checks, even though the older command started the move. If it fails, the way back lands on the older version, which confirms its own start without health checks or a hold and cannot deliver the `update.*` notifications.

## What has been run

- Unit tests on the workstation on 2026-09-25: 86 Python tests in `lab/test_release_channel.py`, `lab/test_updates.py`, `lab/test_upgrade.py` and `lab/test_upgrade_health.py` (the last two include the older upgrade tests), and 42 Bun tests for the traffic hold, the updates routes and the console page. All pass.
- The rehearsal VM ran the command line cycle on 2026-09-25: an upgrade to a newer PostgREST with `start --to`, a broken version that moved back by itself, and a return to the installed pins, with users unchanged at each step ([evidence](../evidence/vm-upgrade-checks.json), [return](../evidence/vm-upgrade-return.json)). CI runs the same `start --to` cycle on a clean machine with every change, with users, files and buckets compared before and after ([evidence](../evidence/docker-upgrade-checks.json)).
- Not run yet: the three CI cases the [plan](../engineering/plans/2026-09-25-update-channel.md) requires (a release that migrates the catalog and then fails, a release that starts but fails its health checks, an unsigned tag), and the VM rehearsal of a real bump and back through the channel. Until both pass, automatic updates are not described as ready.

## For maintainers: cutting a signed release

Releases are annotated `vX.Y.Z` tags signed with an SSH key, each carrying `release.json` (version, the oldest version it can be applied from, English and Arabic notes, declared migrations).

1. Prepare the manifest and the `package.json` version:

   ```
   /usr/bin/python3 lab/release.py prepare --version X.Y.Z --notes-en TEXT --notes-ar TEXT
   ```

   Add `--minimum-from X.Y.Z` when older installations must pass through an earlier release first, and `--migration TEXT` for each data migration (that makes the release need a migration). It never tags or pushes; it prints the exact commands that commit, sign with `git tag -s` and `gpg.format=ssh`, verify against `deploy/release-signers`, and push the tag to the canonical repository.
2. Installations trust only the keys listed in `deploy/release-signers` **of the version they run**. Add the public half of the signing key there, one line per key in the ssh-keygen allowed signers format, in a commit of its own. An installation still on a version without that key refuses every release as unsigned; it reaches the version with the key through `start --to`.

Until a key is listed, every release is refused as unsigned. That is the intended default, not a fault. A local edit to that file is a local change and blocks upgrades, so the key has to come in a commit.

## For maintainers: changing a pinned upstream version

Sbarbase runs pinned upstream Supabase images. Changing one pin is deliberate, with a written review and the full test gate. **No upstream release has been adopted through this process yet.** The binding rules are in the [upstream update policy](../engineering/UPSTREAM-UPDATE-POLICY.md).

### What is pinned

Every upstream image is pinned by tag and digest in four lock files: `lab/distro-image.lock.json` (the Supabase PostgreSQL image), `lab/images.lock.json` (Auth, PostgREST and the stock PostgreSQL used by fixtures), `lab/storage-image.lock.json` (Storage) and `lab/studio-image.lock.json` (Studio and postgres-meta). Print the whole set and check that nothing floats:

```
/usr/bin/python3 lab/pin_update.py show
/usr/bin/python3 lab/pin_update.py verify
```

### Changing one component

1. Read the upstream release notes for the candidate version.
2. Stage the change. This writes a dated review entry under `docs/upstream/`, records the previous digest as the rollback pin and changes exactly one component:

   ```
   /usr/bin/python3 lab/pin_update.py stage --file lab/images.lock.json --component rest \
       --tag public.ecr.aws/supabase/postgrest:vX.Y --digest sha256:<64 hex> --note "why"
   ```

3. Complete the review entry: what changed, which Sbarbase surfaces it touches, breaking changes and migrations, and the adopt or defer decision.
4. Pass the adoption gate: the complete Python and Bun suites, and the live integration checks against the new version. No suite, no adoption.
5. Adopt one component at a time, so a failure points to one version change.

### Rolling back a pin

Restore the previous pin recorded in the review entry and ship it as a new version; installations reach it with `lab/upgrade.py`. If the newer version migrated data, rolling back may need a data migration of its own; record it in the same entry.

## Database containers are special

Startup never recreates a database container as an implicit upgrade. A managed database container is pinned by its exact identity, and replacing it (for a new image, for example) goes through `lab/migrate-generation.py`, which is journaled and crash-tested on disposable fixtures. Its first attended run on retained data was made on 2026-09-25 and kept every row count; see [status](../reference/status.md).

## Limits

- The update channel has unit tests only. The CI cases and the VM rehearsal above have not run, and no upgrade has run on a server with real client data; the rehearsal VM held test users only.
- The health checks cover the console, the management Auth and each environment's Auth, REST and Storage. Realtime, Edge Functions and Studio are not checked. A version that passes the checks and then misbehaves is not moved back by itself: use **Roll back** or `rollback`.
- The first move onto the version with the update channel needs a rebuild and the command line (above). A way back that lands on a version older than the channel cannot deliver the `update.*` notifications.
- `lab/install_server.py supervise`, with or without `--apply`, rewrites the tracked `docs/evidence/supervisor-unit.json`. On a version with the update channel that does not block an upgrade: it is copied aside with the other evidence. An installation whose `lab/upgrade.py` has no `set_aside_evidence` (it landed on 2026-09-25) still refuses when its evidence files changed. Copy `docs/evidence/` somewhere, run `git checkout -- docs/evidence` as the service account once, then upgrade.
- Automatic updates install only safe releases, and only once per version. A release that needs a rebuild or a migration always waits for you.
