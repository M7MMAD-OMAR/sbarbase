# Security and operations adversarial review

Reviewed: 2026-09-20. Scope: proposed architecture, not a tested implementation. The working directory contained no implementation or earlier architecture documents when this review began. This review corrects and qualifies earlier conversational recommendations.

## Correct threat model

Sbarbase is downloadable open source software. Each operator runs an independent installation and its own projects. The installation operator, host administrator, and PostgreSQL administrator are trusted. Public application users are untrusted. Project dependencies, uploaded functions, stolen credentials, and routing mistakes remain realistic threats. Public hosting for mutually hostile project owners is an optional future deployment model, not the default requirement.

The baseline can share one PostgreSQL cluster while allocating a database and roles per project environment. It need not impose a VM per project or prohibit the trusted operator's SQL editor. Environment isolation is defense against mistakes and compromised application credentials, not protection from the installation administrator. Organizations need not be a security or billing requirement for a single-owner installation.

## Corrections and required decisions

| Earlier advice or possible inference | Corrected finding and action |
| --- | --- |
| Separate databases make projects fully isolated | They separate ordinary database access and namespaces, but share roles, administration, resources, WAL, and failure scope. Explicitly distinguish data access isolation from availability isolation. [1] |
| Arbitrary SQL must be prohibited | The trusted owner may run administrative SQL. Public application endpoints must not expose arbitrary SQL through an authenticator that can assume privileged roles. Use separate administrative and end-user paths. |
| RLS protects data from the project owner | Owners normally bypass RLS and can alter policies. `FORCE ROW LEVEL SECURITY` is useful defense in depth, not protection from a malicious owner who controls the table. Superusers and BYPASSRLS roles bypass RLS. [2] |
| Every project needs independent auth signing keys | Per-environment keys reduce compromise scope and are a recommended default, not a PostgreSQL requirement. An explicitly designed shared issuer can use strict audience and environment checks, but is a wider trust boundary. Choose and test one contract. |
| Role timeout defaults are hard quotas | `ALTER ROLE SET` supplies session defaults, and many settings are user-changeable. `work_mem` is not a per-project RAM ceiling; temporary-file limits are not complete disk quotas. Pool admission and query cancellation mitigate overload but do not guarantee hostile-workload isolation. [3][4] |
| More databases cause an equal number of idle server processes | Database catalogs do not imply a permanent backend per database. Connections and per-environment services create the major scaling questions. Measure idle pools and subscriptions; do not publish invented project capacity. |
| Per-database PITR is direct | Native physical PITR restores an entire cluster. Recover one environment by restoring a temporary isolated cluster, exporting that database, restoring to a replacement environment database, validating, then switching routing. Never rewind the live shared cluster for one project. [5] |
| Database backup restores the whole platform | SQL dumps omit cluster globals, and database recovery does not restore filesystem configuration or object storage. Maintain a recoverable installation manifest and separate secret/config/object backups. [5][6][7] |
| A replica means HA and zero data loss | Failover needs a decision mechanism and fencing of the old primary. Async replication can lose committed writes during failover; synchronous policies trade availability and latency for durability. HA also requires gateway, storage, keys, and scheduler recovery. [8][9] |
| Rollback means restarting the old image | Major PostgreSQL and application-schema migrations require explicit rollback or forward-repair plans. `pg_upgrade --link` prevents safely reusing the old cluster after starting the new one. New writes cannot be recovered by merely switching back to an old snapshot. [10] |

## Minimum security contract

1. Each environment receives unique database service LOGIN roles. Ordinary end-user roles lack superuser, CREATEROLE, CREATEDB, REPLICATION, BYPASSRLS, and membership in unrelated environment administrative roles. Supabase backend service roles intentionally bypass application RLS and require server-only credentials restricted to their environment's database path. Revoke default PUBLIC database connection privileges and grant access explicitly. Never reuse global role names such as `authenticated` without analyzing shared role membership. Roles belong to the cluster, not individual databases. Shared canonical NOLOGIN API roles and namespaced API roles are different compatibility strategies, neither yet selected; see the feasibility review. [1][11]
2. Owner/migration access and runtime access are separate. Runtime does not own application tables. Administrative credentials never enter public clients or application-function environments. A shared service holding every environment credential remains a trusted component with installation-wide compromise impact.
3. Resolve environment routing from a trusted registry. Validate token signature, issuer, intended audience, expiry, and environment binding before acquiring the environment connection. User-supplied IDs and hostnames select a candidate route, not permission. Platform-console identities and application-user identities are separate trust domains even if backed by the same implementation.
4. Request identity is transaction-local and cleared by transaction completion or rollback. Test cancellation, exceptions, connection reuse, and prepared queries. Do not describe custom PostgreSQL settings carrying JWT claims as tamper-proof: arbitrary SQL under the same role can modify such settings. PgBouncer transaction pooling is incompatible with reliance on session SET/RESET or LISTEN. [12]
5. Treat operator-installed SQL extensions as trusted native or privileged code according to their capabilities. Audit SECURITY DEFINER functions, restrict EXECUTE, and fix search paths to trusted schemas with `pg_temp` last. The blanket claim that PostgreSQL functions must run outside PostgreSQL is wrong: ordinary SQL/PLpgSQL functions are appropriate. User-deployed application code runs separately from the gateway and receives only its environment secrets. [13]
6. A compromised project function must not have the Docker socket, host filesystem, metadata-service access, control-plane credentials, or unrestricted access to sibling internal services. Sandboxing depth depends on trust: containers with least privilege may fit a trusted operator; hostile customer code requires a stronger separately validated isolation model.
7. Storage object authorization, realtime subscriptions, background jobs, logs, caches, and signed URLs must carry environment identity. A database boundary does not automatically protect these paths. Secret revocation behavior and queued-job authorization must be specified rather than inferred from initial request validation.

