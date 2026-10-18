# Management account MFA

Scope: SB-06, original management Auth `v2.196.0` from `lab/images.lock.json`, SDK `2.116.0`. Application Auth realms remain separate. This is development security implementation and scoped evidence, not production acceptance.

MFA is required by default for new and existing installations. The current installation owner can choose optional MFA from **Account security**, after a native TOTP verification no more than five minutes old. This change ends all management and Supabase dashboard sessions. Accounts with any verified factor must still verify MFA. Accounts without verified factors can sign in with their password when the installation policy is optional.

Account security supports adding and verifying authenticators. Required mode keeps at least one verified TOTP factor. Optional mode permits a verified account to remove its last factor, revokes its existing grants, and requires a new password sign-in. Setup secrets and codes remain transient and never enter the catalog or audit.

## Server contract

The management proxy exposes local `GET/PATCH /management/auth/v1/policy` plus native password and refresh grants, `GET /user`, logout, settings, `POST /factors`, `POST /factors/{id}/challenge`, `POST /factors/{id}/verify`, and `DELETE /factors/{id}`. It refuses signup, OAuth, magic-link, recovery-link, arbitrary Auth admin paths, unexpected methods, query parameters and oversized bodies. Auth validates every identity token against the fixed management realm with redirects disabled and a five second deadline. User metadata cannot grant authority.

Protected `/management/` requests require native validated password AMR and a real session id. MFA mode additionally requires native `aal2`, a currently verified matching TOTP factor and a persisted grant recorded after successful verification through the proxy. Optional password mode requires `aal1`, no verified factors of any type and a durable `management_password_grant` issued only after a successful proxy password login and native identity lookup. Each grant binds actor, session, original password timestamp, actor epoch, policy epoch and configurable password-age expiry (twelve hours by default). Token expiry is checked separately. Refresh does not mint a grant or extend password freshness. A direct private Auth token or a revoked token cannot replace a grant.

The installation policy lives in `management_auth_policy`, defaults to required and survives restart. Policy mutation is owner-only, accepts one boolean, uses the actor security lease and a fresh native factor lookup, audits in the same catalog transaction and advances all known actor epochs. Policy generation also prevents a required/optional toggle from restoring earlier authority. Current memberships remain required for every operation.

Lifecycle material explicitly records `authorization: mfa` with its exact factor, or `authorization: password` with a null factor. Both bind the original bearer, native actor/session/AMR/expiry, actor epoch, policy epoch, catalog identity and operation. TS and installed Python effect workers repeat these checks. Password authority refuses when enrollment appears in current native factors. Previously captured lifecycle material without an explicit mode and policy generation fails closed after upgrade; renew authorization through the console before resuming a pending operation. Transfer history enforces the same catalog policy and grant. Studio retains the epoch invalidation contract, so policy changes and logout invalidate its tickets and cookies.

The authenticated HTTP request carries its current local grant check across asynchronous reads. Catalog authorization boundaries recheck that grant, and a revoked request's final response is refused. A pending streamed body cannot resume an administrative mutation after local MFA revocation. Private workers and host catalog calls keep their existing trusted entry points; the HTTP scope does not authorize private entry points.

Grant revocation is persisted per actor. Removing a verified factor revokes every management grant before and after the native request, including failures, so old tokens and concurrent sessions cannot retain management access. Required policy prevents removing the last verified authenticator; optional policy permits removal only from a currently verified MFA session. Native Auth can retain an old JWT's `aal2` claim until refresh, which is why the catalog grant and current factor checks are required in addition to the JWT claim. Recovery never issues an application token or a management grant.

Native factor mutations share a sixty second actor lease across controllers. The factor list is reread under the lease before removal, preventing simultaneous removals from both treating the other factor as a spare. Revocation also records a Unix second watermark: a verification at or before that timestamp cannot create a new grant. Wait for a new code and verify again if a concurrent removal or recovery produces `mfa_retry`. This prevents an older native token with the same second-resolution AMR from regaining access.

Proxy logout at any native scope also revokes all local management grants and advances the actor epoch before and after the upstream attempt. Other management sessions must sign in again or verify MFA as required by their account and installation policy, including when only a single native session was signed out. The durable `catalog.managementSecurity.epoch(actor)` is the invalidation contract for consumers such as signed Studio sessions. Direct edits or logout through a private native endpoint outside this proxy do not advance the local epoch. Native identity checks detect them on the next management request; dependent sessions must declare or independently enforce their private-admin invalidation boundary.

