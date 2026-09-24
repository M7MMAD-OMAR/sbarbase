[العربية](database-access.ar.md)

# Database access

Each environment can give you a PostgreSQL connection string for migrations and tools: `psql`, `pg_dump`, `supabase db push --db-url`, Prisma, Drizzle and the like. It is off until an owner or admin turns it on.

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

Examples:

```bash
supabase db push --db-url "postgresql://…@127.0.0.1:6543/e_…?sslmode=disable"   # run your Supabase migrations
pg_dump "postgresql://…@127.0.0.1:6543/e_…?sslmode=disable" --schema=public > public.sql
DATABASE_URL="postgresql://…@127.0.0.1:6543/e_…?sslmode=disable" npx prisma migrate deploy
```

## What the developer login can do

It works like a Supabase project's `postgres` user, inside this environment's database only:

- Create and change tables, views, functions and policies in `public`. New tables are granted to `anon`, `authenticated` and `service_role`, so the API can reach them, and row level security decides what each caller sees.
- Add triggers on `auth.users` (for example, create a profile at sign-up) and policies on `storage.objects`.
- Read and write every row; it bypasses row level security, as the `postgres` user does.

It is not a superuser, cannot create roles or databases, and cannot reach another environment's database. It holds at most 10 connections.

## How it is kept safe

- The listener is on the server's loopback address, port 6543 by default. It reads each connection's login before anything reaches PostgreSQL and passes on only an environment's developer login asking for that environment's own database. Every other login, the superuser included, is refused there.
- PostgreSQL then checks the password (SCRAM). Turning access off closes the login and every session it has open.
- Traffic is not encrypted by the listener; the SSH tunnel encrypts it. `SBARBASE_DATABASE_BIND` can expose the listener on another address, but do that only on a network you trust ([configuration](../reference/configuration.md)).

CI turns access on for an environment on a clean machine with every change, runs a Supabase-style migration through the listener, and checks the refusals, a password reset and turning it off ([evidence](../evidence/docker-database-checks.json)).

## Limits

- One login per environment, with up to 10 connections; there is no connection pooler yet.
- The listener declines TLS; use `sslmode=disable` or `prefer` through the tunnel.
