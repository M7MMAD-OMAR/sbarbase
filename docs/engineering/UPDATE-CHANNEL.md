# Update channel

Recorded 2026-09-25. Built from the [update channel plan](plans/2026-09-25-update-channel.md). Status: built and covered by unit tests only. **The three CI cases the plan requires and the VM rehearsal of a real bump and back through the channel have not run.** Nothing here is production ready. The operator's view is the [upgrades guide](../guides/upgrades.md).

## Pieces

| File | Role |
|---|---|
| `lab/release_channel.py` | Lists, fetches and verifies signed release tags; reads `release.json`; classifies a move from the diff; writes `.lab/upgrades/available.json` |
| `lab/release.py` | The maintainer's side: `prepare` writes `release.json` and the `package.json` version and prints the commands that commit, sign, verify and push the tag |
| `lab/upgrade.py` | `channel`, `start --release`, `start --to`, `check`, `status`, `rollback [--check]`; the upgrade state, the control snapshot, the hold marker, `before_start` and `after_start` |
| `lab/upgrade_health.py` | The health round and the `Confirmation` poller with its 120 second deadline |
| `lab/updates.py` | The supervisor's side: settings, the request file, the ledger, check scheduling, the automatic decision, the rollback verdict, notifications |
| `lab/dev.py` | Wiring: `upgrade_prepare`, the gated start, `Supervisor.schedule_updates`, `RESTART_FOR_UPGRADE = 42` |
| `src/control/updates.ts`, `src/control/http.ts` | The console's side: `GET /management/v1/updates`, `PUT .../settings`, `POST .../check`, `.../apply`, `.../rollback`, for the installation operator only |
| `src/gateway/hold.ts`, `src/http/health.ts`, `lab/upstream-server.ts` | The traffic hold and the loopback `/health` |
| `ui/Updates.tsx`, `ui/releases.ts` | The notice, the Updates page, the progress view and the settings form. Release notes follow `document.documentElement.lang`, which the console sets to `en` only, so the Arabic notes are not shown yet |
| `deploy/sbarbase.service`, `compose.yaml`, `Dockerfile` | `RestartForceExitStatus=42`; `restart: unless-stopped`; `openssh-client` for `ssh-keygen` |
| `deploy/release-signers`, `release.json` | Allowed signers (no key yet); the manifest of the running version |

## Design

The console process cannot upgrade: `upgrade.py start` moves the checkout under the running console, and only the supervisor can stop everything and exit so the service manager starts the new code. So the console writes a request and the supervisor carries it out, checking everything again itself. The console never runs Git and never trusts the request file: the supervisor re-derives the refusal from `available.json`, takes the tag from there rather than from the request, and `upgrade.py start --release` fetches and verifies the tag again from the source and moves to the verified commit, never to a ref.

After an apply or rollback child exits 0, the supervisor finishes the request, stops its children and the owned runtime, releases its locks and exits with 42. systemd restarts it through `Restart=on-failure` and, explicitly, `RestartForceExitStatus=42`; Docker through `restart: unless-stopped`. Neither restarts a clean exit 0, which is why the code is not 0. A failed start exits 1, which both also restart.

Only one update child runs at a time (a check, an apply or a rollback), spawned through `lab/parent_bound.py` with its output in `.lab/upgrades/<kind>.log`, copied to the journal once it ends. A failure of the scheduling itself pauses it for 60 seconds and never stops the installation.

## The upgrade state

`.lab/upgrades/state.json` holds the last upgrade: `from`, `to`, `phase`, `started_at`, `snapshot`, `trigger` (`cli`, `console`, `automatic`), `automatic` (the way back happened by itself), and for a channel release `release: {version, tag, class, signed}`.

| From | Event | To |
|---|---|---|
| none | `start` passed its refusals, pulled, backed up and took the first snapshot | `applied` |
| `applied` | the checkout move or `bun install` failed; the checkout is put back | `failed` |
| `applied` | first start of the new version (`before_start`): a fresh snapshot replaces the first one, `attempted_at` is set | `applied` |
| `applied` | a health round passed | `confirmed` |
| `applied` | a start stage failed, or no round passed by the deadline: snapshot restored (only if `attempted_at`), checkout moved back | `rolling_back` (`automatic: true`) |
| `applied` | the automatic way back itself raised | `rollback_failed` |
| `applied` or `confirmed` | operator `rollback` | `rolling_back` |
| `rolling_back` | the previous version passed its health round | `rolled_back` |
| `rolling_back` | the previous version failed to start | `rollback_failed` |

