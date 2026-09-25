# Internal control-plane boundary

The local Catalog adapter is an implementation experiment, not a deployed management service. It persists organizations, memberships, projects, environments and mutation audit events. Application visitors and application JWT identities must never be treated as management actors.

| Role | Read organization projects | Create project/environment | Manage membership | Transfer project metadata |
|---|---|---|---|---|
| Owner | Yes | Yes | Yes, preserve at least one owner | Only if owner of both organizations; revokes the project's API keys |
| Admin | Yes | Yes | No | No |
| Viewer | Yes | No | No | No |

This is an initial policy, not a complete product permission model. Organization creation is a trusted bootstrap operation. The new HTTP handler derives actor IDs from its authentication adapter, never request body fields. All mutations use immediate transactions, so authorization checks and changes share one database transaction. Reads derive environment authority from the current project organization, avoiding stale duplicated memberships. Audit events are local records, not a tamper-proof external audit log.

Ownership transfer preserves project and environment IDs and runtime IDs and changes inherited management access immediately. Since 2026-09-25 it is exposed as `POST /management/v1/projects/{id}/move` (H3 of the [verification plan](plans/2026-09-23-verification-and-migration-plan.md)). In the same catalog transaction it cancels queued provisioning and rewrites `provision_jobs.organization` (H2). The route then revokes every API key of the project's runtimes in the key store, a separate SQLite file: the catalog commits first so a name clash in the destination costs no key, and a revocation failure answers 500 saying the move committed. It does not rotate the environments' JWT signing key or direct database password, end application sessions, or move a database; an operator who must cut every kind of access rotates those separately. Transfers between independently owned organizations still require one verified actor with ownership of both; a destination acceptance process remains future work.

## Rename and delete (2026-09-25)

Owners and admins rename projects and environments; owners rename organizations and delete. Deletes are conservative:

