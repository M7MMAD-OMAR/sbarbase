[العربية](studio.ar.md)

# Supabase Studio for an environment

Each environment can open the original Supabase Studio: the table editor, the SQL editor, users, and Storage buckets, all on that environment alone. Studio runs only while someone needs it, behind the console login, so it costs nothing when it is closed.

## Open it

1. Sign in and create a project. Sbarbase creates **production** and opens a waiting tab from your Create action.
2. Production is provisioned, then Studio is started and opened automatically in that tab. The console stays open. **Stay in console**, or closing the waiting tab, cancels the automatic dashboard opening.
3. An existing project shows **Open Supabase dashboard**. Press it once to start Studio when needed and enter its original full page on the environment's own Studio address, using your console sign-in. No custom bar or iframe is added to native Studio.
4. **Connection details** opens environment settings. **Database connection** includes **Stop dashboard** and direct database controls. **Sign-in settings** configures the self-hosted Auth service; native user management remains in Studio.

If the browser blocks the waiting tab, the dashboard is prepared and the console offers the normal Open button. If startup fails, the temporary tab closes and a retryable error stays in the console. **Retry dashboard status** recovers a failed status request. If provisioning fails, use **Retry** in the environment row. These retries do not create another project. The default production name does not certify deployment readiness.

Only owners and admins of the client can start, open or stop Studio. Viewers do not see it.

## Open it from anywhere

By default Studio's address is `<id>.studio.localhost`, which every browser sends to its own machine. So it works on your computer, or on a server through an SSH tunnel (`ssh -N -L 8790:127.0.0.1:8790 you@your-server`, then open `http://localhost:8790`), as the [Docker guide](docker.md) shows. From the console's public address, the console says this instead of opening a tab the browser cannot reach.

To open Studio from the public address, set up one domain once. Every project and environment you create afterwards gets its own Studio address under it, with no DNS or certificate work per project.

1. **Pick a domain that holds no other site.** The wildcard record in the next step answers every name under the domain that has no record of its own, so use a domain meant for this, not one that already serves other sites. Named records you already have keep working either way; any other name reaches Sbarbase, which refuses it.
2. **See what to provide.** On the server run `/usr/bin/python3 lab/studio_address.py plan example.com`. It prints the address pattern (`studio-{id}.example.com`) and the two things you provide: a wildcard DNS record (`*.example.com`, proxied if you use Cloudflare) and a certificate that covers `*.example.com`. With Cloudflare, create an Origin CA certificate for `*.example.com` and `example.com`, generate the key on the server and paste only the request into Cloudflare, then install it on the TLS proxy.
3. **Apply it.** Run `sudo /usr/bin/python3 lab/studio_address.py apply example.com`. It writes `SBARBASE_STUDIO_HOST` as a systemd drop-in for the console and the TLS proxy, which both read it, and restarts them. It refuses when the proxy already passes a different `--studio-host`.
4. **Check it.** Run `/usr/bin/python3 lab/studio_address.py verify example.com`. It says which step is missing: DNS that does not resolve, a certificate that does not cover the name, or a server that refuses the address.

Each environment then opens Studio on its own address, such as `studio-<24 hex>.example.com`. The proxy passes only names that match the pattern and refuses every other name with 421, so the wildcard never shows the console under another name. To undo it, delete the two drop-ins named `studio-host.conf` (under `sbarbase.service.d` and `sbarbase-proxy.service.d`), restart both services and delete the DNS record.

Good to know:

- If the wildcard would sit on a domain that serves other sites, do not use it. Name each environment's record yourself (one `studio-<24 hex>` record per environment) or use a domain of its own.
- On Cloudflare Free the edge certificate covers one level under the domain, so keep the pattern `studio-{id}.example.com`, not `{id}.studio.example.com`.
- With Docker, export `SBARBASE_STUDIO_HOST=studio-{id}.example.com` before `deploy/compose.sh up` (the launcher forwards it to the console), and run the TLS proxy with the same variable or flag. `apply` writes systemd drop-ins, so it is for the systemd install.
- The console and the proxy read the same `SBARBASE_STUDIO_HOST`. The proxy's `--studio-host` flag still works and wins when both are set.
- Studio is reached on port 443; a public address on another port is not supported.
- With a proxying CDN such as Cloudflare in front, the CDN terminates TLS and sees Studio's traffic, as it sees the console's.
- A request that another site starts is refused, except a plain page visit, which lands on the project home without its query, so a link cannot open the SQL editor with SQL already in it.

On Supabase's cloud the dashboard is Studio itself, built for many projects. The Studio image a server runs serves one project at the root of its own address, which is why each environment has its own Studio address and opens in its own tab.

## What it can do

| Page | Works on |
|---|---|
| Table editor and SQL editor | The environment's own database, with the rights of its developer login |
| Authentication users | The environment's own Auth |
| Storage buckets and files | The environment's own files |
| Saved SQL snippets | A folder inside this Studio session; they are gone when Studio closes, so keep lasting SQL in migrations |
| Edge Functions list and code | A copy of each function's deployed code, taken when Studio opens; deploy from the console or the CLI |

Studio works as the environment's developer login, as Studio on Supabase works as the project's `postgres` user: it sees every row, including rows hidden by row security, and it can change the tables, policies and schemas that migrations made. What it creates belongs to the same owner, so later migrations can change it. It cannot create roles, act as a superuser, or reach another environment's database.

Studio's Logs and Realtime pages expect Supabase's own platform services and stay empty or hidden. Use the console for those: [logs and metrics](logs-and-metrics.md) and [Realtime](realtime.md). On a server with a firewall, Studio's Storage and users pages need the route described in [server deployment](server-deployment.md#firewall-two-routes-from-containers-to-the-host).

## How it is kept safe

- Every environment has its own Studio address, and a session for one never opens another.
- The console gives a one-minute ticket that works once; it becomes a session cookie for that address only, HttpOnly, and on a public address `Secure` with the `__Host-` prefix, so no sibling site can set or read it. Without it, nothing reaches Studio.
- While the Studio tab is in use, the session renews itself, so it does not close under you. It ends after eight hours without any request, or seven days after you opened it from the console, whichever comes first; then open Studio again from the console. Signing out of the console, a password change or losing the owner or admin role ends it at once.
- A request that changes something must come from that Studio's own page (its `Origin`); a page on another site of the same domain is refused.
- Changing MFA policy or logging out revokes dashboard sessions through the actor epoch.
- Losing the owner or admin role closes access on the next request.
- Studio signs in to the database with its own login, which exists only while Studio runs and gets a new password on every start. It is not a superuser and owns nothing.
- Studio and its helper (postgres-meta) have no login of their own, so neither listens where other environments run. Each session gets a private network that only the server reaches; Edge Functions and databases of other environments cannot connect to it.
- Stopping Studio, or restarting Sbarbase, closes that login and removes both containers and the session's network. After a restart or an upgrade, a Studio used in the last eight hours starts again by itself with a new login, and an open tab shows "Studio is starting" for a few seconds and then continues, without opening it again.

## Memory

A running Studio uses up to 768 MiB (Studio 512 MiB and postgres-meta 256 MiB). If the server lacks the room, Start fails with a message and nothing else is touched. Stop Studio when you are done.

CI starts Studio on a clean machine with every change, uses the table list, SQL, users and buckets through it, checks every refusal above, and stops it ([evidence](../evidence/docker-studio-checks.json)).
