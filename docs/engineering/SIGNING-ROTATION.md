# Signing key rotation: who ends the sessions, and what it waits for

Recorded 2026-10-22. Scope: `Runtime.rotate_signing` and the rotating branch of
`Runtime.activate_services` in `lab/durable_runtime.py`, and `revoke_auth_sessions`, which they
call. The reader guide is [signing keys](../guides/signing-keys.md).

## What a rotation does

`rotate_signing` writes a new secret and a `jwt_rotating` mark to the private runtime file, pins the
REST login (bounded, its sessions ended first), recreates Auth and REST with the new secret, updates
the Storage tenant, brings Realtime and Edge Functions back, and then ends every Auth session of the
environment: all refresh tokens revoked, all rows of `auth.sessions` deleted. Without that last
step a refresh token issued before the rotation would get a leaked session a new token signed with
the new key. The mark is cleared only once the sessions are ended. A start that finds the mark
finishes the rotation with the same secret. The `signing` command runs under the installation
operation lock.

## Two faults found in review

**The statement ran as the superuser, and tenant triggers ran with it.** The revocation was
`self.sql(...)`, which is `psql -U supabase_admin`. The developer login is a member of the Auth
login (`Runtime.developer_sql`), so it may add triggers and rules to `auth.sessions` and
`auth.refresh_tokens`, which the Auth login owns. A trigger runs as whoever runs the statement. On a
disposable database with the pinned images, a statement trigger added by the developer login ran
`ALTER ROLE <developer> SUPERUSER` and it succeeded: tenant code became a superuser of the shared
cluster, every environment's database included. `SET ROLE` would not have helped, since the trigger
can `RESET ROLE` back to the superuser, nor would `session_replication_role = replica`, since the
table owner can `ENABLE ALWAYS` a trigger. Confirmed by `lab/signing-revoke-check.py`, which runs
the earlier statement in a transaction it rolls back.

**It waited without a bound under the operation lock.** The statement had no `lock_timeout` or
`statement_timeout`. Tenant code holding the rows (an open `SELECT ... FOR UPDATE` on
`auth.sessions` through the developer login) made it wait for as long as it chose, and with it every
other environment's provisioning, start and toggle, which all need the operation lock.

## The fix

- `revoke_auth_sessions` connects as the environment's own Auth login, over TCP inside the database
  container, with the generated password on stdin (never in an argument). That is the role tenant
  code can already act as, so whatever its triggers do, they do it with rights it already had. A
  `RESET ROLE` in a trigger returns to the Auth login; `SET SESSION AUTHORIZATION` is refused, since
  the session was not opened by a superuser.
- The statement is `SESSIONS_REVOKE`: search path pinned, `REST_PIN_BOUNDS` (5 s for a lock, 30 s
  per statement), and both writes in one transaction.
- A trigger can lift those bounds for the statements after its own: it can `SET statement_timeout =
  0` during the `DELETE`, and a deferred constraint trigger then sleeps at `COMMIT` with no bound.
  The live check measured exactly that. So the client also gives up, after `SESSIONS_REVOKE_SECONDS`
  (45 s), and then ends every backend of the Auth login as the superuser (`pg_terminate_backend`
  fires no tenant code). Ending a killed client's backend also rolls its transaction back.
- A revocation that fails or runs out of time leaves the mark. `rotate_signing` then raises, the
  console shows the rotation as not finished, and the owner asks for it again. On a start the failure
  is printed for that environment and the start goes on: sessions one environment's tenant code
  holds never stop the start of the others. The next start or rotation tries again.

Unit tests: `RotationTests` and `RevokeTests` in `lab/test_signing.py`. Live:
`lab/signing-revoke-check.py`, 13 checks, on the pinned database and Auth images
([evidence](../evidence/signing-revoke-checks.json)): the pinned Auth's migrations leave
`auth.sessions` and `auth.refresh_tokens` owned by the Auth login, the earlier statement escalates,
the new one revokes everything while the trigger is refused, held rows make it give up in 5.1 s, and
a trigger that lifts the bounds and sleeps at commit makes the client give up at 45 s with the
server session ended and nothing committed.

## What remains

- **The operation lock is still held for up to about 50 s** by one environment's tenant code: 45 s
  of client bound plus the clean-up, once per rotation. Bounded, not removed. Moving the revocation
  out of the operation lock would need the mark to be finished by something other than the
  rotation and the start, for example the supervisor's per-environment child; not done.
- **A window between the new Auth and the revocation.** The new Auth serves before the sessions are
  ended. A refresh in that window gets a token signed with the new key; the revocation then deletes
  its session, so Auth refuses it, but REST and Storage accept it until it expires (one hour by
  default). The same window exists when the revocation fails: until it succeeds, refresh tokens
  issued before the rotation still work against the new key. The console shows the rotation as not
  finished for as long.
- **Running out of time ends the Auth service's own connections too.** Every backend of the Auth
  login is ended, the Auth container's pool included; it reconnects on its next request.
- **Tenant code can make the rotation fail every time**: hold the rows, or change the Auth login's
  password (it can, acting as that login). The environment's own sessions then stay alive after a
  rotation its owner asked for, until the code stops. One environment, its own developer's code.
  The platform does not write the Auth login's password back, as it does for the REST login.
- **Other superuser statements on the rotation path were not audited here**: Realtime's migrations
  (`realtime_start` with `migrate=True`, see [REALTIME](REALTIME.md)) and the readiness query in
  `activate_services`, which reads the catalog only and runs no tenant code.