- An organization is deleted only when it has no project; the bootstrap organization never. Memberships and pending invitations go with it; audit rows stay.
- A project is deleted only when it has no environment.
- An environment is refused while its job is queued or running, and while any supervisor-managed state is on or changing: Studio desired running or starting, Realtime, Edge Functions or database access on or pending, a pending sign-in change or signing rotation, routing in maintenance or with a staged placement. Deleting those rows would leave a container or login that nothing reconciles.
- An allowed environment delete writes a `deleted_runtimes` tombstone (runtime, ids, actor, time, and the last attempt's settled effect result), then removes the environment's job, settled effect results, recovery decisions and per-runtime settings rows. The gateway answers 401 `Invalid API key` for a tombstoned runtime, the answer a revoked key gets, and 404 stays for runtimes it never knew. The route also revokes the runtime's keys after the catalog commits; the tombstone already refuses them, so a revocation failure cannot reopen access.
- The runtime itself (database, logins, containers, Storage files, `runtime.json` entry) is retained. Reclaiming it is a separate operator step that does not exist yet, and until then the runtime still counts against the worker's `ENVIRONMENT_LIMIT`, which counts runtime state, not catalog rows.
- The tombstone also keeps the last attempt's settled worker outcome: the effect result (claim, exit code) or the preflight recovery decision (claim, receipt token, decision). `applyProvisionReceipt` and `recoverPreflightReceipt` accept a receipt for a deleted environment only when it matches that record exactly, so a worker that restarts before consuming a settled receipt does not stop; any other receipt still fails closed.
- The audit event `environment.deleted` is recorded against the project, which remains, so the organization's audit view still shows it.

## Re-linking a restored environment (2026-09-25)

`Catalog.relinkEnvironment`, exposed as `POST /management/v1/relink` and `sbarbase relink`, recreates the organization, project and environment a backup's `ownership` records, with their ids and the original runtime id, and queues provisioning so the worker builds an empty runtime of that name for `lab/backup.py restore` to fill. Keeping the runtime id keeps the scoped login names, so the restored database ACL matches the dump. Installation operators only. Matching is by id, never by name: an absent organization id is created with the caller as owner unless another organization already has that name; an existing one requires the caller to own it; a project or environment id that exists elsewhere, an environment name clash, or a runtime id used by another job or tombstoned here all refuse with 409 and change nothing. A repeat after success is a no-op. Memberships are not carried because actor ids belong to the source installation's management Auth realm. H7 of the verification plan.

Tests cover crossed organization access, viewer writes, admin privilege escalation, last-owner removal/demotion, failed transfer authority, membership revocation, identity preservation, uniqueness and persistence after reopening the database. Dedicated management deployment, invitations, server placement and lifecycle execution remain unfinished.


## Initial HTTP integration

`src/control/http.ts` exposes GET/POST handlers for organization projects and project environments. Project creation returns `state: metadata_only`. Environment creation returns 202 with `state: queued`; the separate worker can start local runtime services. See [provisioning](PROVISIONING.md). No organization creation, membership mutation or transfer endpoint is exposed yet. Requests use explicit Bearer tokens, not cookies. JSON bodies allow only a name, with a 4 KiB limit and five-second read deadline. Responses are not cacheable, and internal errors are sanitized.

`src/control/auth.ts` uses Supabase SDK getUser against one configured management endpoint for every request. The endpoint must have a dedicated Auth database and signing keys, separate from all application environments. Invalid credentials and anonymous identities are denied; unavailable verification fails closed. The caller cannot select the endpoint through a header or route. Network requests reject redirects and have a timeout. This design follows [Supabase getUser documentation](https://supabase.com/docs/reference/javascript/auth-getuser), checked 2026-09-20.

Seventeen unit/integration tests pass with 71 assertions. The management tests use a fake Auth transport through the real SDK, not a live dedicated Auth instance. Deployment wiring, management login UI, issuer configuration verification, MFA, request admission limits and live crossed-token checks remain required. Do not claim management authentication is production-ready from these tests.


## Live probe

`bun lab/management-check.ts` now passes 10 checks against real Supabase Auth and a loopback HTTP server. For this test only, a_stage acts as the management realm and a_prod as the independent application realm. Crossed application tokens and tampered tokens are rejected. Authenticated nonmembers and viewers cannot create projects, actor injection fails, and removing membership immediately blocks the same valid token. Allowed creation writes metadata only. The catalog is ephemeral; synthetic Auth users remain in the isolated lab volume. [Sanitized evidence](../evidence/management-checks.json).

This supplements the mocked SDK tests above. It does not prove a dedicated management deployment, complete login experience, MFA, production rate limits or session revocation semantics. All owned lab containers were stopped afterward.


## Management-issued environment keys

`controlHandler` combines metadata and key handlers. A ready environment exposes GET `/management/v1/environments/:id/connection`, returning a relative API path for the installation gateway. Owners/admins can GET or POST `/keys` and DELETE `/keys/:keyId`. Viewers can discover connection information but cannot list, issue or revoke keys. A POST accepts no body and issues only a publishable key, returning raw material once. Listings contain hashes neither directly nor indirectly; they return key metadata only. Cross-environment key IDs cannot revoke another environment's keys. Two read routes are answered beside these: `GET /management/v1/environments/:id/mail` returns that environment's non secret mail state from the runtime's own `mail-state.json`, with no credential field at all and the unconfigured state when the environment has no entry, and `GET /management/v1/notifications` returns the undelivered count and the most recent operator events to an owner or admin. Both sit behind the same management identity as the routes above.

Authorization and readiness checks hold a catalog write transaction throughout the synchronous key operation. The key store is a separate local database; this is not a distributed transaction or atomic audit/key store. Membership changes apply to subsequent management requests. Previously issued application keys survive membership removal until explicitly revoked; complete organization transfer is still internal and unfinished.

The managed gateway checks successful provisioning on every request, resolves operator-controlled endpoints and validates current key metadata before proxying. Unknown runtimes and unavailable metadata fail closed. A readiness record does not replace ongoing health monitoring, and internal configuration must be refreshed after container IP changes.

`bun lab/connection-check.ts` passed nine live checks using the existing provisioned environment, a temporary management Auth realm and a loopback server. It exercises management-issued keys with the real Supabase SDK, separate application identity rejection and immediate gateway denial after revocation. Twenty-one unit tests pass with 101 assertions. All lab services were stopped afterward. Secret-key forwarding, automatic route publication in a supervised server, key limits, full audit integration, TLS, browser CORS and production management deployment remain incomplete.
