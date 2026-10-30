# Optional services: off by default, one place to control them, opt-in sleep

Status: proposal, nothing built. Written 2026-10-30 after a read-only review of the real server (v0.9.2 then v0.10.2).

## Why

Every environment can start extra containers: Realtime (320 MiB ceiling, 0.25 CPU), Edge Functions (384 MiB, 0.5 CPU), direct database access (a listener and a login) and scheduled jobs. Studio already starts on demand. The owner wants the platform to cost as little as possible by default, to switch any extra on or off from the console alone, and to stay safe while doing it.

## What the server showed (2026-10-30, measured, read only)

| Item | Finding |
|---|---|
| Realtime | On for one environment since 2026-10-19, no connection in the last 24 hours, 183 MiB resident (57% of its ceiling). It was stopped through the console's own path (`requestRealtime`, applied by `lab/realtime.py`) on the owner's word. |
| Edge Functions | On in two environments, 31 and 56 MiB. |
| Per environment Auth and REST | 9 to 29 MiB each. Not worth sleeping. |
| Storage, Postgres | One shared process each (90 MiB and 148 MiB). |
| Host | 16 GB RAM, 14 GB available, disk 10% used, container log rotation set, no swap. |
| Reclaimable | About 466 MB of build cache. About 2.3 GB of images, but some are the way back of an update and must stay. |

Realtime is already off for a new environment (`realtime_settings` has no row, so it reads `off`), and so are Functions, direct database access and scheduled jobs. So "off by default" holds today. What is missing is visibility, a hint when something on is unused, and an optional way to sleep.

## Decisions

1. **Per environment, never shared.** One Realtime, one Functions container per environment that turns it on (`docs/engineering/REALTIME.md`): the pinned Realtime owns a replication slot and tenant secrets, and Functions run tenant code. A shared one would trade the isolation the product promises for about 183 MiB per environment. The cost of a new one is bounded by admission (`resource_policy.start_placement`), which refuses the start when restart headroom is short.
2. **Off stays the default for every optional service,** and a service is never turned on or off by a side effect the person did not see. Deploying a function turns Functions on today; the console will say so before it does.
3. **Sleep is a mode the person chooses, never silent.** Each optional service has `Off`, `On` or `On demand`. `On` is what exists now. `On demand` is offered only for Realtime and Functions and only after the cold start numbers are measured (phase S3).
4. **The console is the only control surface.** No host command is needed to switch a service, see its cost or understand a refusal.

## Phases

### S1. A Services page per environment (no new risk)

One section for owners and admins listing Realtime, Edge Functions, Direct database access, Scheduled jobs and Studio, each with: state (off, starting, on, failed with the reason), memory now, last used, a switch, and what turning it on costs.

- **Memory now:** a sampler in the supervisor (`lab/dev.py`) writes `docker stats` for the `sbarbase-durable-*` containers to one private JSON file every 60 s; the control plane reads that file. No Docker socket reaches the console process.
- **Last used:** the gateway already counts Functions calls (`src/gateway/observe.ts`); count Realtime connections the same way (upgrade requests through the gateway). Direct database access and scheduled jobs read their own last connection and last run.
- **Capacity card:** the installation's headroom from `lab/resource_policy.py` and the cost of the next service in the plain words "needs about 320 MiB, 2.1 GiB free", so a refusal is never a surprise.
- **Switch:** the existing endpoints (`PUT /environments/{id}/realtime|functions|database|cron`), unchanged. The existing sections move into this page; routes, audit events (`realtime.requested`, `realtime.stop_requested`, ...) and the 409 while an update is being confirmed stay as they are.
- Tests: handler tests for the new read model, UI harness renders (light, dark, RTL, phone), a unit test that the sampler file is read with a size cap and stale data is shown as stale.

### S2. Hints, never actions

When a service has been on for 7 days with no use, the Services page shows "No connection for 11 days, 183 MiB. Turn it off?" and one operator notification (`docs/engineering/OPERATOR-NOTIFICATIONS.md`, deduplicated, repeated at most every 14 days). Nothing is changed. The 7 days is a setting with a floor of 1 day.

### S3. On demand mode (opt-in, gated by measurement)

State machine per service and environment: `off`, `on`, `sleeping`, `waking`, `failed`. `sleeping` keeps `desired=on_demand` in the catalog row so a restart of the host restores the right state without starting the container.

