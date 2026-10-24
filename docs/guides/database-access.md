[العربية](database-access.ar.md)

# Database access

Each environment can give you a PostgreSQL connection string for migrations and tools: `psql`, `pg_dump`, Prisma, Drizzle and the like. It is off until an owner or admin turns it on.

## Turn it on

1. Open the environment's page in the console.
2. Under **Database**, press **Turn on database access**.
3. Copy the connection string it shows. The password is only shown once; **New password** replaces it, and the old one stops working.

The string looks like this:

```text
postgresql://e_…_developer:<password>@127.0.0.1:6543/e_…
```

## Connect from your computer

The address is on the server itself. Open an SSH tunnel, then use the string as it is:

```bash
ssh -N -L 6543:127.0.0.1:6543 you@your-server
psql "postgresql://e_…_developer:<password>@127.0.0.1:6543/e_…?sslmode=disable"
```

`deploy/db-tunnel you@your-server` does the same and reconnects if the link drops (it is a small shell script; copy it to your computer if you do not have the repository).

Examples:

```bash
psql "postgresql://…@127.0.0.1:6543/e_…?sslmode=disable" -v ON_ERROR_STOP=1 -1 -f supabase/migrations/20261017000000_init.sql   # one migration file
pg_dump "postgresql://…@127.0.0.1:6543/e_…?sslmode=disable" --schema=public > public.sql
DATABASE_URL="postgresql://…@127.0.0.1:6543/e_…?sslmode=disable" npx prisma migrate deploy
supabase db push --db-url "postgresql://…@127.0.0.1:6543/e_…?sslmode=disable"   # every file in supabase/migrations
supabase gen types typescript --db-url "postgresql://…@127.0.0.1:6543/e_…?sslmode=disable" > database.types.ts
```

## Connect from a service, such as Cloudflare Hyperdrive

A Cloudflare Worker cannot open an SSH tunnel, and Hyperdrive requires TLS and has no fixed addresses to allow. For this, an owner or admin can open **external access** for one environment. It is off until you do, and it needs database access on.

1. Under **Database**, press **Allow external connections**. The console shows the certificate authority (CA) that clients verify the server with; copy or download it. On the server, `sbarbase database-external <environment> on` does the same from the shell ([the command](cli.md)).
2. Make the listener reachable by the service **without opening a port to the internet**. It listens on the server's loopback address, port 6545 (`SBARBASE_DATABASE_EXTERNAL_PORT`). With Cloudflare, run a Cloudflare Tunnel connector on the server and give Workers VPC a TCP service that points at it:

```bash
# 1. Cloudflare dashboard, Networking, Tunnels: create a tunnel and run the install command it shows on the server
#    (cloudflared as a service). `wrangler tunnel create my-server-db` also creates one and prints its id.
# 2. Point a Workers VPC service at the listener, through that tunnel:
wrangler vpc service create my-server-db --type tcp --tcp-port 6545 --app-protocol postgresql \
  --tunnel-id <tunnel-id> --ipv4 127.0.0.1 --cert-verification-mode disabled
```

   `disabled` is meant: Workers VPC trusts only public and Cloudflare Origin CA certificates, and the Cloudflare Tunnel is the authenticated, encrypted path to your server. Without Cloudflare, use any private network or tunnel that reaches port 6545 (WireGuard, Tailscale, a TLS-passthrough proxy). Setting `SBARBASE_DATABASE_EXTERNAL_BIND` to a public address publishes the port: do it only behind a firewall that allows the service's addresses, and add the host name to `SBARBASE_DATABASE_TLS_NAMES` so `sslmode=verify-full` can match.
3. Create the Hyperdrive with the environment's developer login (the password you saved) and caching off, since a payment flow needs fresh reads:

```bash
wrangler hyperdrive create my-db --service-id <vpc-service-id> --database e_… --user e_…_developer \
  --password '<password>' --caching-disabled --origin-connection-limit 8
```

   Keep the limit under the login's 10 connections so a migration through the SSH tunnel still gets a session.
4. In the Worker, use the binding's connection string with a driver that supports transactions (`pg` 8.16.3 or newer, `postgres`). A transaction (`sql.begin`) works because Hyperdrive gives the Worker a real PostgreSQL session.

Other clients connect straight to the listener with `sslmode=verify-ca` and the CA from the console (`sslrootcert=ca.pem`). A cleartext connection to this port is refused, and so is any login other than this environment's developer login. Closing external access, or turning database access off, refuses new connections at once and ends this environment's open external sessions within a second. Whatever tunnel or network you put in front of the port must itself be authenticated (a Cloudflare Tunnel behind Workers VPC, WireGuard, SSH), because everything behind it reaches the listener as one address.

## What the developer login can do

It works like a Supabase project's `postgres` user, inside this environment's database only:

