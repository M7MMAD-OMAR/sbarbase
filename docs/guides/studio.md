[العربية](studio.ar.md)

# Supabase Studio for an environment

Each environment can open the original Supabase Studio: the table editor, the SQL editor, users, and Storage buckets, all on that environment alone. Studio runs only while someone needs it, behind the console login, so it costs nothing when it is closed.

## Open it

1. Sign in and create a project. Sbarbase creates **production** and opens a waiting tab from your Create action.
2. Production is provisioned, then Studio is started and opened automatically in that tab. The console stays open. **Stay in console**, or closing the waiting tab, cancels the automatic dashboard opening.
3. An existing project shows **Open Supabase dashboard**. Press it once to start Studio when needed and enter its original full page on `<id>.studio.localhost`, using the console port and sign-in. No custom bar or iframe is added to native Studio.
4. **Connection details** opens environment settings. **Database connection** includes **Stop dashboard** and direct database controls. **Sign-in settings** configures the self-hosted Auth service; native user management remains in Studio.

If the browser blocks the waiting tab, the dashboard is prepared and the console offers the normal Open button. If startup fails, the temporary tab closes and a retryable error stays in the console. **Retry dashboard status** recovers a failed status request. If provisioning fails, use **Retry** in the environment row. These retries do not create another project. The default production name does not certify deployment readiness.

Only owners and admins of the client can start, open or stop Studio. Viewers do not see it.

On a server, reach the console through an SSH tunnel (`ssh -L 8790:127.0.0.1:8790 your-server`) as the [Docker guide](docker.md) shows. Browsers send every `*.localhost` name to your own machine, so the Studio tab goes through the same tunnel with no extra setup.

## What it can do

| Page | Works on |
|---|---|
| Table editor and SQL editor | The environment's own database, as its own Studio login |
| Authentication users | The environment's own Auth |
| Storage buckets and files | The environment's own files |

The SQL editor sees every row, including rows hidden by row security, like Studio on Supabase does. It cannot change the Auth or Storage schemas, create roles, or reach another environment's database.

Studio's Logs, Realtime and Edge Functions pages expect Supabase's own platform services and stay empty or hidden. Use the console for those: [logs and metrics](logs-and-metrics.md), [Realtime](realtime.md) and [Edge Functions](edge-functions.md).

## How it is kept safe

- Every environment has its own Studio address, and a session for one never opens another.
- The console gives a one-minute ticket; the ticket becomes an eight-hour session cookie for that address only. Without it, nothing reaches Studio.
- Changing MFA policy or logging out revokes dashboard sessions through the actor epoch.
- Losing the owner or admin role closes access on the next request.
- Studio signs in to the database with its own login, which exists only while Studio runs and gets a new password on every start. It is not a superuser and owns nothing.
- Studio and its helper (postgres-meta) have no login of their own, so neither listens where other environments run. Each session gets a private network that only the server reaches; Edge Functions and databases of other environments cannot connect to it.
- Stopping Studio, or restarting Sbarbase, closes that login and removes both containers and the session's network.

## Memory

A running Studio uses up to 768 MiB (Studio 512 MiB and postgres-meta 256 MiB). If the server lacks the room, Start fails with a message and nothing else is touched. Stop Studio when you are done.

CI starts Studio on a clean machine with every change, uses the table list, SQL, users and buckets through it, checks every refusal above, and stops it ([evidence](../evidence/docker-studio-checks.json)).
