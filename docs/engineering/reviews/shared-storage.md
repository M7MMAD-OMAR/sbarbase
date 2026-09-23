# Shared original Supabase Storage

Recorded 2026-09-20. The installed Storage v1.73.1 image is pinned by ID/digest in [lockfile](../../lab/storage-image.lock.json). The probe runs one original Storage process for two environments on the pinned Supabase PostgreSQL cluster. It uses a local file backend, not S3 or an external service.

## Implemented and tested

A separate metadata database and login hold Storage tenant configuration. Each environment has a distinct storage login/password and storage schema owner. Those logins can assume canonical API roles but are allowed to connect only to their own database. The installer pre-creates schemas and default grants, sets DB_INSTALL_ROLES=false and leaves migrations to the original service. This avoids replaying its global role installer.

Each tenant registration provides its own database URL, JWT secret, anon JWT and service JWT. The internal admin endpoint requires a separate API key. Tenant selection uses an anchored x-forwarded-host expression. The eventual gateway must set this header from trusted routing and discard the client's version; it is not a safe externally supplied tenant selector.

Seventy-one combined live checks passed: the preceding forty Auth/REST checks plus thirty-one Storage checks. Storage coverage includes authenticated admin registration, private bucket creation, upload/download, same-path files with different bytes in both environments, re-reading after the neighbor upload, cross-environment user/service token rejection, same-environment second-user denial, private-file denial through the public route, schema table ownership and database credential rejection across tenant/metadata/admin databases. [Evidence](../evidence/shared-storage-checks.json).

The complete probe uses six containers: PostgreSQL, two Auth, two REST and one Storage. Aggregate container ceilings are 2560 MiB and 2.5 logical CPUs. These are configured limits, not a benchmark or estimate for ten projects. All temporary containers, network, file data and credential files are removed afterward.

## Operational findings and remaining gates

Tenant registration can return 201 even when its migration attempt fails and is deferred. Provisioning must check migration state and usable APIs before marking Storage ready. The probe checks object metadata existence and performs real bucket/object operations, rather than treating registration alone as readiness.

One shared process holds access to all tenant configuration and can reach all tenant databases. Compromise of that service is therefore a shared failure boundary, even though each database credential is scoped. The management encryption key and metadata database are recovery material. These boundaries are compatible with the current trusted-operator threat model only after further containment and recovery work.

This demonstrates a sharing candidate, not full Storage integration. Still required: gateway-controlled tenant routing, SDK compatibility through the gateway, opaque service-key handling, signed URLs, range/streaming uploads, S3, restart and migration recovery, upgrades, tenant deletion, backup/restore of objects with database state, quotas and noisy-neighbor measurements. The durable worker does not yet provision Storage, and no UI is implemented.

Sources: [pinned configuration](https://github.com/supabase/storage/blob/v1.73.1/src/config.ts), [tenant admin routes](https://github.com/supabase/storage/blob/v1.73.1/src/http/routes/admin/tenants.ts), [role/schema migration](https://github.com/supabase/storage/blob/v1.73.1/migrations/tenant/0002-storage-schema.sql). The actual executed image's compiled source and migrations were also inspected locally; raw copies remain outside version control.


## Gateway integration follow-up

The gateway now accepts optional per-environment Storage configuration: a public Storage upstream URL and an operator-controlled tenant host. It sets x-forwarded-host from that configuration and discards client tenant/routing headers. It never forwards the installation's Storage admin API key. Missing Storage configuration returns an unavailable route. Auth/REST behavior remains available without Storage configuration.

Sixteen additional live checks pass through the real Supabase SDK and loopback gateway: upload, upsert, download of updated bytes, list, removal, attempts to inject another tenant host, crossed environment API keys and denial after key revocation. The combined upstream run now has 87 checks, including the previous 71. [Evidence](../evidence/storage-gateway-checks.json). The helper receives transient credentials through stdin, does not write them and uses an in-memory key store. Owned infrastructure is removed after the probe.

Gateway request bodies remain bounded to 1 MiB total, including multipart overhead, with a ten-second read deadline. Broken or stalled bodies fail without reaching upstream. Twenty-four unit tests pass with 115 assertions, including cancellation resolving a pending read. Large/resumable uploads need a streaming design with admission limits; this implementation must not be marketed as supporting arbitrary uploads.

Public object URLs and signed URLs without an API-key header are not yet supported by this gateway. Browser CORS, opaque service keys, S3 protocol, image transforms, durable Storage provisioning and backup/restore remain open. This follow-up implements and verifies basic gateway integration, superseding only that item in the earlier remaining-work list.


## Public and signed download URLs

The gateway now permits GET/HEAD without an API-key header only for `/object/public/:bucket/:object` and `/object/sign/:bucket/:object?token=...`. Signed reads require one nonempty bounded token parameter; Storage itself verifies its signature, expiry and object binding. Writes, listing, signing requests and authenticated-download paths still require an environment-bound API key. If a client supplies an API key, it must be valid even on public paths. The configured environment must remain enabled, and tenant routing is still supplied by the gateway.

The combined live probe now passes 111 checks: previous checks plus public fixtures and signed/public URL behavior. Tests cover SDK-produced URLs, header-free downloads, private bucket denial through public paths, changed signatures, changed object paths, changed environment paths, actual expiry after a one-second TTL and the independence of an existing signed URL from API-key revocation. [Evidence](../evidence/storage-url-checks.json). Twenty-five unit tests pass with 128 assertions, including exclusion of write and private paths from the exception.

A signed URL is its own temporary access capability. Revoking the issuing API key does not invalidate that URL before expiry. Do not promise that API-key rotation alone removes all previously shared file access. This behavior was verified locally, not inferred from a successful signing response. Public URL access intentionally exposes public bucket objects. Cached copies and emergency signed-link revocation remain operational design work.

The earlier public/signed URL limitation is superseded for ordinary object downloads. Signed upload URLs, image transformations, resumable uploads, browser CORS, durable Storage lifecycle and production deployment remain unfinished. Reference behavior: [bucket visibility](https://supabase.com/docs/guides/storage/buckets/fundamentals), [serving downloads](https://supabase.com/docs/guides/storage/serving/downloads), [SDK signed URLs](https://supabase.com/docs/reference/javascript/file-buckets-createsignedurl).
