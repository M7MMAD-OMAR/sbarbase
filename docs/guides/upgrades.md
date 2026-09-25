[العربية](upgrades.ar.md)

# Upgrades

Sbarbase tells you in the console when a newer signed release exists. A safe release installs with one click, and a release that changes environment databases installs after you confirm a warning. An update from the console, or an automatic one, first lets running work finish; every upgrade backs up your environments. Application traffic waits until the new version passes its health checks, and Sbarbase returns to the previous version by itself if it does not. You can also let safe releases install themselves inside a maintenance window. That is off by default.

Nothing here is production ready. The update channel (the release check, the console page, one-click install, automatic updates, the start guard and the drain) was built on 2026-09-25 and is covered by unit tests and by three CI cases, which passed on 2026-09-25 on a clean CI machine, not a real server ([evidence](../evidence/docker-upgrade-checks.json), 45 of 45 checks): a version that migrates the control catalog and then stops, one whose PostgREST never answers and one that never passes its health checks each moved back by themselves, with the catalog snapshot restored and application traffic held with 503 while the health checks waited, and an unsigned tag and a tag signed by an unlisted key were refused with nothing moved. Those runs moved the checkout with `lab/upgrade.py` from the command line, not the console or automatic updates. **Nothing about the channel has run in the rehearsal VM or on a real server yet**; see [what has been run](#what-has-been-run).

## Where you see an update

Only the installation operator (an owner or admin of the organization created at setup) sees anything about updates. Other members see nothing.

- **The notice.** When a check finds a newer release you can install, a notice at the top of the console names the version and its class, for example "Sbarbase 0.2.0 ready to install". When a newer signed release exists that this installation cannot install yet, the notice says so. **View updates** opens the Updates page. The notice can be dismissed; it comes back for the next version.
- **The Updates page.** It shows the installed version and when the last check ran, with **Check now**. For the release on offer it shows the tag, the commit, whether it is signed, its class, the release notes (in Arabic when your browser prefers Arabic, otherwise in English), why it has that class, which pinned images change, and why it cannot be installed now if something blocks it. Newer releases the check passed over are listed with the reason. Below that: the last update and its outcome, and the update settings.
- **Notifications.** If [operator notifications](../reference/configuration.md) are set up, you also get `update.available` once per version, `update.applied` when an update is confirmed, `update.rolled_back` when a new version failed and the installation went back by itself, and `update.rollback_failed` (critical) when the way back failed too.

Whether a release can be installed now is decided by the server, not by the page: the supervisor judges it and publishes its verdict, and the console shows that verdict and its reason as they are.

Sbarbase checks every 6 hours, starting 5 minutes after it starts. A check that fails (an offline host, for example) is retried after 30 minutes, then 1, 2 and 4 hours, then every 6 hours again, and the last good result stays on the page. Checking never installs anything.

## Install a release from the console

1. Open **Updates** and read the release notes and the list of what changes.
2. Press **Install update**. The button is available only when the server says the release can be installed now.
3. For a release marked **Needs your confirmation**, read the warning and tick the box that says environment data may need restoring from the backups if the update returns to the current version. Then press **Install now**.
4. Stay on the page. It shows each stage: waiting for the server, finishing running work, backup and move, restart, health checks. The console disconnects for a few minutes while Sbarbase restarts and reconnects by itself.
5. When it says the update is installed and healthy, press **Reload console** so the page loads the new version.

If the new version does not become healthy, the page says so and Sbarbase is back on the version you had. If the page has no final outcome after 10 minutes, run `python3 lab/upgrade.py status` on the server.

## What happens, step by step

1. **The request.** The console cannot upgrade itself: it writes a request, and the supervisor (the process that runs everything) carries it out after judging it again. There is only ever one request at a time; a request nobody picked up within an hour is dropped. An update waits for a daily backup that is running.
2. **Drain.** The provisioning worker stops claiming new jobs, and the supervisor waits until the job in hand, any Studio, sign-in, Realtime, Edge Functions, database access or signing key change, and the daily backup have finished, and no operation record is left to settle. Nothing new starts meanwhile. If that takes longer than 10 minutes, the request fails, nothing moves, and provisioning continues.
3. **Checks.** `lab/upgrade.py start --release <tag>` fetches the release tag again, verifies its signature and computes its class again from the source. It refuses, with nothing changed, when the checkout has local changes to tracked files, when the release does not contain every commit the checkout runs, when the release changes the PostgreSQL image, when an earlier upgrade has not finished starting, when an operation record still needs settling, or when a backup or restore is running. Evidence the acceptance and the live checks wrote under `docs/evidence/` is not a local change: it is copied to `.lab/upgrades/evidence-<time>/` before the checkout moves.
4. **Images.** It pulls every image the new version pins, so a missing download never stops a running installation. Everything up to here changes nothing.
5. **Backup.** It backs up every environment on this server ([backup and restore](backup-and-restore.md)). The start of this step is the point of no return for an automatic try. Backups taken for an upgrade are marked as such. Once the checkout has moved, the run is also recorded in `.lab/backups/upgrade-moved.json`: the backups of the last 3 upgrades that moved the checkout are kept whatever their age, and they do not count against `SBARBASE_BACKUP_KEEP`. A try that stopped before the move keeps ordinary backups.
6. **Control snapshot and guard.** It copies the control state (the control catalog, every SQLite store directly under `.lab/upstream/`, and the key store `.secrets/upstream/managed-keys.sqlite`) into `.lab/upgrades/snapshots/`, and copies this version's start guard to `.lab/upgrades/guard.py` (see [the start guard](#the-start-guard)).
7. **The move.** It moves the checkout to the release commit and installs its dependencies.
8. **Restart.** The supervisor stops the console and the owned containers cleanly, then exits with code 42. systemd (`RestartForceExitStatus=42` in the unit) and Docker (`restart: unless-stopped`) start it again. From the command line you restart it yourself.
9. **Guard, then the new version.** Every start runs the guard first. If the previous version left an operation record unsettled, the new version's first start moves back at once, before it touches anything, so the previous version can settle it; such a way back does not count against the release for automatic updates.
10. **Hold.** Before anything opens the control catalog, the new version takes a fresh snapshot of the control state; this is the one the way back restores. Until the health checks pass:
    - requests through the gateway get `503` with `Retry-After: 5`, and Realtime sockets and direct database access are refused;
    - management changes in the console get `409` with a sentence saying changes are paused, because the way back would drop them;
    - the console still answers reads, sign-in and sign-out, **Roll back**, **Check now** and the update settings;
    - the provisioning worker, the daily backup, Studio starts and the other background changes wait.
11. **Health checks.** Once the console process runs, one round of checks must all answer within 120 seconds: the console's own `/health` (which reads the control catalog and the key store), the management Auth, for every environment in service its Auth, REST and Storage directly, and then its REST and Auth once more through the gateway, the way an application reaches them, with a token valid for this start only. When a round passes, the update is confirmed: traffic flows, the worker starts, and `update.applied` is sent.
12. **The way back.** If a start stage fails, or no round passes within 120 seconds, the supervisor moves the checkout back and restores the snapshot from step 10, then exits with an error. The restart policy starts the previous version with its previous images. It goes through the same hold and health checks before it is recorded as `rolled_back`, and `update.rolled_back` is sent. If the previous version does not start either, the state is `rollback_failed`, `update.rollback_failed` is sent, and the way forward is to restore the backups from step 5.

On the restart only the Auth, REST, Storage, Realtime and Edge Functions containers whose pinned image changed are replaced, and the way back puts the previous images back. The database container is never replaced by an upgrade.

## The start guard

The guard is a small script that runs before any code of the version the checkout holds: as the first `ExecStartPre` of the systemd unit, first in the container's start script, and from `lab/dev.py` when you start Sbarbase from a terminal. An upgrade copies the guard of the version it leaves to `.lab/upgrades/guard.py`, and the next starts run that copy, so a broken release cannot break the way back to the version before it.

While an upgrade or a rollback waits for its health checks, and never otherwise, the guard counts each start and moves the checkout back when:

- the previous start of the new version ended before its health checks passed (the process died, or a step before the supervisor failed);
- the new version has not passed its health checks in 3 starts;
- the checkout is not the version being confirmed, or the upgrade stopped while it moved the checkout.

It also completes a way back that was interrupted. If the previous version then fails in the same way, the guard records `rollback_failed` and lets the previous version start without the health checks.

Every way back, by the guard, the supervisor or an operator's rollback, moves the checkout by force and checks the result:

- evidence under `docs/evidence/` goes to `.lab/upgrades/evidence-<time>/`, as in an upgrade; other local changes to tracked files, and untracked files the previous version would overwrite, are first copied to `.lab/upgrades/aside-<time>/`, one folder per way back. Nothing prunes those folders: look there for an edit you made on the server, and remove a folder yourself once you no longer need it;
- a `.git/index.lock` left behind by a killed Git process is removed, but only when no Git process could be using the checkout;
- nothing is recorded until the checkout is the previous version and its tracked files are clean.

If the move back inside `start` itself fails, the upgrade stays `applied`, and the next restart moves the checkout back before any code of either version runs.

A move that keeps failing is tried again by the next starts. After 3 failed moves, one of three things happens:

- the checkout holds the previous version: the phase becomes `failed` (the move back of `start`) or `rollback_failed` (any other way back), and the previous version starts without the health checks;
- the checkout still holds the confirmed version an operator's rollback tried to leave: it stays on that version, the phase is `confirmed` again, and `upgrade.py status` shows a `rollback` line saying why the rollback failed;
- anything else: Sbarbase stays stopped rather than run an unchecked tree. The guard prints one line, waits 5 minutes and exits, so each restart tries the move again, and `status` shows a `stuck` line with the reason. The installation comes back by itself once the cause (a full disk, wrong file ownership) is gone.

After an update child fails in a way that may have left the checkout between two versions, the supervisor does not resume provisioning on it: it fails the request, says Sbarbase restarts, and exits so the guard settles the checkout first. A way back the guard takes is announced by the next start that can send notifications. The systemd unit sets `StartLimitIntervalSec=0`, so systemd never stops restarting while the guard needs several starts, and `TimeoutStartSec=600`, because a way back reinstalls the previous version's dependencies before the preflight runs.

A supervisor that is killed rather than stopped (SIGKILL, the OOM killer), during the health checks or at any other time, leaves the owned containers running, since Docker owns them and not the service. After the guard, the next start stops them the way the supervisor's own stop does, keeping every container and volume (`lab/leftover_runtime.py`, the unit's second `ExecStartPre`, and again in the supervisor after it takes its locks). It refuses instead, and names why, when a live supervisor or worker still holds its lock or a provisioning receipt or HBA journal waits for reconciliation. A unit installed before that line existed still refuses at the preflight until the unit is reinstalled ([server deployment](server-deployment.md)).

