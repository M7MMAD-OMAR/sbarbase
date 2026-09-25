# Update channel: notice, one-click and opt-in automatic updates

Plan, 2026-09-25. Builds on `lab/upgrade.py` and the `after_start` hook in `lab/dev.py`
([upgrades guide](../../guides/upgrades.md)); it does not replace them.

## Goal

Anyone running Sbarbase sees in the console when a newer release exists, reads what it
changes, and applies it with one click, or lets safe releases apply themselves inside a
maintenance window. Nothing an installation already has (features, settings, data, local
edits) may be lost on the way.

## What "nothing is lost" means, stated plainly

- Before confirmation the way back is complete: the gateway stays paused until the
  post-start health checks pass, so no application write lands on a version that is not
  trusted yet, and the automatic way back restores the control state snapshot taken before
  the move.
- After confirmation the fix is forward only. The backups taken before the upgrade stay.
  This is roadmap Milestone 4, item 4.
- A release that changes the PostgreSQL image is never an upgrade (that is
  `lab/migrate-generation.py`). Local edits to tracked files block an update and are never
  discarded.

## Two defects in the current path, fixed first

1. The pre-upgrade backup copies environment databases only. `control.sqlite` refuses to
   open under an older release once a newer one migrated it (`Catalog.migrate`), so an
   automatic way back after a catalog migration ends in `rollback_failed`.
2. `upgrade_outcome(True)` runs right after `installation_runtime.py up`, before the
   supervisor serves the console and gateway. A version that starts its containers and then
   fails in the console is recorded as confirmed.

## Phases, in order

1. **Safety foundation.** A consistent snapshot of the control state (catalog, key store,
   upgrade-relevant `.lab/upstream` files) before the checkout moves, restored on the way
   back. Confirmation moves after health checks (console answers, each environment's Auth,
   REST and Storage health) with a deadline; the gateway is held paused until then.
   Preconditions checked before anything moves: no pending receipts or HBA journal, no
   restore or backup running; the installation operation lock is held.
2. **Release channel.** Releases are signed `vX.Y.Z` tags carrying `release.json`
   (version, minimum from-version, English and Arabic notes). Tags are verified against
   `deploy/release-signers` (SSH allowed signers). The check reads the canonical
   repository over HTTPS, not the local `origin`. An explicit `--to <commit>` from the
   command line keeps working for operators and CI.
3. **Classification from the diff, not from the manifest.** `safe`: no database image,
   `Dockerfile` or `deploy/sbarbase.service` change. `rebuild`: `Dockerfile` or unit file
   changed (a restart would not pick it up). `manual`: database image change or a declared
   data migration. Only `safe` may apply from the console or automatically.
4. **Check and notify.** The supervisor checks every 6 hours by default (can be turned
   off), writes `.lab/upgrades/available.json`, and emits operator notifications for
   available, applied and rolled back. `GET` on the updates endpoint serves it.
5. **Console.** A banner with the version and class, a panel with notes and what changes,
   "update now" for the installation operator only, and a progress view. The console
   writes a request; the supervisor applies it, stops cleanly and exits with a dedicated
   code that systemd (`RestartForceExitStatus=`) and Docker (`restart: unless-stopped`)
   restart.
6. **Opt-in automatic updates.** Off by default. Safe releases only, inside an operator
   maintenance window. A release that rolled back is never retried automatically.

## Acceptance

- Unit tests for classification, signature verification, snapshot and restore, the
  request file and the exit code, and health-gated confirmation.
- The CI upgrade check gains three cases: a release that migrates the catalog and then
  fails (returns with nothing lost), a release that starts but fails health (returns), and
  an unsigned tag (refused).
- The VM rehearsal of a real bump and back (`lab/vm-milestones.sh`) passes before
  automatic updates are described as ready.
- Bilingual guide updates; `server-deployment.md` stops saying automatic upgrades are out
  of scope only once they exist.

## Decisions taken

Automatic updates off by default and safe releases only; the check on by default every 6
hours with an opt-out; releases are semver tags, not every commit on `main`; tags signed
with an SSH key whose public half is in `deploy/release-signers`.
