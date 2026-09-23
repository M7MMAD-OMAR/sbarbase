# Receipt-bound native SQL provisioning

The durable worker provisioning path now uses GuardedSQL for its complete native environment database stage. Routine published resume remains separate. This is runtime wiring, not automatic recovery of partial provisioning.

## Authorization and ordering

After admission checks and before credential reservation, sql_identity requires an owned pending durable-worker receipt, exact preflight stage record and the current running catalog claim. Runtime, UUID token, UUID claim and integer attempt must match. Missing or malformed identity, an old catalog attempt, or a direct invocation without a receipt cannot reach native provisioning SQL.

The database write-ahead marker precedes registry creation. Authorization is checked again against that exact stage. All native per-environment SQL goes through the pinned scoped executor, including target registration, schema permissions, extensions, Storage setup and service limits. Quiet fresh psql sessions preserve query results.

After native SQL succeeds, close revokes control and target authority before the services marker and HBA write. Close compares captured control cluster/OID and target OID before revocation, and rechecks the captured target after control work drains. A missing/replaced target, failed acknowledgment or other interruption poisons the executor and prevents advancement to services. A durable completion witness still requires the later service, Storage and publication phases.

No operator recovery API or automatic replay is enabled. SQL authority retirement does not fence Docker HBA writes, Auth migrations or Storage tenant operations. The legacy durable-check probe now uses the worker protocol instead of manually claiming a job and bypassing receipts.

## Evidence and limits

The disposable upstream probe exercises actual Runtime.provision with real temporary receipt, stage, catalog and PostgreSQL state. It verifies both tokens are revoked at the services boundary. Host lease validation and host admission are simulated in that disposable wiring fixture; HBA and services are deliberately not run there. Separate tests validate catalog/receipt mismatches, direct invocation refusal, interruption poisoning and failure before HBA.

Validation: 118 Python tests and 51 disposable upstream checks pass. The retained supervisor rehearsal passes 13 checks, with four working environment routes and all owned runtimes stopped afterward. No new retained environment was allocated and no pending receipt remains. Independent review found no remaining must-fix in this scope.

The retained supervisor rehearsal exercises actual worker ownership and admission refusal without allocating another environment. The separate [fresh lifecycle fixture](FRESH-WORKER-LIFECYCLE.md) now verifies a complete worker-driven Auth/REST/Storage cycle with the guard. Stage-specific recovery and service-effect fencing remain open.