## The four classes

The class is computed from the difference between the commit you run and the release commit, not taken from what the release says about itself. A release can only make itself stricter, by declaring a data migration.

| Class | What decides it | What to do |
|---|---|---|
| Safe | Nothing below changes. Code, dependencies and the PostgREST, Edge Functions and Studio pins may change: none of them changes an environment database | Install from the console, from the command line, or let automatic updates do it |
| Needs your confirmation (`attended`) | The Auth, Storage or Realtime pin changes | Install from the console after you acknowledge the warning, or with `--allow-class attended`. Never automatically |
| Needs a rebuild | `compose.yaml`, `deploy/sbarbase.service`, `.dockerignore`, the TLS proxy (`deploy/console-tls-proxy.ts`), the `Dockerfile`, or any file the release's own `Dockerfile` copies into the image (its `COPY` and `ADD` lines) changes. A restart does not read these again | Install on the server, then rebuild (below). Never from the console or automatically |
| Needs a migration | The PostgreSQL image changes, or the release declares a data migration | Not an upgrade. Never from the console, automatically or with `start --release` |

### Releases that need your confirmation

Auth, Storage and Realtime each run their own schema migrations in every environment database when they start, and the previous image may not run on the migrated schema. The way back restores the control state only, not environment databases. So if such an update returns to the previous version, **environment data may need restoring from the backups taken before the upgrade**. Storage also migrates its shared `storage_metadata` database, which holds every environment's Storage settings; the per-environment backups do not include it. That is why this class installs only after an explicit acknowledgement, and never automatically.

