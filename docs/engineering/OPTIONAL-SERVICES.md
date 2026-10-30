# Optional services: the Services page, hints, and On demand

Plan: [2026-10-30-optional-services](plans/2026-10-30-optional-services.md). Guide: [Services](../guides/services.md). Each phase adds a section here.

Optional services are the ones an environment can run or not: Realtime (320 MiB ceiling), Edge Functions (384 MiB), direct database access, scheduled jobs and Studio. Auth and REST are not optional and are not on this page.

## S1: the Services page (read model)

`GET /management/v1/environments/{id}/services` (`src/control/services.ts`, `catalog.services`) answers every member of the organization, viewers included; the switches stay on the existing routes (`PUT .../realtime|functions|scheduled-jobs`), owners and admins only, with their audit events and 409s unchanged. The answer carries state, the reason of a failure, memory now and its ceiling, last use, what turning it on adds, open Realtime sockets or Functions calls in flight, and the host's free memory. It carries no key, address, variable or log.

- **Memory.** The console process has no Docker socket. The supervisor samples `docker stats` for the installation's containers once a minute in a thread (`lab/service_stats.py`, called from `lab/dev.py`) and writes `.lab/upstream/service-stats.json` (0600, one rename). `readStats` refuses a file over 256 KiB, a version it does not know, a container name outside the pattern and any non-integer figure. A sample older than 3 minutes is `stale` and its numbers are not shown as current. The file also holds the cost of one more Realtime or Edge Functions container, taken from `resource_policy.TIERS`, so the page never duplicates a constant.
- **Last use.** `src/gateway/activity.ts` (`ServiceActivity`) is told by the gateway: a Functions call after its API key was verified (`src/gateway/managed.ts`; a request that failed authentication never counts), and a Realtime socket accepted by the front listener (`UpgradeDecision.onOpen`, called once the socket reaches Realtime, released when it closes). Times are kept in memory and written to the catalog table `service_activity` at most once a minute, a later time winning. In flight Functions calls are the gate's own count (`ConcurrencyGate.inFlight`), so a call counts until its answer has ended. Direct database access and scheduled jobs record no last use; the page says "Not recorded". Studio uses its existing `studio_activity`.
- **Quiet time** (`ServiceActivity.quietFor`, used from S3): never quiet while a socket is open or a call is in flight, within the window of the last use, or before this process has itself been up for the window.
- **Not a change of behaviour.** Nothing here starts or stops anything; Realtime, Functions, database access and scheduled jobs are off for a new environment exactly as before.

### Not established

The console against a live installation (the renders use stubs; the route is tested against the real handler and catalog). Realtime sockets and Functions calls are counted per console process; two processes during a handover each count their own.
