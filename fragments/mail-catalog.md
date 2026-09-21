# Fragment: `src/control/catalog.ts` mail table (not applied here)

> APPLIED on 2026-09-21 by the parent: the table is in `src/control/catalog.ts`
> between `runtime_routing` and `audit_events`. Kept as the record of what was
> applied. The read route and the console surface it defers are still open.

`src/control/catalog.ts` is owned by another agent running in parallel, so this
fragment carries the exact change instead of applying it.

Source: `docs/ENVIRONMENT-EMAIL.md` section 5.3. Acceptance criteria: the state
each environment's mail is in must be answerable from the catalog without a
credential ever entering a row (redteam B.5 question 1, and the rule that a
secret is protected by not being in the plane, B.4).

## The change

Inside the constructor's single `this.db.exec(...)` template, beside the existing
`CREATE TABLE IF NOT EXISTS` statements, after `runtime_routing` and before
`audit_events`:

```sql
      CREATE TABLE IF NOT EXISTS environment_mail(
        environment TEXT PRIMARY KEY REFERENCES environments(id),
        enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
        host TEXT, port INTEGER, from_address TEXT, reply_to TEXT, sender_name TEXT,
        autoconfirm INTEGER, secure_email_change INTEGER, otp_exp INTEGER,
        rate_limit_email_sent TEXT, rate_limit_otp INTEGER,
        credentials_set INTEGER NOT NULL CHECK(credentials_set IN (0,1)),
        state TEXT NOT NULL CHECK(state IN ('unconfigured','applied','failed','off')),
        detail TEXT, updated_at INTEGER NOT NULL, updated_by TEXT NOT NULL);
```

## Why it looks like this

- **No password column and no user column, by construction.** The password never
  crosses the management API (design section 2.1 rule 5 and section 9 item 1),
  so there is nothing to store. `credentials_set` is derived from the mail
  configuration file's presence, not from its content.
- **The state vocabulary is the four states the runtime already names**, and the
  same four that `lab/mail_config.py` defines as `STATES`. A `failed` row must
  carry a classified reason in `detail`, one of `invalid_configuration`,
  `smtp_unreachable`, `smtp_rejected`, `smtp_tls`; a transport error string is
  never written here (design section 7).
- **`references environments(id)`**, so a mail row cannot outlive its
  environment. `PRAGMA foreign_keys=ON` is already set in the constructor.
- `state` is `NOT NULL` with a CHECK, so an unknown state fails at write time
  rather than rendering as blank in the console.
- No new index is needed: the primary key is the only lookup this table serves,
  and the table is one row per environment.

## Who writes it

The reconcile operation, and only the reconcile operation. The runtime is Python
and the catalog is SQLite owned by the Bun control plane, so the runtime does not
open `control.sqlite`: it writes the non-secret summary file described in
`fragments/mail-durable-runtime.md` Hunk 4, and the control plane copies it into
this table inside the same transaction that records the audit event.

## Deliberately not in this fragment

- `GET /management/v1/environments/<uuid>/mail` in `src/control/http.ts`
  (design step 8) and the `Email` section in `ui/Connection.tsx` (step 9) are
  separate increments. This task builds neither.
- No write route, no console test message, no template columns.
- No column is added to `organizations`, `projects` or `environments`: the switch
  stays per environment, and an installation wide mail key would let one
  environment's abuse spend another environment's provider quota (design section
  2.1 rule 1).