### Releases that need a rebuild

With Docker:

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

A release that needs a rebuild and also changes the Auth, Storage or Realtime pin needs both options: `--allow-class rebuild --allow-class attended`. When the TLS proxy changes, restart its own unit after the update as well. The restart then goes through the same guard, snapshot, hold, health checks and way back as any upgrade.

### Releases that need a migration

A PostgreSQL image change replaces the database container, which is `lab/migrate-generation.py`'s job ([database containers are special](#database-containers-are-special)). For a declared data migration, follow what the release notes name. Sbarbase has no tool that applies either from the Updates page.

## Automatic updates

Off by default. Turn them on in the settings at the bottom of the Updates page: tick **Install safe updates automatically**, choose the maintenance window, and save. The window is in the server's local time, shown in 12-hour form with the zone the server uses, and defaults to 3:00 AM to 5:00 AM. A window whose end is earlier than its start crosses midnight. Under Docker the server's local time is the container's: UTC unless you set `TZ` (for example `TZ=Asia/Dubai`), which `compose.yaml` passes in; the image carries the time zone database.

An update installs by itself only when all of these hold:

- checking for new releases is on;
- the time is inside the window;
- the release is safe and the server's verdict says it can be installed now;
- no backup, restore, other upgrade or unsettled operation record is under way (it waits these out);
- this version has not used up its automatic try and never rolled back.

