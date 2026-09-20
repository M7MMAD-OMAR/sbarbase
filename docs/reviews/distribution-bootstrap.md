# Supabase PostgreSQL distribution probe

Recorded 2026-09-20. Image `public.ecr.aws/supabase/postgres:17.6.1.166`, pinned by image ID and repository digest in [lockfile](../../lab/distro-image.lock.json). Server reports PostgreSQL 17.6. Original Auth is the already pinned v2.196.0 image. These are installed local images, not a claim to match every version in current upstream Compose.

## Findings that change implementation

1. Image initialization installs canonical roles and Auth/Storage schemas in its initial database. Creating another database from template0 does not copy those schemas.
2. Replaying the image's initial schema SQL in another database fails because roles are cluster-global. The probe wraps replay in a transaction and verifies rollback leaves no partial publication. This is an expected incompatibility finding, not a passed multi-project bootstrap.
3. PostgreSQL image initialization alone leaves an older auth.uid definition that does not read modern JSON claims. The first attempted modern-claim check failed. Inspection located the upgrade in Auth migrations, not the PostgreSQL migrations. Starting original Auth completed its migrations; auth.uid then read JSON claims correctly and auth.jwt existed. Do not replace this pipeline with a hand-written helper and call it full compatibility.
4. Final effective memberships matter more than the first initialization SQL: the image's initial bootstrap grants supabase_admin to authenticator, but the completed migration state removes that membership. The probe verifies the final denial. The complete sanitized role graph is included in evidence.
5. Shared canonical API roles are NOLOGIN. This does not establish isolation for every component or make inherited administrative roles safe for environment service credentials.

Ten checks passed in an ephemeral, network-isolated database/Auth pair. Container ceilings were 1280 MiB and 1.25 logical CPUs. Both owned containers and temporary environment files were removed afterward. No production volumes were attached. [Evidence](../evidence/distro-checks.json).

## Required next gate

Build a versioned environment bootstrap that separates cluster roles/configuration, per-database schemas/extensions and each service's migrations. Preserve scoped login ownership and defaults rather than granting global service-admin memberships. Run real SDK/RLS/isolation checks against that environment and test an upgrade before replacing the current stock-PostgreSQL lab. Storage, Realtime, cron, Vault encryption material and global event-trigger behavior still require component-specific validation. The independent-PostgreSQL fallback remains open.

The existing stock-PostgreSQL component evidence still uses a minimal auth.uid fixture. This distribution probe supplements it; it does not retroactively certify it as a full Supabase bootstrap.

Sources inspected: [upstream Compose](https://github.com/supabase/supabase/blob/master/docker/docker-compose.yml), [initial role bootstrap](https://github.com/supabase/postgres/blob/develop/migrations/db/init-scripts/00000000000000-initial-schema.sql), [Auth JSON-claim helper migration](https://github.com/supabase/auth/blob/master/migrations/20220224000811_update_auth_functions.up.sql), [Auth JWT helper migration](https://github.com/supabase/auth/blob/master/migrations/20220531120530_add_auth_jwt_function.up.sql). These links may move; the live evidence pins the executed image contents.
