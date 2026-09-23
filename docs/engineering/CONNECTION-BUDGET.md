# Local PostgreSQL connection budget

The durable runtime now enforces `CONNECTION LIMIT 6` on each environment's Auth, REST and Storage login, and `CONNECTION LIMIT 18` on its database. Management Auth and Storage control each have six-login and six-database limits. Original service pools remain configured at three; extra login slots accommodate listeners, initialization and short overlap. These local constants still need workload calibration.

Before allocating a new environment, provisioning reads the running PostgreSQL `max_connections`, `superuser_reserved_connections` and `reserved_connections`. It requires:

```
18 * proposed_environment_count + 12 shared + 10 operations
    <= max_connections - superuser_reserved_connections - reserved_connections
```

This planning reserve is not an exclusive PostgreSQL reservation for management traffic. The separate native superuser/reserved slots are subtracted rather than assigned to tenants. Missing or invalid measurements fail closed; insufficient planned capacity returns the safe capacity failure. Existing environments reconcile in place without evicting connections.

## Evidence

[Seven live checks](../evidence/connection-limit-checks.json) saturated one actual environment Auth login using concurrent owned sessions, observed PostgreSQL reject an extra login, and verified neighboring environment SQL access and operator access still worked. Probe connections were terminated by a unique application name before the owned runtime stopped. No credentials were printed or placed in command arguments. The fixture did not modify application data.

Four Python budget tests cover default-cluster accounting, reserved slots, invalid measurements and refusal before credential persistence. The complete Python suite now contains 22 passing tests.

## Limits

These are login and database connection controls, not query CPU, memory, lock-duration or disk-I/O isolation. Authenticated app users share service pools; pool acquisition delay and request overload handling need separate tests. Poolers, Realtime and functions must be included before adding them to this budget. Unmanaged logins and existing-session overages are not fully accounted for by the fixed inventory formula.

PostgreSQL documents connection limits as approximate and excludes superusers; prepared transactions and background worker connections are not counted in a role's normal connection limit. See [PostgreSQL 17 CREATE ROLE](https://www.postgresql.org/docs/17/sql-createrole.html) and [connection settings](https://www.postgresql.org/docs/17/runtime-config-connection.html). No absolute noisy-neighbor isolation is claimed.

The Supabase changelog index was checked on 2026-09-20. The runtime retains its pinned PostgreSQL 17 image and original Auth/REST/Storage versions; this change does not upgrade images or introduce Supabase application-schema migrations.
