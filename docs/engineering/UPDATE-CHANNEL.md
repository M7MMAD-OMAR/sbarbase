# Update channel

Recorded 2026-09-25. Built from the [update channel plan](plans/2026-09-25-update-channel.md). Status: built and covered by unit tests; the three CI cases the plan requires passed on a clean CI machine on 2026-09-25 (CI run 36195831433, 45 of 45 checks, [evidence](../evidence/docker-upgrade-checks.json)). **The VM rehearsal of a real bump and back through the channel has not run.** Nothing here is production ready. The operator's view is the [upgrades guide](../guides/upgrades.md).

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
| `ui/Updates.tsx`, `ui/releases.ts` | The notice, the Updates page, the progress view and the settings form. The console is English only, so release notes follow the browser's preferred language instead (`navigator.languages`: Arabic when it comes before English, else English) |
| `deploy/sbarbase.service`, `compose.yaml`, `Dockerfile` | `RestartForceExitStatus=42`; `restart: unless-stopped`; `openssh-client` for `ssh-keygen`, `tzdata` so `TZ` may name a zone |
| `deploy/release-signers`, `release.json` | Allowed signers (no key yet); the manifest of the running version |

## Design

The console process cannot upgrade: `upgrade.py start` moves the checkout under the running console, and only the supervisor can stop everything and exit so the service manager starts the new code. So the console writes a request and the supervisor carries it out, checking everything again itself. The console never runs Git and never trusts the request file: the supervisor re-derives the refusal from `available.json`, names the release `v` + the version it verified there (a `tag` in the request is not read), and `upgrade.py start --release` fetches and verifies the tag again from the source and moves to the verified commit, never to a ref.