- **Wake:** only the gateway wakes a service, and only after it has verified the environment's API key and role (the same check that precedes every forward). An unauthenticated or invalid request never wakes anything. The wake asks the supervisor through the same pending row used today (`lab/realtime.py apply`); the gateway then holds the request for up to the measured cold start plus a margin and answers `503` with `retry-after` if the service is not up in time. Functions calls are idempotent to retry; for Realtime a websocket that does not connect in time simply fails and the client reconnects (the supabase-js client already does).
- **Sleep:** after N idle minutes (default 15, floor 5) and no open websocket and no in-flight call. A stop first drains: the gateway pause lease (`src/gateway/concurrency.ts`) refuses new calls for at most 10 seconds, then the container is stopped.
- **Abuse and flapping:** a wake needs a valid key, is limited per environment (at most 6 wakes per hour, then a clear `429`), only one wake may run on the installation at a time, and a service stays up at least the idle window after waking.
- **Admission:** a wake passes the same admission as a manual start. If headroom is short the request fails with `503` and the Services page shows "could not wake: not enough memory".
- **Gate:** S3 ships only if measured cold starts (Realtime, Functions) on the VM rehearsal and on the real server are low enough to hold a request without a visible failure; if Realtime's is too slow, S3 ships for Functions only.
- **Kill switch:** one installation setting turns every `On demand` into `On` without touching the saved choices.

### S4. Hygiene the review found

- Show disk and memory on the installation overview with the thresholds already in `lab/resource_admission.py`.
- A console action "Clear build cache" that runs `docker builder prune` only, with the size shown first. It never removes images (the way back of an update needs them); a plain statement of that sits beside the button.
- Document that the host has no swap and what the ceilings mean (`docs/engineering/RESOURCE-POLICY.md`).

## Cases to handle (each gets a test)

| Case | Behaviour |
|---|---|
| Toggled while an update is being confirmed | `409` with the existing "changes are paused" sentence. |
| Two admins toggle the same service | The second gets `409 change in progress` (exists for Realtime; extend to the others). |
| Supervisor dies after the row is written | The row stays `pending`; the next supervisor start applies it (receipt rows, `docs/engineering/PROVISIONING-RECEIPTS.md`). Never deleted by hand. |
| Turning Realtime off with open sockets | The page shows the live count first and asks to confirm; the stop drains for 10 s. |
| Turning Functions off with calls in flight | Same drain; deployed code and secrets are kept, only the container stops. |
| Host restart | Each service returns to its saved state: `on` starts, `sleeping` and `off` do not. |
| An update replaces containers (`lab/rolling.py`) | A sleeping or off service is not started by the replacement. |
| Backup and restore | Settings rows are part of the catalog backup; a restore brings services back as saved, never started by the restore itself. |
| Capacity refused | The row records `failed` with the reason in words, and the Services page says what to free. |
| Viewer or member role | Reads the state, cannot toggle (owner and admin only, as now). |
| Deploying a function while Functions is off | The console asks before turning it on; the API keeps its current behaviour for existing clients and reports `turned_on: true`. |
| Cold start slower than the hold | The client gets `503` with `retry-after`, and the next request succeeds; never a hung connection. |

## Security

- Wake only after authentication, never by an anonymous path; per environment rate limit; one wake at a time installation wide.
- The Services page and the sampler file carry container names, numbers and times, never environment variables, keys or logs.
- The supervisor and the sampler run as the existing service user; no new privileges and no Docker socket in the console process.
- Every state change writes an audit event with the actor and the reason (`user`, `idle`, `wake`, `capacity`).
- A kill switch and a floor on every timer keep a wrong setting from taking a service down for good.

## Verification

Unit and handler tests per phase; the live probes one container at a time (`lab/realtime-check.ts` extended for sleep and wake), the cold start numbers recorded under `docs/evidence/`, UI renders in both themes, RTL and phone width, bilingual guide updates (`docs/guides/realtime.md`, `edge-functions.md`, a new `services.md`) with demo screenshots, an independent review per phase, and a release per phase (S1 and S2 together as 0.11.0, S3 and S4 after).

## Open questions for the owner

1. Is 15 idle minutes the right default for Functions, and 30 for Realtime?
2. Should S3 wait for the measurements, or ship S1 and S2 first? (Recommended: S1 and S2 first.)