`applied` and `rolling_back` are pending: while the state is pending, `plan()` refuses a new upgrade and the console refuses an apply. `upgrade.lock` serializes `start` and `rollback`; it is not the installation operation lock, which the running worker holds for its lifetime. The automatic way back waits up to 30 seconds for it.

`.lab/upstream/upgrade-intent.json` records which pins the next start may replace (Auth, REST, Storage, Realtime, Functions); `lab/durable_runtime.py` replaces nothing else, and the intent is removed on confirmation.

## The request

`.lab/upgrades/request.json` is `{id, kind: apply|rollback|check, version?, tag?, trigger: console|automatic, state, requested_at, started_at?, finished_at?, detail?}`. It is created by linking a private temporary file into place (`linkSync` in TypeScript, `os.link` in Python), which fails when the file exists, so the console and the automatic mode can never both hold the slot.

| From | Event | To |
|---|---|---|
| none | console `POST` or the automatic decision | `requested` |
| `requested` | the supervisor re-checks and refuses | `failed` with the refusal sentence |
| `requested` | the supervisor spawns the child | `running` (`started_at`) |
| `running` | the child ends | `done` or `failed` (its meaningful last line) |
| `requested` | not picked up within 1 hour | `failed` (`expired`) |
| `running` | the supervisor started again and the upgrade state shows it went through | `done` |
| `running` | otherwise, at the next start or with no child to follow | `failed` (`interrupted`) |

A final request is copied to `last-request.json` and the slot freed. An apply request waits while the daily backup runs. The refusal sentences are shared word for word between `lab/updates.py` `MESSAGES` and `src/control/updates.ts` `UPDATE_MESSAGES`; `lab/test_updates.py` keeps them in step.

## Confirmation gate and hold

`before_start` runs after the supervisor takes its locks and before the settle stage, which may open and migrate the control catalog. For a pending state it writes `.lab/upgrades/hold` and returns true; a snapshot that cannot be taken raises, the start counts as failed and the way back runs before the new version touched anything. Any other bookkeeping failure leaves the start ungated. A marker without a pending state is removed.

While gated, `Supervisor.run` starts only the console. The worker, the daily backup, Studio starts, sign-in applies, turning Realtime, Edge Functions or direct database access on or off, signing key rotation and the update scheduling all wait, so nothing leaves an effect the restore would not know about. Each turn polls `upgrade_health.Confirmation`:

- The deadline (120 s) starts at the first poll, once the console process exists, so setup before it does not use it up. A round runs every 2 s in a daemon thread; each probe times out after 5 s and goes straight to the upstream service, never through the gateway, with any proxy variable ignored.
- A round is: the console's `/health` (which reads the catalog schema; `server.json` must name the server this supervisor started), the management Auth `/health`, and for each routed runtime (provisioned, not deleted, not in maintenance, not moved) Auth `/health`, REST `/`, and Storage `/bucket` with a `service_role` token and the tenant host. All must answer 200.
- A passing round calls `upgrade_outcome(True)`: phase to `confirmed` or `rolled_back`, intent and marker removed, the outcome notification emitted; then the worker starts and `current.json` is published again. The deadline raises `RuntimeError`, `main` calls `upgrade_outcome(False)`, and the process exits 1.

The hold (`src/gateway/hold.ts`) is the marker **and** a pending phase in `state.json`, re-read at most once a second. Anything else fails open, including an unreadable state, so a stale marker can never wedge an installation. Held: everything except `/management/` gets `503` with `Retry-After: 5`; Realtime socket upgrades get 503; the direct database listener gets no target. Not held: the console's static files, `/management/`, Studio hosts and `/health`, which also reports `held`.

## Snapshot scope and restore rules

