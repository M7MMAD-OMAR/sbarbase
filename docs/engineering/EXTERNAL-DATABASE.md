# External database access (TLS listener, per-environment opt-in)

Scope: how a service that cannot use an SSH tunnel, such as Cloudflare Hyperdrive from a Worker,
reaches one environment's PostgreSQL with a real connection and SQL transactions. Written
2026-10-24. Code: `src/http/database-proxy.ts` (TLS and the guard hook), `src/http/database-external.ts`
(`ExternalGuard`, `externalListen`), `src/http/database-tls.ts` (the certificate authority),
`externalDatabaseHandler` in `src/control/database.ts`, `database_external` in `src/control/catalog.ts`,
the wiring in `lab/upstream-server.ts`, the `ExternalAccess` section of `ui/Database.tsx`.
Reader guide: [database access](../guides/database-access.md). Decision: [decisions](../decisions/README.md).

## Why

Supabase's cloud gives a project a public PostgreSQL address with TLS, a password and a pooler.
Sbarbase's database listener (port 6543) is on loopback, declined TLS and is meant for an SSH
tunnel, so a Cloudflare Worker could not reach a self-hosted environment: Hyperdrive has no fixed
egress addresses (an allowlist is impossible), it refuses a connection without TLS, and the REST API
cannot run the multi-statement transactions a payment flow needs.

## What was verified before the design

- The gateway (`databaseProxy`) answered `SSLRequest` with `N`, so `sslmode=require`, which Hyperdrive
  always uses, failed with "TLS not supported by the database" (Hyperdrive error 2012).
- It forwards the startup message and then every byte untouched, so PostgreSQL's own SCRAM-SHA-256
  check stays authoritative. The gateway only decides who may start a session (the environment's
  developer login, for its own database).
- Hyperdrive documents two ways to a private database: Workers VPC (a TCP service through a Cloudflare
  Tunnel, recommended by Cloudflare) and a Tunnel hostname guarded by Access with a service token. Both
  need the origin to speak TLS. Workers VPC trusts publicly trusted and Cloudflare Origin CA
  certificates and offers `verify_ca` or `disabled` for a private one; Hyperdrive itself accepts an
  uploaded CA with `verify-ca`.

## Design

1. **TLS in the gateway.** With a certificate the listener answers `SSLRequest` with `S`. Bun cannot
   start TLS on a socket it already accepted, so the connection is piped to a TLS server of the same
   process on a loopback port (`pending` keys the piped connection by its source port and carries the
   client's real address and the outer socket). A TLS connection that arrives without an entry is
   dropped, so that port gives nothing to a local process that did not come through the outer listener.
   Bytes after `SSLRequest` in the same read are refused (plaintext injected before the handshake).
   TLS 1.2 or newer.
2. **A private certificate authority**, made on first start with the system `openssl` into
   `.secrets/upstream/database-tls`: an ECDSA P-256 CA (10 years, `pathlen:0`), a server certificate
   (397 days, `serverAuth`, names from `SBARBASE_DATABASE_TLS_NAMES` plus `localhost`, `127.0.0.1`,
   `::1`), renewed when 30 days remain or the names change, the CA kept so clients keep trusting it.
   Keys are `0600`. `ca.pem` is public and is shown by the console.
3. **A second listener, default `127.0.0.1:6545`** (`SBARBASE_DATABASE_EXTERNAL_PORT`,
   `SBARBASE_DATABASE_EXTERNAL_BIND`). It requires TLS (`requireTls`: a cleartext startup is refused
   before PostgreSQL), and a `ConnectionGuard` decides, before a byte reaches PostgreSQL:
   - the environment must have external access on (`database_external.enabled`, read from the catalog
     for every connection);
   - every attempt spends from a per-address and a total budget (30 and 120 per 10 s);
   - 10 failed passwords for one environment within a minute close it for a minute. The guard sees
     the outcome by reading the server's first messages (`AuthenticationOk`, or an error with SQLSTATE
     28P01) without changing a byte of the stream.
   The login rules of the loopback listener still apply (`decide`): only `e_<id>_developer` for its own
   database, so another tenant's database, the superuser and the service logins stay unreachable.
