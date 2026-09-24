[العربية](studio.ar.md)

# Supabase Studio for an environment

Each environment can open the original Supabase Studio: the table editor, the SQL editor, users, and Storage buckets, all on that environment alone. Studio runs only while someone needs it, behind the console login, so it costs nothing when it is closed.

## Open it

1. Sign in to the console and open an environment's connection page.
2. Under **Studio**, press **Start Studio**. It takes a few seconds the first time.
3. Press **Open Studio**. Studio opens in a new tab on its own address, `<id>.studio.localhost` on the console's port.
4. When you are done, press **Stop**.

Only owners and admins of the client can start, open or stop Studio. Viewers do not see it.

On a server, reach the console through an SSH tunnel (`ssh -L 8790:127.0.0.1:8790 your-server`) as the [Docker guide](docker.md) shows. Browsers send every `*.localhost` name to your own machine, so the Studio tab goes through the same tunnel with no extra setup.

## What it can do

| Page | Works on |
|---|---|
| Table editor and SQL editor | The environment's own database, as its own Studio login |
| Authentication users | The environment's own Auth |
| Storage buckets and files | The environment's own files |

The SQL editor sees every row, including rows hidden by row security, like Studio on Supabase does. It cannot change the Auth or Storage schemas, create roles, or reach another environment's database.

Logs, Realtime, Edge Functions and the other pages that need services Sbarbase does not run yet are empty or hidden.

## How it is kept safe

- Every environment has its own Studio address, and a session for one never opens another.
- The console gives a one-minute ticket; the ticket becomes an eight-hour session cookie for that address only. Without it, nothing reaches Studio.
- Losing the owner or admin role closes access on the next request.
- Studio signs in to the database with its own login, which exists only while Studio runs and gets a new password on every start. It is not a superuser and owns nothing.
- Stopping Studio, or restarting Sbarbase, closes that login and removes both containers.

## Memory

A running Studio uses up to 768 MiB (Studio 512 MiB and postgres-meta 256 MiB). If the server lacks the room, Start fails with a message and nothing else is touched. Stop Studio when you are done.

CI starts Studio on a clean machine with every change, uses the table list, SQL, users and buckets through it, checks every refusal above, and stops it ([evidence](../evidence/docker-studio-checks.json)).
