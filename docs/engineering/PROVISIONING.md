# Durable local provisioning

Environment metadata and its provisioning operation commit in one SQLite transaction. HTTP POST to `/management/v1/projects/:id/environments` returns 202 and queued state. GET `/management/v1/environments/:id/provision` returns authorized state and attempt count without worker claims or credentials. Project creation remains metadata-only.

States: queued, running, succeeded, failed, cancelled. A stable random runtime identifier survives retries. Claims prevent a stale attempt from recording completion. Before execution the worker checks that the requesting actor still has owner/admin authority in the original organization. Revoked authority or changed ownership cancels pending work. Organization transfer is denied while provisioning is running. Revocation after execution begins does not interrupt the in-flight operation; it removes subsequent management access.

## Local worker

Start the owned component lab, then run `/usr/bin/python3 lab/worker.py`. The wrapper holds an inherited OS file lock across the entire Bun worker lifetime. Only that worker may recover running records and claim work. A separate lab operation lock serializes Docker/database mutations. There is no timeout-based lease takeover and no multi-host worker implementation.

`lab/provision.py` persists unique credentials before effects, reconciles the database and grants, updates exact login/database HBA rules, starts original Auth and PostgREST and checks their health. Completion does not imply a gateway route, API key, object storage, Realtime or functions have been configured. Credentials and runtime volumes remain local and ignored. Failed partial operations retain resources for reconciliation, never delete a database automatically. Explicit internal retry is required for failed operations.

Admission is currently a laboratory guard: at most five environments, at least 4 GiB available memory before dynamic provisioning, 512 MiB and 0.5 CPU container ceilings per Auth/REST pair, plus the shared database. This is not a production capacity policy or hard database quota. The current four-environment run has 3072 MiB and 3 CPU aggregate container ceilings. The up command includes enrolled dynamic environments on restart.

## Evidence and limitations

`bun lab/provision-check.ts` first creates an environment through the management HTTP handler, runs external provisioning and deliberately omits the success record. Reopening the catalog and running the worker reconciles the same resources. Seven checks passed, including unchanged database OID and successful login to the previously created account. This simulates the lost-completion boundary, not arbitrary power loss during every disk write. Repeated probe runs reuse the existing environment and provide a narrower verification; preserve the initial evidence separately.

The expanded four-environment matrix passed 97 Auth/REST/database isolation checks and 44 SDK/gateway checks, including the dynamic environment. Twenty unit tests passed with 90 assertions. Full Supabase bootstrap, network/volume disaster recovery, a production installer, automatic worker supervision, request idempotency keys, API retry controls, route publication and multi-host fencing remain unfinished. Existing metadata-only environments are not silently enqueued by the new schema.

The probe uses `.lab/control.sqlite` and a synthetic bootstrap actor. Dedicated management Auth and production authorization remain separate integration work. Do not expose the lab's auto-confirm signup settings or internal provisioning command as production interfaces.
