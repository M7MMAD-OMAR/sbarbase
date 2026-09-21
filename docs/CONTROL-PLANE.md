# Internal control-plane boundary

The local Catalog adapter is an implementation experiment, not a deployed management service. It persists organizations, memberships, projects, environments and mutation audit events. Application visitors and application JWT identities must never be treated as management actors.

| Role | Read organization projects | Create project/environment | Manage membership | Transfer project metadata |
|---|---|---|---|---|
| Owner | Yes | Yes | Yes, preserve at least one owner | Only if owner of both organizations |
| Admin | Yes | Yes | No | No |
| Viewer | Yes | No | No | No |

This is an initial policy, not a complete product permission model. Organization creation is a trusted bootstrap operation. The new HTTP handler derives actor IDs from its authentication adapter, never request body fields. All mutations use immediate transactions, so authorization checks and changes share one database transaction. Reads derive environment authority from the current project organization, avoiding stale duplicated memberships. Audit events are local records, not a tamper-proof external audit log.

Ownership transfer currently changes metadata only. It preserves project and environment IDs and changes inherited management access immediately. It does not revoke application API keys, end sessions, rotate database credentials, stop jobs or move a database. It must not be exposed as a finished transfer until these steps have a durable, recoverable workflow. Transfers between independently owned organizations will eventually require a destination acceptance process; the current internal operation requires one verified actor with ownership of both.

Tests cover crossed organization access, viewer writes, admin privilege escalation, last-owner removal/demotion, failed transfer authority, membership revocation, identity preservation, uniqueness and persistence after reopening the database. Dedicated management deployment, invitations, server placement and lifecycle execution remain unfinished.


## Initial HTTP integration

`src/control/http.ts` exposes GET/POST handlers for organization projects and project environments. Project creation returns `state: metadata_only`. Environment creation returns 202 with `state: queued`; the separate worker can start local runtime services. See [provisioning](PROVISIONING.md). No organization creation, membership mutation or transfer endpoint is exposed yet. Requests use explicit Bearer tokens, not cookies. JSON bodies allow only a name, with a 4 KiB limit and five-second read deadline. Responses are not cacheable, and internal errors are sanitized.

`src/control/auth.ts` uses Supabase SDK getUser against one configured management endpoint for every request. The endpoint must have a dedicated Auth database and signing keys, separate from all application environments. Invalid credentials and anonymous identities are denied; unavailable verification fails closed. The caller cannot select the endpoint through a header or route. Network requests reject redirects and have a timeout. This design follows [Supabase getUser documentation](https://supabase.com/docs/reference/javascript/auth-getuser), checked 2026-09-20.

Seventeen unit/integration tests pass with 71 assertions. The management tests use a fake Auth transport through the real SDK, not a live dedicated Auth instance. Deployment wiring, management login UI, issuer configuration verification, MFA, request admission limits and live crossed-token checks remain required. Do not claim management authentication is production-ready from these tests.


## Live probe

`bun lab/management-check.ts` now passes 10 checks against real Supabase Auth and a loopback HTTP server. For this test only, a_stage acts as the management realm and a_prod as the independent application realm. Crossed application tokens and tampered tokens are rejected. Authenticated nonmembers and viewers cannot create projects, actor injection fails, and removing membership immediately blocks the same valid token. Allowed creation writes metadata only. The catalog is ephemeral; synthetic Auth users remain in the isolated lab volume. [Sanitized evidence](evidence/management-checks.json).

This supplements the mocked SDK tests above. It does not prove a dedicated management deployment, complete login experience, MFA, production rate limits or session revocation semantics. All owned lab containers were stopped afterward.


## Management-issued environment keys

`controlHandler` combines metadata and key handlers. A ready environment exposes GET `/management/v1/environments/:id/connection`, returning a relative API path for the installation gateway. Owners/admins can GET or POST `/keys` and DELETE `/keys/:keyId`. Viewers can discover connection information but cannot list, issue or revoke keys. A POST accepts no body and issues only a publishable key, returning raw material once. Listings contain hashes neither directly nor indirectly; they return key metadata only. Cross-environment key IDs cannot revoke another environment's keys. Two read routes are answered beside these: `GET /management/v1/environments/:id/mail` returns that environment's non secret mail state from the runtime's own `mail-state.json`, with no credential field at all and the unconfigured state when the environment has no entry, and `GET /management/v1/notifications` returns the undelivered count and the most recent operator events to an owner or admin. Both sit behind the same management identity as the routes above.

Authorization and readiness checks hold a catalog write transaction throughout the synchronous key operation. The key store is a separate local database; this is not a distributed transaction or atomic audit/key store. Membership changes apply to subsequent management requests. Previously issued application keys survive membership removal until explicitly revoked; complete organization transfer is still internal and unfinished.

The managed gateway checks successful provisioning on every request, resolves operator-controlled endpoints and validates current key metadata before proxying. Unknown runtimes and unavailable metadata fail closed. A readiness record does not replace ongoing health monitoring, and internal configuration must be refreshed after container IP changes.

`bun lab/connection-check.ts` passed nine live checks using the existing provisioned environment, a temporary management Auth realm and a loopback server. It exercises management-issued keys with the real Supabase SDK, separate application identity rejection and immediate gateway denial after revocation. Twenty-one unit tests pass with 101 assertions. All lab services were stopped afterward. Secret-key forwarding, automatic route publication in a supervised server, key limits, full audit integration, TLS, browser CORS and production management deployment remain incomplete.
