# Supabase PostgreSQL distribution probe

Recorded 2026-09-20. Image `public.ecr.aws/supabase/postgres:17.6.1.166`, pinned by image ID and repository digest in [lockfile](../../../lab/distro-image.lock.json). Server reports PostgreSQL 17.6. Original Auth is the already pinned v2.196.0 image. These are installed local images, not a claim to match every version in current upstream Compose.

## Findings that change implementation

1. Image initialization installs canonical roles and Auth/Storage schemas in its initial database. Creating another database from template0 does not copy those schemas.
2. Replaying the image's initial schema SQL in another database fails because roles are cluster-global. The probe wraps replay in a transaction and verifies rollback leaves no partial publication. This is an expected incompatibility finding, not a passed multi-project bootstrap.
3. PostgreSQL image initialization alone leaves an older auth.uid definition that does not read modern JSON claims. The first attempted modern-claim check failed. Inspection located the upgrade in Auth migrations, not the PostgreSQL migrations. Starting original Auth completed its migrations; auth.uid then read JSON claims correctly and auth.jwt existed. Do not replace this pipeline with a hand-written helper and call it full compatibility.
4. Final effective memberships matter more than the first initialization SQL: the image's initial bootstrap grants supabase_admin to authenticator, but the completed migration state removes that membership. The probe verifies the final denial. The complete sanitized role graph is included in evidence.
5. Shared canonical API roles are NOLOGIN. This does not establish isolation for every component or make inherited administrative roles safe for environment service credentials.

Ten checks passed in an ephemeral, network-isolated database/Auth pair. Container ceilings were 1280 MiB and 1.25 logical CPUs. Both owned containers and temporary environment files were removed afterward. No production volumes were attached. [Evidence](../../evidence/distro-checks.json).

## Required next gate

Build a versioned environment bootstrap that separates cluster roles/configuration, per-database schemas/extensions and each service's migrations. Preserve scoped login ownership and defaults rather than granting global service-admin memberships. Run real SDK/RLS/isolation checks against that environment and test an upgrade before replacing the current stock-PostgreSQL lab. Storage, Realtime, cron, Vault encryption material and global event-trigger behavior still require component-specific validation. The independent-PostgreSQL fallback remains open.

The existing stock-PostgreSQL component evidence still uses a minimal auth.uid fixture. This distribution probe supplements it; it does not retroactively certify it as a full Supabase bootstrap.

Sources inspected: [upstream Compose](https://github.com/supabase/supabase/blob/master/docker/docker-compose.yml), [initial role bootstrap](https://github.com/supabase/postgres/blob/develop/migrations/db/init-scripts/00000000000000-initial-schema.sql), [Auth JSON-claim helper migration](https://github.com/supabase/auth/blob/master/migrations/20220224000811_update_auth_functions.up.sql), [Auth JWT helper migration](https://github.com/supabase/auth/blob/master/migrations/20220531120530_add_auth_jwt_function.up.sql). These links may move; the live evidence pins the executed image contents.


## Scoped environment implementation

The follow-up probe `lab/upstream-environments.py` now provisions two environment databases on one initialized Supabase PostgreSQL image. The reusable environment reconciler accepts a scoped SQL executor; it creates unique Auth/REST logins, databases and Auth schema ownership without replaying cluster bootstrap. Each database enables pgcrypto and uuid-ossp under extensions. Exact login/database HBA rules deny neighbor and administrative databases. Shared canonical API roles retain compatibility.

Original Auth performs its own per-database migrations, including auth.uid and auth.jwt. The test never replaces those functions. Forty live checks passed: scoped table ownership, credentials accepted only for intended databases, REST login denied SET ROLE supabase_admin, real signup, original-helper RLS reads/writes, forged-owner denial, second-user isolation, crossed Auth/REST tokens, same-email identity separation, bootstrap retry and Auth restart preserving accounts/database identity. [Evidence](../../evidence/upstream-environment-checks.json).

An initial test failed with an empty JWT role because the new probe omitted GOTRUE_JWT_DEFAULT_GROUP_NAME. The function definition and EXECUTE/schema privileges were correct. Setting the canonical authenticated default resolved the failure. Auth/REST configuration builders are now shared with the existing lab to avoid this configuration drift; the forty checks passed again after that refactor.

This is implemented per-environment Auth/REST initialization on the upstream image, not complete replication of every upstream database feature. Event triggers, Storage, Realtime, cron, Vault configuration, project developer SQL credentials, upgrade paths and SDK integration against this exact image still need verification. The regular durable worker remains on stock PostgreSQL until those gates are sufficient. No existing lab data was migrated. Temporary resources are removed after the probe; aggregate ceilings were 2048 MiB and 2 logical CPUs.
