# Management account MFA

Scope: SB-06, original management Auth `v2.196.0` from `lab/images.lock.json`, SDK `2.116.0`. Application Auth realms remain separate. This is development security implementation and scoped evidence, not production acceptance.

Every management account must sign in with its password, enroll a native TOTP authenticator if none exists, and verify a code before management access. The console presents these steps before loading projects. Account security lets users add a spare authenticator, verify it, and remove an old device while retaining a verified spare. Enrollment secrets and codes are held only for the active setup form and are never stored in the catalog, audit, or application logs.

## Server contract

The management proxy exposes only native password and refresh grants, `GET /user`, logout, settings, `POST /factors`, `POST /factors/{id}/challenge`, `POST /factors/{id}/verify`, and `DELETE /factors/{id}`. It refuses signup, OAuth, magic-link, recovery-link, arbitrary Auth admin paths, unexpected methods, query parameters and oversized bodies. Auth validates every identity token against the fixed management realm with redirects disabled and a five second deadline. User metadata cannot grant authority.

Protected `/management/` requests require the native validated password AMR, native `aal2`, a real `session_id`, a currently verified matching TOTP factor, and a persisted grant recorded after successful native verification through the management proxy. A valid native token obtained directly from the private Auth endpoint without this grant is refused. Password authentication has a twelve hour maximum age. Refresh does not extend it. Management authorization still reads current catalog membership for every operation. Tenant owners cannot become installation owners by enrolling MFA.

The authenticated HTTP request carries its current local grant check across asynchronous reads. Catalog authorization boundaries recheck that grant, and a revoked request's final response is refused. A pending streamed body cannot resume an administrative mutation after local MFA revocation. Private workers and host catalog calls keep their existing trusted entry points; the HTTP scope does not authorize private entry points.

Grant revocation is persisted per actor. Removing a verified factor revokes every management grant before and after the native request, including failures, so old tokens and concurrent sessions cannot retain management access. The server prevents removing the last verified authenticator. Native Auth can retain an old JWT's `aal2` claim until refresh, which is why the catalog grant and current factor checks are required in addition to the JWT claim. Recovery never issues an application token or a management grant.

Native factor mutations share a sixty second actor lease across controllers. The factor list is reread under the lease before removal, preventing simultaneous removals from both treating the other factor as a spare. Revocation also records a Unix second watermark: a verification at or before that timestamp cannot create a new grant. Wait for a new code and verify again if a concurrent removal or recovery produces `mfa_retry`. This prevents an older native token with the same second-resolution AMR from regaining access.

Proxy logout at any native scope also revokes all local management grants and advances the actor epoch before and after the upstream attempt. Other management sessions must verify MFA again, including when only a single native session was signed out. The durable `catalog.managementSecurity.epoch(actor)` is the invalidation contract for consumers such as signed Studio sessions. Direct edits or logout through a private native endpoint outside this proxy do not advance the local epoch. Native identity checks detect them on the next management request; dependent sessions must declare or independently enforce their private-admin invalidation boundary.

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

The only accepted reasons are `lost_factor` and `compromised_factor`. The helper validates the operator through native Auth plus the persisted MFA grant, checks current installation ownership, durably revokes all target grants, verifies the target password through original Auth, signs out the target globally through original Auth, then deletes only the selected native factor through the private native admin API. Requested, completed and failed outcomes are audited with identifiers and reason enums, never secrets. Failure retains grant revocation. Success still requires the target to log in again and complete native enrollment and verification. A new password session alone cannot access management.

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