## Recovery and operations contract

- Define installation RPO/RTO and environment restore RPO/RTO separately. Start with measured restore procedures rather than claiming HA. Off-host encrypted backups must be restorable without the failed host, including access to decryption material.
- Record database versions, extensions, role provisioning, routing manifests, auth configuration, deployed function artifacts, object versions, and secret references in the recovery inventory. A DB timestamp alone cannot provide atomic recovery across external services.
- Quiesce or disable outbound jobs, webhooks, and email during restores. A restored scheduler can replay already completed side effects. Use idempotency records and an explicit replay policy. Decide whether auth recovery invalidates sessions and how object metadata reconciles with retained object versions.
- Logical export of one database does not include roles or tablespaces. Recreate environment roles from a controlled manifest or reviewed globals; do not overwrite all live cluster roles to restore one environment. Treat imported dumps as executable content when their source administrator is untrusted. [6][7]
- Monitor connection occupancy, query age, database growth, disk free space, archive failures, replication lag, and retained WAL. Logical slots can retain WAL; caps can invalidate lagging slots, so define resynchronization behavior rather than promising both bounded disk and indefinite replay. [9][14]
- Separate routine component updates, application-schema migrations, extension upgrades, and PostgreSQL major upgrades. Pin a tested compatibility matrix, run preflight checks, rehearse against restored data, and document maintenance windows. Expand/contract migrations reduce rollback coupling but do not make all migrations reversible. [10]
- If HA is added, test network partition, stale-primary restart, fencing failure, endpoint reconnection, scheduler leadership, and slot readiness. Two containers on one host do not remove host failure. [8]

## Acceptance gates

| Gate | Evidence required |
| --- | --- |
| Data boundary | A stolen runtime credential for environment A cannot connect to, assume roles in, or access data from B. Test APIs, SQL, objects, realtime, and jobs. |
| Identity boundary | Wrong issuer/audience/environment and swapped host/header combinations fail; pool reuse after failures and cancellation never carries another identity. |
| Privilege boundary | Runtime cannot become owner, modify privileged functions, install forbidden extensions, or access installation secrets. Trusted-owner SQL still works as documented. |
| Capacity | Measure zero-traffic and active footprints for the actual service/pool design, and effect of one overloaded environment on another. Publish observed limits and hardware, not extrapolated guarantees. |
| Recovery | Restore installation from off-host backups; restore A to a replacement database while B continues unchanged; reconcile storage and suppress duplicate external jobs. Measure elapsed time and recoverable data. |
| Upgrade | Rehearse a supported upgrade and a deliberate failure. Demonstrate the exact recovery path before and after new writes begin. |
| HA, if offered | Partition/fencing exercise proves only one writable primary; document actual write-loss policy and service recovery coverage. |

## Primary sources

1. [PostgreSQL schemas, database boundaries, and shared roles](https://www.postgresql.org/docs/current/ddl-schemas.html)
2. [PostgreSQL row security and bypass behavior](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)
3. [PostgreSQL resource consumption](https://www.postgresql.org/docs/current/runtime-config-resource.html)
4. [ALTER ROLE session defaults](https://www.postgresql.org/docs/current/sql-alterrole.html)
5. [Continuous archiving and cluster PITR](https://www.postgresql.org/docs/current/continuous-archiving.html)
6. [pg_dump scope and restore trust](https://www.postgresql.org/docs/current/app-pgdump.html)
7. [pg_dumpall global objects](https://www.postgresql.org/docs/current/app-pg-dumpall.html)
8. [PostgreSQL failover and fencing](https://www.postgresql.org/docs/current/warm-standby-failover.html)
9. [PostgreSQL replication configuration](https://www.postgresql.org/docs/current/runtime-config-replication.html)
10. [pg_upgrade compatibility and rollback restrictions](https://www.postgresql.org/docs/current/pgupgrade.html)
11. [PostgreSQL role capabilities](https://www.postgresql.org/docs/current/sql-createrole.html)
12. [PgBouncer feature compatibility](https://www.pgbouncer.org/features.html)
13. [CREATE FUNCTION security guidance](https://www.postgresql.org/docs/current/sql-createfunction.html)
14. [Logical decoding and replication slots](https://www.postgresql.org/docs/current/logicaldecoding-explanation.html)