- **Scope:** every `*.sqlite` directly under `.lab/upstream/` (the control catalog) and the key store `.secrets/upstream/managed-keys.sqlite`. Not the JSON descriptors and journals under `.lab/upstream/`, not other `.secrets/` files, not the environment databases (the pre-upgrade backup covers those, and held traffic keeps applications from writing).
- **Taking:** SQLite's backup API from a read-only connection, so it is consistent while a process has the stores open; each copy fsynced; a manifest with size, SHA-256 and `user_version` per file; written under `<commit>-<UTC time>.partial` and renamed when complete. Directories are 0700. The newest three complete snapshots are kept, plus the one `state.json` names.
- **When:** once by `start` (it proves the copy works; nothing changes if it fails), again by `before_start` on the first start of the new version. The second is the one restored: it holds everything the old version wrote up to the restart. Later starts of the same pending version keep it, because the catalog may already be migrated.
- **Restoring:** only on the way back from `applied` with `attempted_at` set (a version that never started touched nothing, and restoring would drop what the running version wrote since `start`). Every file is verified against the manifest before any is written. Each store is then replaced atomically: a private temporary file, fsync, owner and mode of the store it replaces, leftover `-journal`, `-wal` and `-shm` removed, rename, directory fsync. The replacement is atomic per file, not across files. An operator `rollback` that restores requires the supervisor lock to be free, so it refuses while Sbarbase runs.
- A store the new version created that is not in the manifest is left in place.
- After confirmation nothing is restored. `rollback` keeps the control state and refuses when the catalog's `user_version` is newer than the `CATALOG_SCHEMA_VERSION` the previous commit's `src/control/catalog.ts` declares (no refusal when that commit declares none).

## Trust model

- **Source:** `https://github.com/M7MMAD-OMAR/sbarbase.git`, or `SBARBASE_RELEASE_SOURCE`. Never the local `origin`. Network Git runs with `GIT_TERMINAL_PROMPT=0` and a 120 second timeout.
- **Listing is a hint:** `git ls-remote --tags` names candidates (`vMAJOR.MINOR.PATCH`, pre-releases only with `--preview`, which the console and the supervisor never pass). One tag at a time is fetched with `--no-tags --no-write-fetch-head` into `refs/sbarbase-releases/tags/`, so the operator's branches, tags and remotes stay as they are.
- **Verification fails closed:** no key in the signers file, no `ssh-keygen`, a lightweight tag, a tag object whose own `tag` header differs from the fetched name (a replayed signed tag), a tag not pointing at a commit, or `git verify-tag` with `gpg.format=ssh`, `gpg.ssh.allowedSignersFile` and `gpg.minTrustLevel=fully` that does not print a good signature or prints "No principal matched" (the exit code alone is not trusted across Git versions). An unsigned release is still reported, with `signed: false` and the reason in the refusals, so the console can say why.
- **Signers come from the running checkout:** `deploy/release-signers` at `ROOT`. A key a version does not list is not trusted by it, and a local edit to that file blocks upgrades as a local change.
- **The commit comes from the verified tag**, and `release.json` is read and validated at that commit; its `version` must equal the tag. Unknown manifest fields are ignored, so older installations keep reading later manifests.
- **`minimum_from`:** a release this version cannot reach directly is skipped with a reason and the next older one is offered.
- **Classification comes from the diff** (`git diff --name-only current target`), never from the manifest: `manual` when an entry of `lab/distro-image.lock.json` changes its id or the manifest declares migrations; `rebuild` when `Dockerfile`, `.dockerignore`, `deploy/container/start.sh`, `compose.yaml` or `deploy/sbarbase.service` changes; else `safe`. The manifest can only make a release stricter.
- **What each path allows:** the console and automatic mode only `safe`, signed, with no refusals, from a check made on the running commit. `start --release` also `rebuild` with `--allow-class rebuild`, never `manual`. `start --to` is the operator's explicit choice: not verified, not classified; `plan()` still refuses a PostgreSQL image change.

## Settings, checks and the automatic decision

Settings (`check`, `automatic`, `window`) are validated identically in Python and TypeScript; a missing or invalid file means the defaults (check on, automatic off, 3:00 AM to 5:00 AM local time), so a damaged file can never turn automatic updates on. `automatic` requires `check`.

`check_due`: never within 5 minutes of the supervisor start; then every 6 hours; after a failure 30 minutes, 1, 2, 4 hours, capped at 6; and as soon as the last result was made on another commit, unless backing off. A check that exits non-zero or finds the source unreachable is a failure, and the previous `available.json` is put back so an offline host keeps the release it knew of.

