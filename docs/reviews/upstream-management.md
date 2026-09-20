# Dedicated management Auth and composed local API

Verified 2026-09-20. [28 live checks](../evidence/upstream-management-checks.json).

## Implementation

The durable runtime provisions a separate management database, scoped Auth
login and signing secret. Management Auth is a separate 256 MiB/0.25 CPU process
on the shared PostgreSQL installation. HBA denies application logins access to
management and denies management Auth access to application databases. There
is no management REST or Storage service. Shared PostgreSQL administration
still crosses these boundaries; this is not isolation from a malicious host owner.

Public signup and anonymous signup are disabled in management Auth. The composed
API exposes only password/refresh token exchange, current-user lookup, logout
and settings on its management Auth surface. Other Auth paths, including signup
and admin endpoints, are rejected before forwarding. Management responses use
no-store caching. The existing body size/deadline and upstream timeout controls
apply. MFA, invitations, recovery, OAuth and browser edge controls remain pending.

The SDK-facing management publishable key is public routing configuration, not
an authorization secret. Every control operation verifies the user's token
against the fixed management Auth server and checks current catalog membership.
Application JWTs cannot supply a management identity. API keys remain scoped
to their environment and persisted as hashes. Connection discovery now reports
Storage when the trusted runtime route actually provides it.

`src/control/application.ts` composes management Auth, metadata/key APIs and the
managed application gateway. `lab/upstream-server.ts` exposes this composition
on loopback with an OS-assigned free port. It prints no credentials and never
publishes a remote listener. It is an experimental API entry point, not a
production process supervisor or finished dashboard.

## Verified behavior

- Private operator-created management identity can log in through the SDK.
- Public registration fails both at the composed API and at original Auth.
- Management user discovers Auth/REST/Storage, issues and lists publishable keys
  without recovering raw key material from list responses.
- Issued key supports original application Auth, RLS reads and private Storage
  upload/download. Same email remains a different application identity.
- Application JWT cannot mint management keys; an environment key cannot access
  another environment. Current viewer membership blocks minting.
- Management identity and issued key survive runtime/server restart. Revocation
  blocks authenticated gateway requests to all three services and survives reopen.
- Direct database login checks reject crossed management/application credentials.

Revoking a key does not invalidate previously signed object URLs or make public
objects private. Those download paths retain the separately verified semantics.

The standalone server command was also started and queried: settings succeeded,
Auth admin route returned 404, SIGTERM shut down the owned child and removed its
server descriptor. Twenty-seven TypeScript unit tests pass (141 assertions),
including Auth route allowlisting and Storage discovery. Six Python tests pass.

## Remaining gates

Local operator bootstrap has since been implemented and tested in
[operator setup](../OPERATOR-SETUP.md). Onboarding UI, invitations and a finished
management UI are not implemented. The probe creates a temporary private test identity and removes it.
No user account or password was delivered as a production login. Key mutation
audit atomicity, quotas, login rate controls, comprehensive drift checks, TLS,
CORS, recovery and version upgrades remain unfinished. Stop the runtime between
experiments; with two environments the container ceilings are now 2816 MiB and
2.75 CPUs, with a four-environment guard at 3840 MiB and 3.75 CPUs.

Sources: [Supabase admin createUser](https://supabase.com/docs/reference/javascript/auth-admin-createuser),
[authoritative getUser](https://supabase.com/docs/reference/javascript/auth-getuser),
and the pinned GoTrue v2.196.0 runtime verified in this experiment.