An automatic try is used up once it reaches its point of no return, the start of the backup. A try that stopped earlier (a network failure while fetching the release or pulling its images, a refusal, a drain that ran out of time) is tried again, at most 3 times per version, 10 and then 20 minutes apart, inside the window. A try whose backup, snapshot or move then fails is not repeated. A version that rolled back, by itself or on request, is never installed automatically, except after a way back caused only by an operation record the previous version had not settled. You can still install any of these from the console.

The daily backup hour (`SBARBASE_BACKUP_HOUR`) is in UTC, while the window is in local time. On a server whose clock is UTC they overlap by default; an update then waits until the backup finishes.

## Turning the check off

For an offline or private host, untick **Check for new releases** and save. Sbarbase then never contacts the release source by itself, and automatic updates are off with it. **Check now** still runs one check when you press it.

The check reads the canonical repository over HTTPS, not your `origin`. To read another copy (a mirror your host can reach), set `SBARBASE_RELEASE_SOURCE` to its Git URL or path in the service environment; `compose.yaml` passes it into the container. Signatures are still checked against the keys your checkout lists.

## Roll back

**Before confirmation** the way back is automatic, as described above. There is nothing to press.

**After confirmation**, the Updates page offers **Roll back** under the last update. It moves the checkout back to the version you ran before and restarts, through the same drain, guard, hold and health checks. Its limits:

- It keeps the control state as it is now: everything written since the update stays. It does not restore the snapshot.
- It is offered only when the previous version can still open the control catalog and the key store. If the update migrated them to a schema the previous version does not know, it is refused with the reason, and the way forward is a newer version, or the backups taken before the upgrade.
- It is offered only for the last update, and not while another request is under way.
- It is refused, from the page or the command line, while tracked files outside `docs/evidence/` have local changes, because the way back would move them aside; the refusal names them. Commit or discard them on the server first. The automatic way back never refuses for this: it sets the changes aside.
- Going back does not undo a change a newer Auth, Storage or Realtime made to the environment databases when it started (see [releases that need your confirmation](#releases-that-need-your-confirmation)). If the previous version does not run on it, restore the backups taken before the upgrade.

`python3 lab/upgrade.py rollback --check` says whether a rollback would go ahead, and why not, without changing anything.

## What is kept and what can be lost

- **Before confirmation the control state goes back complete.** Application traffic is held, so no application write lands on the new version; management changes are refused with `409` rather than accepted and then dropped; and the control state goes back to the snapshot the new version took when it started.
- **Environment databases are not part of that snapshot.** For a safe release nothing in them changes as the new version starts. For a release that needs your confirmation, the Auth, Storage or Realtime migrations stay after a way back, and `storage_metadata` is in no per-environment backup.
- **After confirmation the fix is forward only.** The backups taken before the upgrade stay in `.lab/backups/`, and those of the last 3 upgrades that moved the checkout are kept out of pruning. A rollback keeps what was written since confirmation, or refuses when it cannot.
- **Database image changes never go through an upgrade.** They are refused, and belong to `lab/migrate-generation.py`.
- **Local edits are never discarded.** A local change to a tracked file outside `docs/evidence/` blocks an upgrade and an operator's rollback until you commit or discard it yourself. A way back that runs anyway (the automatic one, or the guard's) copies local changes to `.lab/upgrades/aside-<time>/`, and evidence to `.lab/upgrades/evidence-<time>/`, before it overwrites the checkout.

## From the command line

With Docker, put `docker compose exec sbarbase` before each `python3` command.

| Task | Command |
|---|---|
| The newest signed release, its class and what it changes | `python3 lab/upgrade.py channel` (`--json` for the full result, `--preview` to include pre-releases) |
| Install a signed release | `python3 lab/upgrade.py start --release vX.Y.Z` |
| Install one that needs your confirmation | add `--allow-class attended` |
| Install one that needs a rebuild | add `--allow-class rebuild`, then rebuild as above |
| Move to any commit or tag, without signature check or class | `python3 lab/upgrade.py start --to <tag or commit>` (default `origin/main`) |
| See what `--to` would change, without changing anything | `python3 lab/upgrade.py check --to <tag or commit>` |
| Restart onto it | `docker compose up -d --build` (or `sudo systemctl restart sbarbase`) |
| See the outcome | `python3 lab/upgrade.py status` |
| Would a rollback go ahead? | `python3 lab/upgrade.py rollback --check` |
| Go back to the version before | `python3 lab/upgrade.py rollback`, then restart the same way |

`start --to` is an operator's explicit choice: it is not signature checked and not classified. It still refuses a PostgreSQL image change, backs up first and has the same way back.

A start from the command line does not drain the running supervisor, which keeps running the previous version from the moved checkout until the restart. When Sbarbase is running, `start` says so and asks you to restart at once, before the old supervisor starts anything else. `status` shows the phase and, after an automatic way back, a `why back` line with the reason.

A `rollback` from the command line while a new version is still waiting for its health checks restores the control snapshot, so it needs Sbarbase stopped first (`sudo systemctl stop sbarbase`, or `docker compose stop`); it says so if it is not.

To go back to earlier pins after the automatic way back, when `rollback` says there is no upgrade to roll back, upgrade to the earlier version: `python3 lab/upgrade.py start --to <earlier commit>`, then restart. It backs up first, like any upgrade.

The `sbarbase` command runs `check`, `start`, `status` and `rollback` with `--to` ([the sbarbase command](cli.md)); it does not offer `channel`, `--release` or `--allow-class` yet.

## Moving onto the first version with the update channel

A version without the update channel (its `lab/upgrade.py` has no `channel` command) cannot see or verify a release. The move onto the first version that has one changes the `Dockerfile`, `compose.yaml`, the container's start script and `deploy/sbarbase.service`, so it is a rebuild and cannot be one click:

1. `python3 lab/upgrade.py start --to <tag or commit>` (this path is not signature checked).
2. With Docker, `docker compose up -d --build`. Without the rebuild the container has no `ssh-keygen` and no guard in its start script, and every release is refused with "ssh-keygen is not installed, so no release signature can be checked; rebuild the container image".
3. With systemd, `sudo /usr/bin/python3 lab/install_server.py supervise --apply` with the service's options, then `sudo systemctl restart sbarbase`. Until the new unit is installed, the guard runs from `lab/dev.py` instead of before the preflight.

The new version's first start takes the control snapshot, holds traffic and runs the health checks, even though the older command started the move. If it fails, the way back lands on the older version, which has no guard, confirms its own start without health checks or a hold, and cannot deliver the `update.*` notifications.

## What has been run

- Unit tests on the workstation on 2026-09-25: 201 Python tests in `lab/test_release_channel.py`, `lab/test_updates.py`, `lab/test_upgrade.py`, `lab/test_upgrade_health.py`, `lab/test_upgrade_guard.py`, `lab/test_upgrade_drain.py` and `lab/test_upgrade_check.py` (the upgrade file also holds the older upgrade tests), and 58 Bun tests for the hold, the probe past it, the updates routes and the console page. All pass.
- The rehearsal VM ran the command line cycle on 2026-09-25: an upgrade to a newer PostgREST with `start --to`, a broken version that moved back by itself, and a return to the installed pins, with users unchanged at each step ([evidence](../evidence/vm-upgrade-checks.json), [return](../evidence/vm-upgrade-return.json)). CI records the same `start --to` cycle on a clean machine, with users, files and buckets compared before and after ([evidence](../evidence/docker-upgrade-checks.json)).
- The three CI cases for the channel passed on 2026-09-25 (CI run 36195831433, 45 of 45 checks, [evidence](../evidence/docker-upgrade-checks.json)), on a clean CI machine with test data: a confirmed upgrade; a version that migrates the control catalog and then stops, moved back with the catalog schema back from 4 to 3; one whose PostgREST never answers; one that never passes its health checks, with application traffic answered 503 during the window; and tags that are unsigned or signed by an unlisted key, refused with nothing moved.
- Not run yet: the VM rehearsal of a real bump and back through the channel, the start guard, the drain, and an `attended` release. Until those pass, automatic updates are not described as ready.

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

- Beyond unit tests, the update channel has run only in its CI cases, on a clean CI machine ([evidence](../evidence/docker-upgrade-checks.json)). Nothing about it has run in the rehearsal VM, and no upgrade has run on a server with real client data; the rehearsal VM held test users only.
- **After the supervisor is killed while a new version waits for its health checks, under systemd, the service can stay down.** The owned containers keep running when the process dies. The guard moves the checkout back on the next start, but the preflight (`lab/install_server.py check`) then refuses because owned containers are already running, and systemd keeps retrying without getting past it. This follows from the code and has not been rehearsed. `journalctl -u sbarbase` shows the preflight refusal. No recovery procedure for this case has been rehearsed yet.
- When Sbarbase stops after an update moved the checkout, the final stop of the owned runtime runs the stop script of the moved checkout, not the one of the version that started it.
- The health checks cover the console, the management Auth, each environment's Auth, REST and Storage, and REST and Auth through the gateway. Realtime, Edge Functions and Studio are not checked. A version that passes the checks and then misbehaves is not moved back by itself: use **Roll back** or `rollback`.
- A release that needs your confirmation may leave environment databases migrated after a way back, and Storage's shared `storage_metadata` database is in no per-environment backup.
- The first move onto the version with the update channel needs a rebuild and the command line (above). A way back that lands on a version older than the channel has no guard, no health checks and no hold, and cannot deliver the `update.*` notifications.
- `lab/install_server.py supervise`, with or without `--apply`, rewrites the tracked `docs/evidence/supervisor-unit.json`. On a version with the update channel that does not block an upgrade: it is copied aside with the other evidence. An installation whose `lab/upgrade.py` has no `set_aside_evidence` (it landed on 2026-09-25) still refuses when its evidence files changed. Copy `docs/evidence/` somewhere, run `git checkout -- docs/evidence` as the service account once, then upgrade.
- Automatic updates install only safe releases. A release that needs your confirmation, a rebuild or a migration always waits for you.
- The forced way back, the 3-move limit and the stopped state live in the guard, and the way back runs the guard copied from the version the upgrade left. So they protect only upgrades started from a version that has them.
- A guard that cannot write `state.json` at all (a full or read-only disk) cannot count its failures, so the service restarts in a loop until the disk is writable again.
