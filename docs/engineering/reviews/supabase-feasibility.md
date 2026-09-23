# Supabase feasibility audit

Reviewed on 2026-09-20. Scope: downloadable open source software operated by one installation owner for that owner's projects. Supabase remains essential. This is not an assumption that the owner sells hostile public hosting.

## Recommendation

Build orchestration around maintained Supabase components. Share the gateway, management interface, observability and backup tooling. Studio is one of those components: it is adopted per environment, pinned and integration tested like Auth and REST, and its resource cost belongs in the capacity work. Evaluate a shared PostgreSQL cluster with one database per environment, while retaining Auth and PostgREST processes per environment. This satisfies avoiding a complete repeated stack; it does not promise constant resource cost regardless of environment count. Do not rewrite authentication merely to eliminate a few processes. Measure idle memory, connections and cold starts before selecting lifecycle policy.

Keep a second deployment profile using an independent PostgreSQL cluster per environment. It costs additional resources but preserves independent canonical roles, permits stronger operational separation and provides a fallback when shared-cluster compatibility fails. Neither profile on one physical host provides independent host failure domains.

## Corrections to previous reasoning

1. Official self-hosted Supabase is a single project. That is a distribution limitation, not proof that all components lack tenant support or that Supabase failed to find a hierarchy. No evidence about the company's motives was established. [Official self-hosting](https://supabase.com/docs/guides/self-hosting)
2. A lightweight Auth and PostgREST pair per environment is different from copying the complete Supabase stack. The owner has not required zero per-environment processes.
3. Supabase Auth runtime migrations must not be confused with the Supabase PostgreSQL image bootstrap. The latter creates canonical cluster roles. The former is substantially more reusable.
4. Separate databases do not create separate PostgreSQL role namespaces. Roles are cluster-global. [PostgreSQL roles](https://www.postgresql.org/docs/current/database-roles.html)

## Direct source inspection

Auth master resolved through `git ls-remote` to `2e9ce6c8e46532879ced1c6f9a7acdcde3815ea6`. The audit downloaded that exact commit archive into memory and scanned all 76 SQL migration files for whole-word `anon`, `authenticated`, `service_role`, `supabase_auth_admin`, and `CREATE/ALTER ROLE/USER`. There were no executable matching statements; one comment matched `alter user`. This is static evidence, not a successful migration test. [Pinned migrations](https://github.com/supabase/auth/tree/2e9ce6c8e46532879ced1c6f9a7acdcde3815ea6/migrations)

Auth configuration exposes the database URL and namespace, plus JWT admin roles and default group. A unique login such as `env_a_auth` therefore appears feasible without an Auth fork, provided it owns the correct schema objects and receives required privileges. This must be tested on fresh and upgraded databases. [Pinned configuration](https://github.com/supabase/auth/blob/2e9ce6c8e46532879ced1c6f9a7acdcde3815ea6/internal/conf/configuration.go)

By contrast, Supabase PostgreSQL bootstrap creates `anon`, `authenticated`, `service_role`, `authenticator`, and `supabase_auth_admin`, and assigns schema ownership and grants. Replaying the raw bootstrap per database is incorrect because roles already exist globally. Bootstrap includes privileged grants that later migrations may alter, so auditing one historical bootstrap file is not sufficient to describe final runtime privileges. Provisioning must distinguish cluster initialization, environment database initialization, service migrations and application migrations. [Initial roles](https://github.com/supabase/postgres/blob/develop/migrations/db/init-scripts/00000000000000-initial-schema.sql), [Auth bootstrap](https://github.com/supabase/postgres/blob/develop/migrations/db/init-scripts/00000000000001-auth-schema.sql)

## Canonical roles versus namespaced roles

### Option A: shared canonical NOLOGIN API roles

Create `anon`, `authenticated` and `service_role` once per cluster. Preserve JWT role strings and application SQL such as `TO authenticated`. Create unique login roles for every environment's Auth, REST, Storage and migration services. Each login connects only to its assigned database. Never share the default `authenticator` password or service database credentials across environments.

This is a plausible compatibility-oriented profile for one trusted installation owner. It has shared role identity: `ALTER ROLE authenticated` affects the cluster, and membership in common API roles is not environment-local. Environment migrations must not manage global API roles. Do not grant environment administrators CREATEROLE or superuser privileges.

Enforce exact login/database pairs in `pg_hba.conf`, revoke PUBLIC CONNECT on environment databases and grant only intended logins, test pooler authentication separately, and prevent project workloads from obtaining alternate database credentials. NOLOGIN prevents logging in directly as the shared API role, but does not erase privileges granted to it. The complete effective membership graph must be audited. [HBA](https://www.postgresql.org/docs/current/auth-pg-hba-conf.html), [Privileges](https://www.postgresql.org/docs/current/ddl-priv.html)

This profile must not be advertised as independent database-role isolation between environments. It protects normal project data paths through database boundaries, scoped login identities and verified routing. A compromised cluster administrator can reach every database.

### Option B: namespaced API roles

Use `env_a_anon`, `env_a_authenticated`, `env_a_service` and equivalents. PostgREST supports selecting an anonymous role and extracting the role from JWT claims. Auth allows configurable JWT role values. However, simply changing those values breaks familiar SQL, including `TO authenticated`, checks of `current_user`, and potentially `auth.role()` comparisons or other code that expects the JWT role `authenticated`. [PostgREST authentication](https://docs.postgrest.org/en/stable/references/auth.html)

Keeping JWT `role=authenticated` while making PostgREST select a separate namespaced claim can preserve that JWT convention, but SQL policies still require a deliberate mapping strategy. A gateway must not trust a client-supplied namespaced role. Adding common group memberships partly restores compatibility while reintroducing shared role identities. Therefore namespaced roles are not a free configuration change with full SQL compatibility. This option needs a documented compatibility contract and fixtures, not a promise of transparent imports.

### Option C: independent PostgreSQL clusters

Canonical API and admin roles exist independently in each cluster. This is the clearest option when unmodified SQL imports and independent role administration outweigh memory savings. Shared control services can remain outside those clusters. Isolation still depends on network credentials, JWT keys, storage routing and runtime secrets.

## Service reuse

Auth's inherited `GOTRUE_MULTI_INSTANCE_MODE` remains explicitly unsupported. PostgREST uses one non-reloadable `db-uri`. Per-environment processes avoid depending on unsupported request-time database switching. [Auth README](https://github.com/supabase/auth#inherited-features), [PostgREST configuration](https://docs.postgrest.org/en/stable/references/configuration.html#db-uri)

Realtime is explicitly multi-tenant and talks to its own metadata database and individual tenant databases. Its tenant connections and replication workers still consume resources. Supavisor supplies dynamic tenant pools. These are candidates for sharing, with pinned-version integration tests. [Realtime architecture](https://github.com/supabase/realtime/blob/master/ARCHITECTURE.md), [Supavisor architecture](https://github.com/supabase/supavisor#architecture)

Storage has explicit `MULTI_TENANT` configuration, a tenant metadata database, idle pool release settings and configurable API role names. This disproves any blanket claim that it is inherently single-tenant. The inspected role-name settings are process configuration; the audit did not establish per-tenant role-name overrides. A shared Storage service therefore requires deeper verification when each environment uses different SQL role names. [Storage configuration](https://github.com/supabase/storage/blob/master/src/config.ts)

pg_cron is installed in one database per cluster and can schedule in other databases. User code execution needs scoped credentials and resource limits even when all projects belong to one owner: a vulnerable application can still be exploited by outside users. [pg_cron](https://github.com/citusdata/pg_cron#setting-up-pg_cron), [Edge Runtime](https://github.com/supabase/edge-runtime#architecture)

Studio and postgres-meta are the administration surface for an environment and follow the same rule as every other service: pinned versions, per-environment instances, and integration plus isolation tests before anything is shared. The self-hosted Studio is single project by construction, one database and one credential pair per process, so per-environment instances are the only shape that does not require forking it. The connection string Studio hands to postgres-meta is encrypted with a key that both containers hold, which makes that key an authorization boundary and argues for one per environment. [Integration specification](../STUDIO-INTEGRATION.md).

## Version and upgrade consequences

Current upstream Docker master uses Envoy and PostgreSQL 17, and pins Auth and PostgREST versions that differ from their newest standalone releases. Do not mix stable documentation or component master behavior with the assumption that the same feature exists in a shipped image. The installer needs a tested component bill of materials, image digests, an upgrade order and rollback/restore procedures. Studio and postgres-meta join that bill of materials when they are adopted, one component at a time. [Compose source](https://github.com/supabase/supabase/blob/master/docker/docker-compose.yml)

Relevant recent breaking changes include the gateway transition and `API_EXTERNAL_URL` gaining `/auth/v1`. Callback routes and issuer validation must use the chosen release's convention. [Gateway change](https://supabase.com/changelog/48048-self-hosted-supabase-envoy-becomes-the-default-api-gateway-b), [Auth URL change](https://supabase.com/changelog/47093-self-hosted-supabase-api-external-url-to-include-auth-v1)

## Evidence required before selecting the shared-cluster profile

- Two databases, separate service logins and JWT keys, successful signup/refresh/logout and REST operations using the real Supabase SDK.
- Failed login from environment A's credentials to B, including pooler routes, direct connections and any enabled FDW/dblink path.
- Auth migration of two databases with unique login owners, then a real version upgrade without global grant or password changes.
- Existing SQL fixtures using `TO authenticated`, `auth.uid()`, `auth.jwt()`, `auth.role()`, triggers and SECURITY DEFINER routines.
- Runtime roles never inheriting administrative roles. Service-role access intentionally bypasses RLS only within its scoped database connection path.
- Storage metadata/object path isolation, signed URLs, Realtime subscriptions, and function secret routing.
- Idle and loaded resource measurements plus restore of one environment without overwriting its neighbor.

No database, service or container was deployed by this audit. GitHub tree API retrieval was rate-limited; the exact Auth archive was successfully retrieved through codeload instead. Remaining claims are design recommendations and static-source observations, not production validation.
