# Optional services: measured cold starts (2026-10-30)

`cold-start.json` is the evidence of `lab/on-demand-check.py --runs 5`; `cold-start-earlier-run.json` is the run made before the replication slot cleanup was tightened (same script, same host, five runs). Both are in full: every run is listed, nothing is averaged away.

**Scope.** Disposable containers of the pinned images on a developer workstation (about 6 GiB of memory available at the start), one PostgreSQL, one Realtime or one Edge Functions at a time, placeholder Auth and REST, driven by the same `Runtime` code the supervisor runs for a wake and a sleep. Not the retained lab, not a server, not the VM rehearsal. The supervisor's quarter second beat and the gateway's few milliseconds come on top.

## What was measured

| Service | Wake to the first real answer | Runs |
|---|---|---|
| Edge Functions | 1.86 to 2.50 s, median 2.34 s (earlier run: 1.75 to 3.44 s) | a call to a function that boots a worker |
| Realtime | 12.32 to 12.94 s, median 12.71 s (earlier run: 12.3 to 14.8 s) | a client opens the socket and joins a channel that listens to database changes |

Realtime's `launch` is about 1.1 s and the login 0.2 s; about 11 s is the upstream Realtime's own start to its health check. The very first start of a Realtime (with its migration) took 26 to 29 s.

Replication slot, five of five runs in each file: none of the environment's slots exists after a sleep, and the slot exists again once a client joins after the wake (`slots` in the file).

## The gate and the decision

A first request is held while the service starts. supabase-js gives a channel 10 seconds to join before it counts the attempt as timed out and retries by itself, so the gate for On demand as a default is: **the longest measured wake to first answer is under 10 seconds**.

- **Edge Functions pass** (2.5 s at most). On demand is the default for Edge Functions of new environments.
- **Realtime does not pass** (12.3 to 14.8 s). New environments keep Realtime Off, and On demand for Realtime cannot be chosen unless an installation operator allows it (`realtimeOnDemand`), for hardware where it starts faster. The numbers are for one workstation; a server may differ, which is why it is a setting and not a ban.

Not measured: a real server, the VM rehearsal, a loaded host, many environments waking together (one wake at a time is enforced, so a burst queues).