`automatic_release` returns a release only when check and automatic are on, no backup runs, the local time is in `[start, end)` (crossing midnight when end is earlier), the release is not in the ledger's `attempted` or `rolled_back`, the upgrade state does not show that version rolling back, and `apply_refusal` finds nothing. `blocked()` then waits out a backup or restore lock, the upgrade lock or an unsettled record rather than spending the attempt. The version is added to `attempted` before the request is created: one attempt per version, whatever happens next.

## canRollback

The console offers "Roll back" when all hold:

1. `state.json` phase is `confirmed`;
2. no request is `requested` or `running`;
3. `current.json` has a `rollback` verdict whose `started_at` equals the state's `started_at` (a verdict about an older upgrade never counts);
4. that verdict says `possible: true`.

The supervisor writes the verdict (`updates.publish_current`) when it starts and again after confirmation, from `updates.rollback_verdict`, which calls the same `upgrade.rollback_refusal` that `upgrade.py rollback` and `rollback --check` use, so the page and the command cannot disagree. The route checks the verdict again, and the supervisor checks it once more before running `upgrade.py rollback`.

## Notifications

Four kinds, registered in `lab/notify.py`, emitted through `notification_producers.emit` (which never raises):

| Kind | Severity | Emitted |
|---|---|---|
| `update.available` | info | after a successful check, once per version: the ledger's `announced` list, written only when the event was written |
| `update.applied` | info | `applied` to `confirmed`, with the trigger |
| `update.rolled_back` | warning | `applied` to `rolling_back` with `automatic: true`, by the failing version after it restored the snapshot, so the row lands in the catalog the previous version opens |
| `update.rollback_failed` | critical | `applied` or `rolling_back` to `rollback_failed` |

An operator's rollback earns no notification; every way back, requested or automatic, is added to the ledger's `rolled_back` list. `version` is the release version, or a 12 character commit for a `start --to` upgrade. The ledger keeps the last 50 per list.

## Tests

Workstation, 2026-09-25: `lab/test_release_channel.py`, `lab/test_updates.py`, `lab/test_upgrade.py` (which also holds the older upgrade tests) and `lab/test_upgrade_health.py`, 86 tests, OK; `tests/hold.test.ts`, `tests/updates-routes.test.ts` and `tests/updates-ui.test.ts`, 42 pass. The existing CI job and `lab/vm-milestones.sh upgrade` exercise `start --to` only.

Pending acceptance, from the plan: the CI upgrade check gains a release that migrates the catalog and then fails (returns with nothing lost), a release that starts but fails health (returns), and an unsigned tag (refused); and the VM rehearsal of a real bump and back passes before automatic updates are described as ready.

## Known limits

- Unit tests only, as above. No release signing key is listed in `deploy/release-signers`, so every release is refused as unsigned until a maintainer commits one; installations without that commit reach it through `start --to`.
- The first move onto the version that introduces the channel changes `Dockerfile`, `compose.yaml` and `deploy/sbarbase.service`: class `rebuild`, and the version before it has no channel, so it takes `start --to` and a rebuild or unit reinstall. A Docker image that was not rebuilt has no `ssh-keygen` and refuses every release with that reason. The new version's first start is still snapshotted and gated, because `before_start` sees an `applied` state without `attempted_at`.
- A way back that lands on a version older than the channel confirms its start without a health round or hold, and cannot deliver the `update.*` notifications.
- `lab/install_server.py supervise`, with or without `--apply`, rewrites the tracked `docs/evidence/supervisor-unit.json`. On this version that does not block an upgrade: `plan()` ignores changes under `docs/evidence/` and `set_aside_evidence` copies them to `.lab/upgrades/evidence-<time>/` before the move. An installation whose `lab/upgrade.py` has no `set_aside_evidence` (it landed on 2026-09-25) still refuses; the workaround is to copy `docs/evidence/` aside and run `git checkout -- docs/evidence` as the service account once.
- The health round does not cover Realtime, Edge Functions or Studio, and one passing round confirms: a version that passes and then misbehaves is not moved back by itself.
- Console changes made during the confirmation window (at most about two minutes from the console's start) are dropped by the snapshot restore if the way back runs, including provisioning jobs requested then. Database schema changes a newer Auth or Storage made at start are not undone.
- The automatic attempt is spent even when `upgrade.py` then refuses (a local change, for example); that version is never retried automatically.
- `SBARBASE_RELEASE_SOURCE` is not passed into the container by `compose.yaml`. The `sbarbase` command has no `channel` or `--release`.
