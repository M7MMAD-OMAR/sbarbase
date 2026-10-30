[العربية](services.ar.md)

# Services

The **Services** page of an environment lists the optional services that cost memory or a login while they are on: [Realtime](realtime.md), [Edge Functions](edge-functions.md), [Scheduled jobs](scheduled-jobs.md), [direct database access](database-access.md) and Studio. Each environment has its own; turning one on or off never touches another environment.

## What the page shows

For every service, the **Overview** shows:

- **State:** **Off**, **Starting…**, **On**, **Stopping…** or **Not applied** (with the reason in words, for example that the server has no room for another Realtime).
- **Memory now:** what the service's container holds, next to its ceiling, for example `183 MiB of 320 MiB`. Services with no container of their own (scheduled jobs, direct database access) say so.
- **Last used:** when a Realtime connection was last open or closed, and when an Edge Functions call last arrived carrying a valid API key (a preflight or a call with no key does not count, since anyone can send those). Studio shows its own last use. Scheduled jobs and direct database access do not record one, and the page says **Not recorded** instead of guessing.

Open a service in the left list for the same facts in one place, its switch, and what turning it on costs: a sentence such as "Turning it on needs about 320 MiB. This server has about 2.1 GiB of memory available now and keeps a reserve, so it can still say no." The available memory comes from the server itself; the start itself is admitted against the server's ceilings and reserve, as always.

## Who can use it

Every member of the client can read the page. Only owners and admins can switch a service. Realtime, Edge Functions and Scheduled jobs are switched here; direct database access and Studio keep their own pages, and the page links to them.

If a service has open connections (Realtime) or running calls (Edge Functions), turning it off first asks you to confirm and says how many there are. The page asks about what is open right now, not about what it showed a minute ago. Turning Edge Functions off ends calls that are still running.

## How the memory is measured

The supervisor reads the containers' memory once a minute and writes one small private file that the console reads (`lab/service_stats.py`); the console process never touches the Docker socket. A figure older than three minutes is shown as out of date, never as current. The page asks again every few seconds while a service is starting or stopping.

## Limits

- The memory and last-used figures come from this server's own counters. A restart of the console starts the in-memory part again; the last-used time itself is kept in the catalog.
- Open Realtime connections and running calls are counted by the console process that serves you.