## Remembered sign-in

The console keeps the management session in the browser's `localStorage` under the key `sbarbase.management.session`, so a reload, a new tab or a restarted browser does not ask for the password or a code again while the server still accepts the session. If the browser refuses storage the console falls back to memory and the sign-in lasts until the page closes. The console asks the server for the current installation policy and grant before opening. An optional password session and a verified MFA session each retain their own grant requirements. Missing or revoked authority returns to verification or sign-in.

How long a sign-in lasts is the password age maximum from the section above. It is one value, `SBARBASE_MANAGEMENT_SESSION_SECONDS`, used by every check in the control plane (`src/control/management-session.ts`) and by the lifecycle authority (`lab/management_access.py`, through the helper `lab/management_session.py`). The default is 2592000 (thirty days), so an owner who signs in once stays signed in for a month; set a lower value for a shorter session. Accepted values are whole seconds from 3600 to 2592000 (thirty days); anything else, including an empty value, means the default. The service reads the value from its environment when it starts, so a change needs the service or container restarted. After the restart, lowering the value cuts existing sessions on their next request, because every check uses the current value. Raising it applies to grants issued after the restart, because a grant keeps the expiry it was issued with. The console does not drop a stored session by itself at the maximum: the first management request after it is refused and the console asks for sign-in or verification. Set it for the systemd service with a drop-in (`Environment=SBARBASE_MANAGEMENT_SESSION_SECONDS=43200` for twelve hours), or in the environment of `deploy/compose.sh`, which forwards it to the container.

Trade-offs to know before raising it:

- The stored session, including its refresh token, is readable by any script that runs on the console's origin. The console serves a strict content security policy and the key is namespaced, which lowers the risk without removing it.
- Every logout scope, including the console's own local sign out, revokes all of the account's management grants and advances its epoch. A copied token cannot reach management APIs after a sign out without a fresh password sign-in or authenticator verification required by the current policy. Sign out also blanks the stored session; a browser that refuses both removal and overwrite cannot be cleaned by page code.
- The cap counts from the password timestamp that native Auth keeps in the token across refreshes. This is relied on. The real Auth fixture (`lab/management-mfa-check.ts`) shows that a refreshed token keeps its grant, but no check compares the password timestamp before and after a refresh over hours; that will be checked on a live server after deployment.

## Rate admission

Admission runs in SQLite immediate transactions shared by all controller processes using the installation catalog. Counts survive restart. Caller-supplied address headers are ignored. Password account keys are SHA-256 hashes of normalized emails, and authentication attempts also share global budgets. Counts include successful attempts, so changing passwords, factor IDs, sessions or client headers cannot reset the relevant actor budget. A storage failure returns 503, not an unbounded Auth request.

| Operation | Server budget |
| --- | --- |
| All Auth requests | 600 per minute |
| Password grants | 60 per minute globally, 10 per account per ten minutes |
| Refresh grants | 120 per minute |
| Authenticated Auth requests | 120 per actor per minute |
| Enrollment | 5 per actor per hour |
| Challenge | 30 per actor per ten minutes |
| Verify | 10 per actor per ten minutes |
| Unenroll | 5 per actor per hour |
| Protected management requests | 1200 globally and 120 per actor per minute |

Global limits protect admission without a trusted client address input, but an attacker can consume a shared budget and temporarily deny legitimate sign-ins. A deployment can add a trusted edge limit. It must not expose private Auth upstream ports or trust public forwarding headers to bypass this server budget. These defaults have functional evidence, not a capacity or denial-of-service guarantee.

## Restricted factor recovery

If one device is lost, sign in with a verified spare, add and verify a replacement, then remove the lost factor. If every device is lost, a different current installation owner with a live verified management session must run the private host helper, after confirming the account holder through the installation's identity verification procedure. The target must prove their current password. Neither a tenant owner nor an installation admin can authorize this helper. Self-recovery through this helper is refused.

Run `bun lab/management-factor-recovery.ts` from a private terminal and supply one JSON object on stdin. Do not put credentials in arguments, commit them, or save the object in shell history. Its shape is:

```json
{
  "catalog": "<installation catalog path>",
  "realm": {
    "auth": "<private management Auth root URL>",
    "publishableKey": "<management public key>",
    "serviceToken": "<private management service-role token>"
  },
  "recovery": {
    "target": "<native management user UUID>",
    "factor": "<native factor UUID>",
    "reason": "lost_factor",
    "operatorToken": "<different installation owner's current MFA token>",
    "targetPassword": "<target account holder's current password>"
  }
}
```