4. **A per-environment flag**, closed by default. Owners and admins set it
   (`PUT /management/v1/environments/{id}/database/external`) only while database access is on, the
   change is audited (`database.external_enabled`, `database.external_disabled`), and turning database
   access off removes it. The catalog schema is version 5.
5. **The connection itself is not published by Sbarbase.** The listener is on loopback; an operator's
   tunnel connector (Cloudflare Tunnel, WireGuard, a reverse proxy) or an explicit
   `SBARBASE_DATABASE_EXTERNAL_BIND` makes it reachable. This is deliberate: Sbarbase opens no
   firewall port and holds no Cloudflare credential.

## Limits and known trade-offs

- **Open sessions end within a second of losing access.** The guard keeps the external sessions of
  each environment and, once a second while there are any, closes those of an environment whose
  external access is off (a session ends after the flag is turned off, or after database access is
  turned off, which clears the flag). Sessions on the 6543 listener are not tracked by this: they end
  when the supervisor closes the developer login, as before.
- **The failed-login lock is per environment, not per source, and a success does not clear it.** A
  tunnel presents every client as loopback, so a per-source lock would be useless there; an attacker
  who can reach the listener and knows an opted-in environment id (96 bits, visible in the connection
  string) can hold that environment's external logins closed for a minute at a time. Passwords are 24
  random bytes, so the lock limits load, not guessing. Only SQLSTATE 28P01 counts: a login refused by
  role rules (28000) does not.
- **Idle and half-open connections.** Bun does not enforce `tls.Server`'s `handshakeTimeout`, so the
  outer socket carries its own 10 s bound from `S` until the handshake is claimed, and 10 s for the
  startup message. A cancel request (no login to admit) is refused in cleartext on a TLS-only
  listener, and nothing after it is piped to PostgreSQL. The loopback TLS port accepts a connection only once per piped client and only from the
  address the outer listener connected from.
- **Certificate renewal** is checked every six hours in the running process; a renewed certificate
  starts a new loopback TLS server (Bun's `setSecureContext` does not change what a running one
  presents) and new connections use it, while open sessions keep theirs; the key pair is checked to
  match, so a crash between writing the two files is repaired at the next start.
- **The accept budget is shared by everything behind a tunnel** (every client is loopback), so whoever
  can reach the tunnel can starve legitimate reconnects. The tunnel must therefore be an authenticated
  one (a Cloudflare Tunnel behind Workers VPC or Access, WireGuard, SSH), never a public relay.
- **No pooler.** One login per environment holds at most 10 connections. Hyperdrive pools in front of
  it; set its maximum connections to 10 or less.
- **Hyperdrive and `verify-ca`.** Use the CA from the console. `verify-full` needs the Hyperdrive host
  name in `SBARBASE_DATABASE_TLS_NAMES`. Workers VPC cannot take a private CA, so the VPC service is
  created with certificate verification disabled; the Cloudflare Tunnel is its authenticated,
  encrypted path.
- Without `openssl` on the server the TLS listeners stay off and the loopback listener stays as before.

## Review

An independent review of the first version (2026-10-24, a fresh agent that saw only the code and the
task) found: an unbounded wait after `SSLRequest` (fixed), a cleartext cancel on the TLS-only listener
(fixed), a loopback TLS port that trusted the source port alone (fixed), a success that cleared the
failure count (fixed), open sessions that outlived the flag (fixed), no certificate renewal in a long
process and an unchecked key pair (fixed), and a handler that skipped the management epoch (fixed).
A second review of the repairs found that the in-process renewal changed nothing on Bun (fixed by starting
a new TLS server, now tested by comparing what a client trusting each authority sees), that failed
handshakes left entries in the `pending` map (fixed), that bytes after a cancel were piped (fixed), and
that a guard callback could throw inside a socket handler (now fails closed). No
route to another tenant's database or to the superuser was found.