The supervisor is also the one authority on whether a release can be installed now. It publishes that verdict in `current.json`, and the console answers an apply and draws its Install button from it rather than judging the class, the signature, the refusals or the phase again (see [The console's side](#the-consoles-side) and [What runs now](#what-runs-now-currentjson)).

After an apply or rollback child exits 0, the supervisor finishes the request, stops its children and the owned runtime, releases its locks and exits with 42. systemd restarts it through `Restart=on-failure` and, explicitly, `RestartForceExitStatus=42`; Docker through `restart: unless-stopped`. Neither restarts a clean exit 0, which is why the code is not 0. A failed start exits 1, which both also restart.

Only one update child runs at a time (a check, an apply or a rollback), spawned through `lab/parent_bound.py` with its output in `.lab/upgrades/<kind>.log`, copied to the journal once it ends. Each gets a run id (`--request`, the request's id or a fresh one for a periodic check) and records how it ended in `.lab/upgrades/outcome.json`: `{request, kind, passed, changed, refusals, error, at}`. The supervisor reads that for the request's detail (the refusals, then "Nothing was changed.", else the error's first line); an outcome that is missing or names another run gives "It stopped without saying why." The command line writes no outcome. A failure of the scheduling itself pauses it for 60 seconds and never stops the installation.

Every turn of the supervisor follows the child, the drain and `request.json`; the rest (the verdict in `current.json`, whether a check is due, the automatic decision) runs at most every 15 seconds.

## The upgrade state

`.lab/upgrades/state.json` holds the last upgrade: `from`, `to`, `phase`, `started_at`, `snapshot`, `trigger` (`cli`, `console`, `automatic`), `automatic` (the way back happened by itself), for a channel release `release: {version, tag, class, signed}` (in the first record `start` saves), `retryable` when the new version's first start found a record the previous version left unsettled (the way back that follows does not rule the version out for automatic mode), and `notices` for outcomes the guard recorded (see [Notifications](#notifications)). The move itself adds `backup` (the time of the backup run `start` took), `back_from` (the phase a way back left), `move_failures`, `stuck` and `rollback_failure` (see [Moving the checkout](#moving-the-checkout)).

| From | Event | To |
|---|---|---|
| none | `start` passed its refusals, pulled, recorded its point of no return (`outcome.json`), backed up (`--reason upgrade`; pruning keeps the last three such runs whose try moved the checkout) and took the first snapshot | `applied` |
| `applied` | the checkout move or `bun install` failed; the checkout is moved back (forced and verified, see [Moving the checkout](#moving-the-checkout)) with the previous version's dependencies | `failed` |
| `applied` | that move back failed too: nothing more is saved, so `moved` stays false | `applied`, until the next start's guard moves back and records `failed` |
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

`.lab/upgrades/request.json` is `{id, kind: apply|rollback|check, version?, trigger: console|automatic, acknowledged?, state, requested_at, started_at?, finished_at?, detail?}`. An apply from the console with `acknowledged: true` of an `attended` release runs with `--allow-class attended`; an automatic one never does. It is created by linking a private temporary file into place (`linkSync` in TypeScript, `os.link` in Python), which fails when the file exists, so the console and the automatic mode can never both hold the slot.

| From | Event | To |
|---|---|---|
| none | console `POST` or the automatic decision | `requested` |
| `requested` | the supervisor re-checks and refuses | `failed` with the refusal sentence |
| `requested` | the supervisor spawns the child | `running` (`started_at`) |
| `running` | the child ends | `done` or `failed` (the sentence from its outcome) |
| `requested` | not picked up within 1 hour | `failed` (`expired`) |
| `running` | the supervisor started again and the upgrade state shows it went through | `done` |
| `running` | otherwise, at the next start or with no child to follow | `failed` (`interrupted`) |

A final request is copied to `last-request.json` and the slot freed. An apply request waits while the daily backup runs. The refusal and settings sentences are shared word for word between `lab/updates.py` `MESSAGES` and `src/control/updates.ts` `UPDATE_MESSAGES`: `lab/test_updates.py` checks that each Python sentence is in the TypeScript, and `tests/updates-routes.test.ts` that each TypeScript one is in `lab/updates.py`. The console's own sentences for a verdict not written yet are `CONSOLE_MESSAGES`, not shared.

## The console's side

`src/control/updates.ts` and the routes in `src/control/http.ts` serve the installation operator only (403 for anyone else), and every refusal ends in `refusal()`, which answers an `UpdateRefusal` with its own sentence (400 for input, 409 for state).

- **`GET /management/v1/updates`** reads `current.json`, `available.json`, `state.json`, `request.json` (else `last-request.json`), `check.json` and `settings.json` once each, through `readJsonCached`, so an unchanged file costs one stat. The running version is `current.json`, else the checkout's `release.json` without a commit. `available.json` is shown as it is: the supervisor moves a result made on another commit aside.
- **`install`** is `null` when nothing is on offer. Otherwise it is `{possible, reason, acknowledgement}` from the supervisor's `apply` verdict about that very version, with three console cases: no verdict at all (the supervisor has not finished starting), a verdict about another version or none (not judged yet), and an apply or rollback request under way (the `busy` sentence). The page shows `reason` as it is and asks for the acknowledgement when the verdict does.
- **`POST .../apply`** with `{version, acknowledged?}` refuses (409) when there is no verdict, when it is `null` (`nothing`), names another version (`other`), says `possible: false` (its `reason`), or asks for the acknowledgement and the body does not carry `acknowledged: true` (`acknowledge`). The request takes the tag from the verdict. The only check the console keeps is the request slot: the hard link that fails while another request exists (`busy`).
- **`POST .../rollback`** answers with the `rollback` verdict, as in [canRollback](#canrollback). **`POST .../check`** only asks. **`PUT .../settings`** validates as the supervisor does and returns the zone beside the saved settings.
- **The console** (`ui/Updates.tsx`, `ui/releases.ts`) keeps the update state in `UpdatesProvider`, read only by the banner and the Updates page, so a 2 second poll during a watched upgrade does not render the whole console again. `openRequest(view)` names the apply or rollback under way (a request not finished, or an `applied` or `rolling_back` phase): the page is busy while there is one and, loaded meanwhile, resumes watching it.

## What runs now (current.json)

`.lab/upgrades/current.json` is written by the supervisor only (`updates.publish_current`):

```
{version, commit, written_at,
 rollback: {started_at, possible, reason},
 apply: null | {version, tag, class, possible, reason, acknowledgement},
 pending, timezone: {name, offset}}
```

- **`version`, `commit`** are read once per supervisor process: after an update child moved the checkout, HEAD is the version that starts next, not the one running.
- **`apply`** is `null` only when `available.json` offers no release. Otherwise `tag` is `v` + `version`, `class` is the release's own (`safe`, `attended`, and also `rebuild` or `manual`, which the console can never install; the verdict then says so with the `class` sentence), and `possible` and `reason` come from `updates.apply_refusal`, the same question the supervisor asks again when it picks the request up: pending (`pending`), not a console class (`class`), not signed (`unsigned`), refusals in the check (`refused`). An `attended` release is judged as if acknowledged, and `acknowledgement: true` tells the console to ask first. Past those, `reason` names a passing blocker the supervisor does not wait out itself: another upgrade or rollback holding `upgrade.lock` (a command line run), or a backup or restore holding `backup.lock` that is not its own daily backup. An operation record still to settle is not one: the supervisor drains before it starts an update. `reason` is always a complete sentence.
- **`pending`** is true while the phase is `applied` or `rolling_back`: an upgrade or rollback waits for its restart or its confirmation.
- **Freshness:** a check result made on another commit is moved to `available.previous.json` before `current.json` is written, so no reader sees the release just installed as available; a check is then due at once (after the first 5 minutes, and unless a failed check is backing off).
- **When:** in full at start and after confirmation (the rollback verdict, which may read Git and the catalog, is judged again then and whenever the upgrade record changes); otherwise at most every 15 seconds, after each update child ends and when the daily backup starts and ends, written only when something other than `written_at` changed.

The console's own reading of it is in [The console's side](#the-consoles-side). Spawning the console before this first publish would start it sooner, but was not done: publishing first is what keeps the console from ever reading an `available.json` that names the version just installed.

## Confirmation gate and hold

`before_start` runs after the supervisor takes its locks and before the settle stage, which may open and migrate the control catalog. For a pending state it writes `.lab/upgrades/hold` and returns true; a snapshot that cannot be taken raises, the start counts as failed and the way back runs before the new version touched anything. Any other bookkeeping failure leaves the start ungated. A marker without a pending state is removed.

While gated, `Supervisor.run` starts only the console. The worker, the daily backup, Studio starts, sign-in applies, turning Realtime, Edge Functions or direct database access on or off, signing key rotation and the update scheduling all wait, so nothing leaves an effect the restore would not know about. Each turn polls `upgrade_health.Confirmation`:

- The deadline (120 s) starts at the first poll, once the console process exists, so setup before it does not use it up. A round runs every 2 s in a daemon thread, its probes at once (at most 16 together, the detail naming the first failure in probe order); each probe times out after 5 s, with any proxy variable ignored. A supervisor that stops during a round waits for it, at most about one probe timeout.
- A round is: the console's `/health` (which reads the catalog schema; `server.json` must name the server this supervisor started), the management Auth `/health`, and for each routed runtime (provisioned, not deleted, not in maintenance, not moved) Auth `/health`, REST `/` and Storage `/bucket` (with a `service_role` token and the tenant host) straight at the upstream service, then REST and Auth once more through the gateway, as an application reaches them, with the per-start probe token that passes the hold (`src/gateway/hold-bypass.ts`). All must answer 200.
- A passing round calls `upgrade_outcome(True)`: phase to `confirmed` or `rolled_back`, intent and marker removed, the outcome notification emitted; then the worker starts and `current.json` is published again. The deadline raises `RuntimeError`, `main` calls `upgrade_outcome(False)`, and the process exits 1.

The hold (`src/gateway/hold.ts`) is the marker **and** a pending phase in `state.json`, re-read at most once a second. Anything else fails open, including an unreadable state, so a stale marker can never wedge an installation. Held: everything except `/management/` gets `503` with `Retry-After: 5`; Realtime socket upgrades get 503; the direct database listener gets no target. Under `/management/`, reads, the management Auth realm (sign-in, refresh, sign-out), "roll back", "check now" and the update settings pass, since the snapshot restores none of what they write; every other management change gets 409 with a sentence saying changes are paused, because the way back would drop it silently. The supervisor's own probe through the gateway passes (`src/gateway/hold-bypass.ts`). Not held: the console's static files, Studio hosts and `/health`, which also reports `held`. When nothing is held, a request costs one cached `held()` answer and nothing else.

## Snapshot scope and restore rules

- **Scope:** every `*.sqlite` directly under `.lab/upstream/` (the control catalog) and the key store `.secrets/upstream/managed-keys.sqlite`. Not the JSON descriptors and journals under `.lab/upstream/`, not other `.secrets/` files, not the environment databases (the pre-upgrade backup covers those, and held traffic keeps applications from writing).
- **Taking:** SQLite's backup API from a read-only connection, so it is consistent while a process has the stores open; each copy fsynced; a manifest with size, SHA-256 and `user_version` per file; written under `<commit>-<UTC time>.partial` and renamed when complete. Directories are 0700. The newest three complete snapshots are kept, plus the one `state.json` names.
- **When:** once by `start` (it proves the copy works; nothing changes if it fails), again by `before_start` on the first start of the new version. The second is the one restored: it holds everything the old version wrote up to the restart. Later starts of the same pending version keep it, because the catalog may already be migrated.
- **Restoring:** only on the way back from `applied` with `attempted_at` set (a version that never started touched nothing, and restoring would drop what the running version wrote since `start`). Every file is verified against the manifest before any is written. Each store is then replaced atomically: a private temporary file, fsync, owner and mode of the store it replaces, leftover `-journal`, `-wal` and `-shm` removed, rename, directory fsync. The replacement is atomic per file, not across files. An operator `rollback` that restores requires the supervisor lock to be free, so it refuses while Sbarbase runs.
- A store the new version created that is not in the manifest is left in place.
- After confirmation nothing is restored. `rollback` keeps the control state and refuses when the catalog's `user_version` is newer than the `CATALOG_SCHEMA_VERSION` the previous commit's `src/control/catalog.ts` declares (no refusal when that commit declares none).

## Moving the checkout

Every way back to the previous version (the move back when `start` fails, an operator's rollback, the automatic way back in `after_start`, and the guard's own) goes through one move, `upgrade_guard.force_checkout` (`upgrade.move_back` wraps it with the intent and `bun install`). Only the forward move of `start` stays a plain checkout, since `plan()` just found the tree clean.

1. A `.git/index.lock` left by a git process that was killed is removed, only while the caller holds `upgrade.lock` and no git process can be using the checkout (`git_running`: one whose working directory is in the checkout, or cannot be read).
2. Evidence first (`upgrade_guard.set_aside_evidence`, which `upgrade.checkout` calls too, so an upgrade and a way back put it in the same place): changed tracked files under `docs/evidence/` and untracked ones the target ships are copied to `.lab/upgrades/evidence-<time>/` (0700), then restored from HEAD (index and tree, so a staged change or a rename cannot fail the move) or removed. Deleted evidence has nothing to copy; a copy already there is kept.
3. Every other tracked file that differs from HEAD (staged or not) and every untracked file the target would overwrite is copied to `.lab/upgrades/aside-<time>/` (0700), one folder per upgrade or way back. A copy already there is never overwritten: the first is the operator's own edit, a later one may be a half-written tree. A copy that fails stops the move before anything is overwritten.
4. `git checkout -q -f --detach <commit>`.
5. HEAD must be the commit and the tracked tree clean before anything is recorded; the files git rewrote get the checkout's owner.

A plain checkout used to fail here in two ways: a local edit made the way back refuse on every later start, with the unit restarting forever; and a `start` killed after it wrote part of the tree, before HEAD moved, made `git checkout <from>` a no-op, so the half-written tree was recorded `failed` and ran ungated.

**Before it gets there.** An operator's `rollback`, and so the console's rollback verdict, refuses a checkout with local changes to tracked files outside `docs/evidence/` (`upgrade.rollback_refusal`), naming up to three of them: a change made on purpose, such as a port in `compose.yaml`, should not move aside from a button. The automatic way back never refuses on it: the version it leaves is failing, and the changes go aside.

**When the move keeps failing.** The guard counts failed moves (and restores after them) in `move_failures`. The next starts try again, up to `MAX_ATTEMPTS`; then the outcome is terminal (`cannot_move`), chosen as the least harmful start:

| The checkout holds | Recorded | The start |
|---|---|---|
| the previous version, clean | `failed` (the move back of `start`) or `rollback_failed` (a way back, the snapshot restored first when it is due; a restore that fails says so) | goes on, ungated, on the previous version |
| the confirmed version, clean, after an operator's rollback of it (`back_from: confirmed`, which `begin_way_back` records) | `confirmed` again with `rollback_failure`; the previous version's intent is removed | goes on, on the version that passed its health checks |
| anything else (the failed version, a half-written tree, a way back from an older record without `back_from`) | `stuck: {at, reason}`, the phase stays pending | does not go on |

No exit status of an `ExecStartPre` stops systemd from restarting (`RestartPreventExitStatus` applies to the main process only), and Docker's `unless-stopped` restarts any exit, so staying stopped is the guard's own doing: it prints one line, releases its locks, waits `STUCK_WAIT` (300 seconds, inside the unit's `TimeoutStartSec` of 600) and exits 1. Each restart tries the move once more, so the installation comes back by itself once the cause (a full disk, wrong ownership) is gone. A start from a terminal (`lab/dev.py run_guard`, `SBARBASE_GUARD_WAIT=0`) gets the line without the wait. `upgrade.py status` shows the stuck reason.

**The supervisor after a failed child.** A nonzero exit of an apply or rollback child does not prove that nothing moved. Before it resumes provisioning, the supervisor checks the upgrade state and HEAD against the commit it started on (`Supervisor.unsettled`); when the state is pending or HEAD differs or cannot be read, it fails the request with a sentence saying Sbarbase restarts, stops and exits with 42, and the guard settles the checkout first. Whether the child got there comes from the state too (`Supervisor.moved_for_good`): an apply with `applied`, `moved` and HEAD at `to`, or a rollback with its way back done, counts as done even when the child exited nonzero (it could not write its outcome, say). The request is then `done`, the automatic try is not spent, and the supervisor restarts as after a clean exit.

**Backups of an upgrade.** `back_up` returns the run's time, which `start` records as `backup` and marks in `.lab/backups/upgrade-moved.json` (`backup.mark_moved`) once the checkout moved. Only marked runs count toward `UPGRADE_RUNS_KEPT`, so failed tries no longer take the protected slots. Before any run was marked, and with a damaged record, every upgrade run counts, as before; the first mark (or one replacing a damaged record) starts from every upgrade run there is, so no earlier upgrade loses its protection, and failed tries from before it keep theirs until newer upgrades push them out.

## Trust model

- **Source:** `https://github.com/M7MMAD-OMAR/sbarbase.git`, or `SBARBASE_RELEASE_SOURCE` (which `compose.yaml` passes into the container; empty means the canonical repository). Never the local `origin`. Network Git runs with `GIT_TERMINAL_PROMPT=0` and a 120 second timeout.
- **Listing is a hint:** `git ls-remote --tags` names candidates (`vMAJOR.MINOR.PATCH`, pre-releases only with `--preview`, which the console and the supervisor never pass). One tag at a time is fetched with `--no-tags --no-write-fetch-head` into `refs/sbarbase-releases/tags/`, so the operator's branches, tags and remotes stay as they are.
- **Verification fails closed:** no key in the signers file, no `ssh-keygen`, a lightweight tag, a tag object whose own `tag` header differs from the fetched name (a replayed signed tag), a tag not pointing at a commit, or `git verify-tag` with `gpg.format=ssh`, `gpg.ssh.allowedSignersFile` and `gpg.minTrustLevel=fully` that does not print a good signature or prints "No principal matched" (the exit code alone is not trusted across Git versions). An unsigned release is still reported, with `signed: false` and the reason in the refusals, so the console can say why.
- **Signers come from the running checkout:** `deploy/release-signers` at `ROOT`. A key a version does not list is not trusted by it, and a local edit to that file blocks upgrades as a local change.
- **The commit comes from the verified tag**, and `release.json` is read and validated at that commit; its `version` must equal the tag. Unknown manifest fields are ignored, so older installations keep reading later manifests.
- **`minimum_from`:** a release this version cannot reach directly is skipped with a reason and the next older one is offered.
- **Classification comes from the diff** (`git diff --name-only current target`), never from the manifest: `manual` when an entry of `lab/distro-image.lock.json` changes its id or the manifest declares migrations; `rebuild` when `Dockerfile`, `.dockerignore`, `compose.yaml`, `deploy/sbarbase.service` or `deploy/console-tls-proxy.ts` changes, or a file the release's own `Dockerfile` copies into the image (the sources of its `COPY` and `ADD`, a copy `--from` another image excepted); `attended` when the Auth, Storage or Realtime pin changes; else `safe`. The manifest can only make a release stricter. One diff of the pins (`pin_diffs`) serves the classification, the image changes shown and `upgrade.py plan()`; a lock file read at a commit id is read once per process.
- **What each path allows:** automatic mode only `safe`, signed, with no refusals; the console also `attended` once the operator acknowledged its warning. `start --release` also `rebuild` with `--allow-class rebuild` and `attended` with `--allow-class attended` (repeat the option for both), never `manual`. `start --to` is the operator's explicit choice: not verified, not classified; `plan()` still refuses a PostgreSQL image change.

## Settings, checks and the automatic decision

Settings (`check`, `automatic`, `window`) are validated identically in Python and TypeScript; a missing or invalid file means the defaults (check on, automatic off, 3:00 AM to 5:00 AM local time), so a damaged file can never turn automatic updates on. `automatic` requires `check`.

`check_due`: never within 5 minutes of the supervisor start; then every 6 hours; after a failure 30 minutes, 1, 2, 4 hours, capped at 6; and as soon as there is no result (right after an upgrade, when the one about the previous version was set aside), unless backing off. A check that fails (a source that cannot be read raises in `release_channel.check`, and `upgrade.py channel` exits 1) writes no result, so an offline host keeps the `available.json` it had.

`automatic_release` returns a release only when check and automatic are on, no backup runs, the local time is in `[start, end)` (crossing midnight when end is earlier), the release is `safe` and `apply_refusal` finds nothing; only then does it read the ledger, and the version must be neither `spent` nor `rolled_back` and within its tries. `blocked()` then waits out a backup or restore lock, the upgrade lock or an unsettled record rather than spending the attempt.

The ledger (`ledger.json`) is `{versions: {version: {announced, tries, last, spent, rolled_back}}}` for the last 50 versions. Each automatic try is counted before its request is created. A try is **spent** only once it passed its point of no return: `upgrade.py start` records `passed: true` in its outcome just before it starts the backup, and the supervisor marks the version spent when such a try ended without moving the checkout (the backup, the snapshot or the move failed), when the child ends or, after a crash, at the next start (`updates.settle`). A try that moved the checkout is judged by how the new version starts: confirmed, it runs; moved back, `announce_outcome` records `rolled_back`, except for a way back the state marks `retryable`. A refusal, a network failure fetching the release or pulling its images, and a drain that timed out spend nothing: that version is tried again, at most 3 times, 10 then 20 minutes apart, inside the window.

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
| `update.available` | info | after a successful check, once per version: the ledger's `announced`, set only when the event was written |
| `update.applied` | info | `applied` to `confirmed`, with the trigger |
| `update.rolled_back` | warning | `applied` to `rolling_back` with `automatic: true`, by the failing version after it restored the snapshot, so the row lands in the catalog the previous version opens |
| `update.rollback_failed` | critical | `applied` or `rolling_back` to `rollback_failed` |

An operator's rollback earns no notification; every way back, requested or automatic, sets the version's `rolled_back` in the ledger, in one place (`announce_outcome`), once the previous version starts (a console rollback when it is confirmed, not when the child ends). `version` is the release version, or a 12 character commit for a `start --to` upgrade.

The guard (`lab/upgrade_guard.py`) imports nothing from `lab/`, so a way back or a `rollback_failed` it records cannot emit anything. It appends a notice `{was, phase}` to the state instead; the next supervisor start (`dev.upgrade_notices`, gated or not, once the runtime is up) runs each through `announce_outcome` as if it had made that change itself, in the catalog of the version that runs then, and clears them under `upgrade.lock`.

## Tests

Workstation, 2026-09-25: `lab/test_release_channel.py`, `lab/test_updates.py`, `lab/test_upgrade.py` (which also holds the older upgrade tests) and `lab/test_upgrade_health.py`, 86 tests, OK; `tests/hold.test.ts`, `tests/updates-routes.test.ts` and `tests/updates-ui.test.ts`, 42 pass; after the console moved to the supervisor's install verdict, 54 pass. After the supervisor's side took on the verdict, the outcome record and the point of no return, the four Python files above with `lab/test_upgrade_guard.py` and `lab/test_upgrade_drain.py` hold 160 tests, OK, and the whole Python suite 978, OK. After the forced and verified way back, the terminal outcomes of the guard, the supervisor's settling after a failed child and the backup marking, those six files hold 183 tests and `lab/test_backup.py` 20, OK, the whole Python suite 1004, OK, and `bun test` 293 pass. `lab/vm-milestones.sh upgrade` exercises `start --to` only. The CI job's run of 2026-09-25 ([docker-upgrade-checks.json](../evidence/docker-upgrade-checks.json)) passed all 45 checks on a clean machine: a confirmed upgrade, three broken versions moved back by themselves (one after migrating the control catalog, whose snapshot restore took the schema from 4 back to 3; one whose PostgREST never answers; one that never passes health, with traffic held with 503 during the window), and unsigned and unlisted-key tags refused with nothing moved. It moved the checkout with `lab/upgrade.py`, not the console or automatic updates.

Acceptance from the plan: the three CI cases (a release that migrates the catalog and then fails, one that starts but fails health, an unsigned tag) passed, above. Still pending: the VM rehearsal of a real bump and back passes before automatic updates are described as ready.

## Known limits

- Unit tests and the CI cases on a clean machine only, as above; nothing on a real server or in the VM. No release signing key is listed in `deploy/release-signers`, so every release is refused as unsigned until a maintainer commits one; installations without that commit reach it through `start --to`.
- The first move onto the version that introduces the channel changes `Dockerfile`, `compose.yaml` and `deploy/sbarbase.service`: class `rebuild`, and the version before it has no channel, so it takes `start --to` and a rebuild or unit reinstall. A Docker image that was not rebuilt has no `ssh-keygen` and refuses every release with that reason. The new version's first start is still snapshotted and gated, because `before_start` sees an `applied` state without `attempted_at`.
- A way back that lands on a version older than the channel confirms its start without a health round or hold, and cannot deliver the `update.*` notifications.
- `lab/install_server.py supervise`, with or without `--apply`, rewrites the tracked `docs/evidence/supervisor-unit.json`. On this version that does not block an upgrade: `plan()` ignores changes under `docs/evidence/` and `set_aside_evidence` (in `lab/upgrade_guard.py` since 2026-09-26) copies them to `.lab/upgrades/evidence-<time>/` before the move. An installation whose `lab/upgrade.py` does not set evidence aside yet (that landed on 2026-09-25) still refuses; the workaround is to copy `docs/evidence/` aside and run `git checkout -- docs/evidence` as the service account once.
- The health round does not cover Realtime, Edge Functions or Studio, and one passing round confirms: a version that passes and then misbehaves is not moved back by itself.
- During the confirmation window (at most about two minutes from the console's start) management changes are refused with 409 by the hold, so the snapshot restore has none to drop; what passes (reads, sign-in, "roll back", "check now", the update settings) is nothing the snapshot restores. Database schema changes a newer Auth or Storage made at start are not undone.
- An automatic try that passed its point of no return and then failed (its backup, for example) spends that version for good, even when the cause passes by itself; only the tries before that point are repeated.
- The record of how a child ended (`outcome.json`) holds the last run only; the supervisor moves what matters (a spent try) into the ledger when the child ends or at its next start, before another child can replace it.
- The `sbarbase` command has no `channel` or `--release`.
- The forced move, the terminal outcomes and the stuck wait live in the guard, and the way back runs the copy of the guard taken from the version the upgrade left (`.lab/upgrades/guard.py`). So they protect upgrades started from a version that has them; an upgrade started from an older version is still moved back by that version's guard, plain checkout and all.
- The console's rollback verdict is judged at start, after confirmation and when the upgrade record changes, not when a file changes: after a local edit the page may still offer "Roll back", and the supervisor refuses the request with the local changes sentence when it picks it up.
- A supervisor killed during the confirmation (or at any time) leaves the owned containers running; the next start stops them before its preflight (`lab/leftover_runtime.py`, the unit's second `ExecStartPre`, and again in `lab/dev.py` after it takes its locks), unless a lock is held or authority state is pending. Not covered: a unit installed before that line (it still refuses at the preflight until reinstalled, or until `lab/leftover_runtime.py` is run once by hand), a way back that lands on a version without the file (its preflight refuses as before), and a start that declines because a receipt or journal is pending, which waits for the operator as it always did. Unit tested only; no live kill has been run.
- A guard that cannot even write `state.json` (a full or read-only disk) records nothing, so it cannot count its failures and the service manager's restarts loop until the disk is writable.
- A stale `.git/index.lock` is removed only when no git process could be using the checkout; a git process of another user whose working directory the service account cannot read counts as one, so on a host where root runs git elsewhere the lock waits for the operator.