The only accepted reasons are `lost_factor` and `compromised_factor`. The helper validates the operator through native Auth plus the persisted MFA grant, checks current installation ownership, durably revokes all target grants, verifies the target password through original Auth, signs out the target globally through original Auth, then deletes only the selected native factor through the private native admin API. Requested, completed and failed outcomes are audited with identifiers and reason enums, never secrets. Failure retains grant revocation. Success requires the target to log in again; enrollment and verification depend on the current installation policy and remaining factors. A new password session alone accesses management only under optional policy with no remaining verified factor.

Before each subsequent native action, the helper rechecks the operator's live native session, local grant, current owner membership and unexpired target lease. A one-use durable recovery receipt records the terminal outcome even if the initiating owner is demoted during an await. A crash leaves a requested intent visible with target grants already revoked. An operator must inspect native factor/session state before resolving such an interrupted operation; automatic crash reconciliation is not claimed by this helper.

There is no public factor-reset endpoint, recovery code, hidden AAL bypass, or replacement TOTP validator. If the only installation owner loses every factor, or the target also loses their password, this helper cannot recover the account. Host-level identity recovery policy and separately reviewed tooling remain required; do not delete database MFA rows or fabricate a grant to bypass the refusal. Prepare at least two independent verified authenticators and a second installation owner before relying on this workflow.

## Reproducible native check

Install the public pinned dependencies with `bun install --frozen-lockfile` and `bun install --cwd lab/mfa-browser --frozen-lockfile`, then build the console with `bun run build:ui`. The browser fixture declares Apache-2.0 licensed Playwright `1.62.1` in its own package and lockfile. Requirements are Docker Engine on Linux, a running user systemd manager for bounded driver execution, Python 3, Chromium (discoverable as `google-chrome`, `chromium` or `chromium-browser`, or supplied through `CHROMIUM_BIN`), and the two public images recorded in `lab/images.lock.json`. The fixture refuses missing pinned images instead of silently changing versions. Obtain the recorded digests from their public registries before running.

```sh
python3 lab/management-mfa-fixture.py --output /tmp/management-mfa-evidence.json
```

This creates only fresh UUID-labeled PostgreSQL and Auth containers on an internal network, without host port publishing. PostgreSQL is limited to 512 MiB and 0.5 CPU, Auth to 256 MiB and 0.25 CPU, and the host test driver to 256 MiB and 0.25 CPU. DB storage is a disposable tmpfs. The test uses original Auth enrollment, challenge, invalid code, verification, refresh, multiple sessions, original native factor deletion and signout. It also exercises tenant isolation, current member revocation, server rate denial, and controller recreation. Its local authenticator computes test codes from an Auth-issued secret, and never replaces native Auth validation. Cleanup checks ownership labels and removes only exact resources created by the run. A failed or unavailable fixture is recorded as failure.

The same bounded driver opens the built console in a fresh headless Chromium profile. It performs real password login, native enrollment, accessible invalid-code feedback, successful verification, account security, last-factor protection, signout and a subsequent native challenge using keyboard submission. The browser can reach only its generated loopback web origin. Enrollment QR and manual secret are masked in exported screenshots. The screenshots are inspection artifacts, not a claim of visual parity with an upstream product design.

See the SB-06 evidence record for observed outcomes and limitations. Unit tests establish boundary behavior only. They do not establish actual Auth MFA, browser usability, container portability or production readiness.

## Original contracts reviewed

The [Supabase MFA guide](https://supabase.com/docs/guides/auth/auth-mfa) defines enrollment, challenge, verification and `aal2` enforcement. The [SDK enrollment contract](https://supabase.com/docs/reference/javascript/auth-mfa-enroll) is used directly. The fixed [Auth v2.196.0 MFA implementation](https://github.com/supabase/auth/blob/v2.196.0/internal/api/mfa.go) owns native factor and session state. The fixed [Auth identity middleware](https://github.com/supabase/auth/blob/v2.196.0/internal/api/auth.go) checks session existence for user identity. The [June 2026 self-hosting URL change](https://supabase.com/changelog/47093-self-hosted-supabase-api-external-url-to-include-auth-v1) was checked against the existing configuration: its external Auth URL already includes the required `/auth/v1` prefix. No runtime startup configuration is changed by this slice.
