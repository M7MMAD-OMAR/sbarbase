# Direct database access and the per-environment console changes

Scope: how a console change of one environment's direct database access, sign-in settings, Realtime,
Edge Functions or signing key is scheduled and locked, so that what a tenant can make wait stays in
that tenant's environment. Written 2026-10-22. Code: `database_command`, `developer_login_sql`,
`database_lock` and the `developer_*` steps of `Runtime` in `lab/durable_runtime.py`;
`schedule_toggles` and `schedule_sign_in` in `lab/dev.py`; `lab/realtime.py` and
`lab/auth_settings.py`. Reader guide: [database access](../guides/database-access.md).

These changes keep the [invariants of the Data API change](DATA-API-SCHEMAS.md#invariants): nothing
a tenant can make wait holds what other environments wait for, no file lock is waited for while
another is held, and races only an operator can cause are listed below.

## What was wrong

- **Turning access on held the operation lock through the grants.** `Runtime.developer_on` ran the
  developer login's role setup and its grants on `public` under the installation's operation lock,
  and under the write pause lock too, with REST serving. A no-op `GRANT` on a table, left open by a
  session acting through the API, made them wait up to 10 s per statement; meanwhile every other
  environment's change and start waited for the operation lock, and every Studio start for the pause
  lock. `developer_sql` (the role, its password and its role settings) had no bound at all.
- **One supervisor queue per kind.** `lab/dev.py` ran one child at a time for each of sign-in,
  Realtime, Edge Functions, database access and signing, for the whole installation, so one
  environment's change that waited kept the same change of every other environment waiting.
- **A lost race was a failure.** `lab/realtime.py` and `lab/auth_settings.py` look at the operation
  lock and then start `durable_runtime.py`, which takes it. A command that lost it in between exited
  1 and its row was recorded as failed.

## Design

**Turning access on** (`database_command`, on) has three steps:

1. Under this environment's database access lock (`database-<e>.lock`, tried) and the operation lock
   (tried): the preconditions, a write pause (none may be recorded), the access rule, the saved
   password, the exposed schema guard and the connection headroom. Nothing is written.
2. Under the environment's lock alone: `developer_prepare` creates the role if it is missing, sets
   its password and role settings, and writes the grants (`developer_grants_sql`), each statement
   within `DEVELOPER_BOUNDS` (10 s for a lock, 60 s per statement). The login itself is left as it
   is. It returns the database clock after the password was written.
3. The environment's lock is let go, the operation lock is waited for, the environment's lock and the
   write pause guard are tried, and `developer_open` checks the headroom again, opens the login
   (`developer_login_sql`), sets the database connection limit, closes the sessions that started
   before the new password and publishes the endpoint.

A pause recorded before step 3, the pause lock held (a Studio start holds it while its own grants run),
or either lock held is `OperationBusy`: nothing is opened, the command exits `BUSY` (76), and the row
stays pending. The supervisor does not ask again while a pause is recorded (`toggle_requests`), and
after `resume` the next try writes the same grants again, which changes nothing.

**Opening or closing the login** changes the login's role row, and a session acting as the login can
hold that row: its own session, Studio (whose login takes the developer role), or a request through
the API running a function the login owns, any of which may change the login's password in a
transaction left open. `developer_login_sql` runs in one transaction: it ends the sessions of the
environment's database that hold a lock on `pg_authid` other than a read, then changes the login,
bounded like the REST login's pins (5 s for a lock, 30 s per statement). A live check against the
pinned image confirms both kinds of holder make a plain bounded `ALTER ROLE` fail, and that
`developer_login_sql` succeeds at once and ends them, when opening and when closing
([evidence](../evidence/developer-login-lock-checks-2026-10-22.json), `lab/developer-login-lock-check.py`).

**Turning access off** stays under the operation lock: it tries the environment's lock and closes the
login with `developer_login_sql`, then ends the login's sessions.

**`write_pause.py resume`** still calls `Runtime.developer_on`, which runs the same steps in one go:
resume holds the operation lock and the pause lock for its whole length, as a start does, and its
grants are bounded. It tries the environment's lock and never waits for it; when a console change of
the same environment is writing its grants, resume stops and is run again, as for any other failure.

**The supervisor** runs one child per environment for sign-in settings (`schedule_sign_in`) and one per
service and environment for Realtime, Edge Functions, database access and signing
(`schedule_toggles`), each asked again 5 s after a busy answer, as `schedule_data_api` does. The
operation lock still makes the changes run one at a time; only what waits outside it (the grants)
now runs beside other environments' changes. `lab/realtime.py` and `lab/auth_settings.py` treat
`BUSY` like their own busy check: 75 to the supervisor, the row left pending. 75 from
`durable_runtime.py` stays the admission limit, which they report.

## Tests

`lab/test_database_access.py`: the role setup and grants run with neither the operation lock nor the
pause lock held, and the login is opened with both; a pause recorded while the grants ran, a held
pause lock and a change of the same environment in progress each make the change ask again without
opening the login; only sessions older than the new password are closed; `developer_login_sql` ends
the holders before it changes the login and is bounded; `developer_sql` is bounded and never opens
the login; the supervisor runs two environments' changes side by side and asks only the busy one
again. `lab/test_realtime.py` and `lab/test_auth_settings.py`: `BUSY` leaves the row pending, and
the capacity refusal keeps its message. Resume still turns access on with the runtime's own
`developer_on` under the pause lock (`lab/test_database_access.py`), and a pause still refuses a console
change (`lab/test_write_pause.py`).

## Limits

- A session that starts holding the login's role row after `developer_login_sql` ended the holders
  and before it changes the row makes the change wait, at most 5 s, under the operation lock; the
  change then fails and can be made again. It needs a tenant to time a role change to that moment.
- Opening the login only tries the write pause lock, so a change that reaches step 3 while a Studio
  start holds that lock is asked again 5 s later, not refused.
- While a pause is recorded, the step 1 check makes a console request ask again; one already past it
  writes its grants during the pause (the login stays as it was) and is asked again after `resume`.
- The checks of step 1 run again in step 3 except the access rule, the password and the guard: a
  password saved between the two steps is applied by the next change, which the console queues.