- Create and change tables, views, functions and policies in `public`. New tables are granted to `anon`, `authenticated` and `service_role`, so the API can reach them, and row level security decides what each caller sees. These default grants are set once, when access is first turned on; a migration that narrows them keeps its choice.
- Add triggers on `auth.users` (for example, create a profile at sign-up) and policies on `storage.objects`.
- Read and write every row; it bypasses row level security, as the `postgres` user does.
- Keep migration history in `supabase_migrations`, a schema made for it, where [AI agents](agents.md) record what they applied.
- Create schemas of its own, such as `app` or `private`, and rename them. It cannot use a name the platform creates itself: `auth`, `storage`, `extensions`, `public`, `realtime`, `_realtime`, `vault`, `pgsodium`, `cron`, `net`, `graphql`, `graphql_public`, a few more (`RESERVED_SCHEMAS` in `lab/durable_runtime.py`), or any name starting with `supabase` or `sbarbase`. A schema it made first under such a name would run its code with the platform's rights, so PostgreSQL refuses the statement, and a rename into one of these names too.
- Keep secrets in Vault (`vault.create_secret`, `vault.update_secret`, `vault.decrypted_secrets`), as on Supabase; `service_role` reads them too. The login cannot pass this access on to other roles, so a grant to `anon` cannot open the secrets to the API.
- Create the extensions an application uses, such as `pg_trgm`, `btree_gist`, `vector`, `pgtap` and `pg_graphql`. Extensions that reach the server's network, see every environment's activity, load code, write files on the server or hold the server's key are refused on a shared server: `dblink`, `postgres_fdw`, `http`, `pg_net`, `wrappers`, `pg_cron`, `pg_tle`, `pgsodium`, `pg_stat_statements`, `pg_stat_monitor`, `pg_walinspect`, `pg_buffercache`, `pg_prewarm`, `pg_repack`, `pgaudit`, `pgroonga` and a few more (`RESERVED_EXTENSIONS` in `lab/durable_runtime.py`).

- Add tables to the `supabase_realtime` publication, which always exists and belongs to it, as it belongs to a project's `postgres` user on Supabase. The publication includes the whole `public` schema, so tables there are published already; adding one is accepted and changes nothing. Tables of other schemas can be added one by one. If the login drops it, the platform makes it again the next time access is turned on.

These rights are given when access is turned on; for an environment whose access was already on, turn it off and on once.

Vault secrets are encrypted with one root key that belongs to the server, not to the environment: every environment on the server uses the same key. They stay apart because each environment reaches only its own database. Keep that in mind for backups: a copy of one environment's `vault.secrets` can be read in any environment of the same server, and on another server only with this server's key.

It is not a superuser, cannot create roles or databases, and cannot reach another environment's database. It holds at most 10 connections.

## How it is kept safe

- The listener is on the server's loopback address, port 6543 by default. It reads each connection's login before anything reaches PostgreSQL and passes on only an environment's developer login asking for that environment's own database. Every other login, the superuser included, is refused there.
- PostgreSQL then checks the password (SCRAM). Turning access off closes the login and every session it has open.
- The listener on 6543 accepts TLS (`sslmode=require` works) and also cleartext; the SSH tunnel encrypts it for you. `SBARBASE_DATABASE_BIND` can expose it on another address, but do that only on a network you trust ([configuration](../reference/configuration.md)).
- The external listener (6545) takes TLS only, only for environments whose external access is on, and with the same login rules. Each attempt counts against a per-address and a total budget, and ten failed passwords for one environment in a minute close its external logins for a minute. TLS uses a private certificate authority Sbarbase makes on first start, kept in `.secrets/upstream/database-tls` (the server's `openssl` makes it; without it the external listener stays off) ([design](../engineering/EXTERNAL-DATABASE.md)). Checked on a real server with Cloudflare Hyperdrive, including a transaction from a Worker and the refusals ([evidence](../evidence/external-database-checks-2026-10-24.json)).

CI turns access on for an environment on a clean machine with every change, runs a Supabase-style migration through the listener, and checks the refusals, a password reset and turning it off ([evidence](../evidence/docker-database-checks.json)). A separate check signs in as one environment's login and tries every reserved schema name, renames, refused extensions, the Realtime publication, the guard itself, Vault and the other environment's database ([evidence](../evidence/developer-schema-checks-2026-10-19.json)).

## Limits

- One login per environment, with up to 10 connections; there is no connection pooler yet.
- Through the SSH tunnel `sslmode=disable` still works; `require` works too. The external listener refuses cleartext.
- Turning access on, and **New password**, give the login its rights on your tables again. A transaction your own application leaves open on one of them (a request still running, a session idle in a transaction) makes this wait up to 10 seconds per step; after that it fails and says so, and you try again. Other environments on the server do not wait for it ([design](../engineering/DATABASE-ACCESS.md)).
- A migration that names the `postgres` role, for example `alter default privileges for role postgres`, is refused: each environment's developer login plays that part, and `postgres` itself is shared by the whole server. Write it without `for role postgres`, so it applies to the login running it.
