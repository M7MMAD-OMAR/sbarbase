# Management sign-in, production creation and dashboard workflow

The owner requested a complete implementation of optional MFA, automatic production environments, a clear route to Supabase Studio and documentation reflecting all of these changes. The existing `.planning/2026-10-10-mfa-optional/task_plan.md` supplied the research for Option B. The owner's current request selects that implementation; its earlier recommendation to stop for an owner choice no longer applies.

## Requirements and outcomes

| Requirement | Outcome | Acceptance |
| --- | --- | --- |
| R1 | Installation MFA policy, default required; optional password sessions for accounts without verified factors | Owner uses a fresh native TOTP code to change policy. Enrolled accounts still verify MFA. HTTP, lifecycle TS/Python, transfer history, CLI and Studio retain session revocation. |
| R2 | Project and production creation commit atomically | Public project POST returns project id, environment id and queued state. Permission, name or capacity refusal leaves no partial project. The console selects the created project and reserves a dashboard tab; provisioning and Studio opening proceed automatically. |
| R3 | Discoverable Supabase dashboard | Project and connection overview show the named dashboard action. Provisioning disables it with an explanation. One click starts Studio and opens a signed session when ready. Native Studio opens directly without an iframe. Errors close the pending tab and allow retry, including a local status retry after a transient request failure. |
| R4 | Current operational documentation and a complete plan | English and Arabic README, MFA contract, CLI guide, Studio guide, console design and provisioning contract describe the implemented behavior. Historical evidence retains its original scope. |
| R5 | Original native Supabase UI | Entry is direct to `/project/default`; vendor body and security policies are preserved. The optional selector has no embedded editor. |
| R6 | Researched full UX and routine automation | Role-aware journey audit in English and Arabic; Create reserves a tab and performs production-to-Studio handoff. Cancellation, blocked tabs and failures retain the console. Advanced project settings stay available on request. |

## Execution order

1. Preserve the previous research and uncommitted planning files. Inspect all current MFA enforcement sites and lifecycle call edges.
2. Add durable policy and password grants. Keep native Auth as identity authority. Add owner-only policy read/change with fresh code verification, security lease, transactional audit and revocation.
3. Bind password lifecycle authority explicitly to the original native session, policy epoch and actor epoch. Mirror guards in installed Python workers and transfer history. Keep MFA material bound to its exact verified factor.
4. Make CLI and console use the server policy. Permit verified users to remove their final factor only in optional mode. Revoke password and Studio sessions when first enrollment verifies.
5. Add a public project creation transaction that queues production and rolls back on capacity refusal. Retain the metadata-only catalog helper for controlled internal fixtures and restore workflows. Update the installed first-project check to consume the returned production id.
6. Share the Supabase dashboard component between the project page and connection overview. Keep viewers outside Studio controls, retain direct environment connection navigation and expose dashboard stop under Database connection.
7. Research and audit the complete user journey. Preserve native Studio, automate Create through dashboard readiness with a cancellable reserved tab, retain explicit advanced configuration and verify blocked/closed tab and failure recovery.
8. Verify scoped unit cases, the full Bun suite, both type checks, UI build, Python effect policy tests and an isolated Chromium walkthrough at desktop and mobile sizes.
9. Obtain independent specification and operational/security reviews, review matched desktop captures against the supplied screenshot, repair findings, rerun affected evidence and export the checkpoint record.

## Authentication invariants

- Native Auth validates each exact bearer. JWT parsing is never a substitute for validation. Application identities and editable user metadata do not grant management access.
- Password mode requires `aal1`, no verified factors of any type, a successful proxy password login, a durable current session grant, unexpired token and password age below twelve hours. Refresh neither creates a grant nor extends password age.
- MFA mode requires `aal2`, current verified TOTP, the exact matching persisted verification grant and the original session binding.
- Policy changes require the current installation owner and native TOTP verification within five minutes. Changes invalidate every known actor epoch and both grant types, including Studio tickets/cookies. The policy generation rejects toggle ABA.
- Logout, verified-factor removal and recovery revoke both grant types. First successful factor enrollment advances the actor epoch and revokes password and Studio sessions before granting the newly verified MFA session.
- Lifecycle material identifies `password` or `mfa` explicitly, binds the policy generation and retains catalog ownership, operation, runtime, original Auth process, bearer and current owner checks at effects and publication.
- No password-only account can bypass an enrolled factor, authorize the restricted owner recovery helper, or change policy without fresh native verification.

## API and user flow

`GET /management/auth/v1/policy` authenticates a native password session and returns policy, whether MFA is needed, whether current grants authorize management and whether the actor owns installation policy. `PATCH` accepts only `{mfa_required: boolean}`. A successful policy change requires sign-in again.

`POST /management/v1/organizations/:organization/projects` returns HTTP 201 with `{id, environment, state: "queued"}`. `production` is the default environment name, not a claim of release acceptance or public deployment. Existing projects are retained; no unrelated resources are silently provisioned or restarted.

From **Projects**, choose **New project**, enter a name and create. The Create gesture reserves a tab. The selected project shows production provisioning, then Studio starts and opens its original full page automatically in that tab using a signed ticket. **Stay in console** or closing the waiting tab cancels the handoff; popup blocking exposes a manual Open action after preparation. Existing projects use **Open Supabase dashboard** on request. **Connection details** exposes the same action; **Database connection** exposes stop and direct connection controls, and **Sign-in settings** distinguishes local Auth configuration from native user management. Project settings are disclosed on request. Each additional environment is still created explicitly.

## Research and full user journey

The follow-up request prioritizes native Supabase interfaces, fewer choices and automatic routine steps. The [complete journey audit](../../design/USER-JOURNEY.md), also [in Arabic](../../design/USER-JOURNEY.ar.md), records current research, all role-aware paths, decisions, cancellation and failure recovery. Native HTML and framing policies remain intact. The optional workspace route is only a selector. No upstream Studio fork or new editor is introduced.

## Evidence and limits

Source tests: `tests/optional-mfa.test.ts`, `tests/lifecycle-authority.test.ts`, `tests/transfer-history.test.ts`, `tests/sbarbase-cli.test.ts`, and the existing management revocation suite. Python guard tests: `lab/test_optional_mfa_authority.py`. Browser vehicle: `lab/project-workflow-browser.ts`, with temporary catalog, native Auth fixture, original application/proxy code, an owned loopback server and a fresh Chromium profile. Browser assets use the actual production UI build.

Browser screenshots are generated in `output/playwright/project-workflow/`; the workflow engine stores command evidence and independent reports outside source scopes. The native Auth and Studio services in this browser vehicle are fixtures. These tests do not establish a new deployment, native container startup or production acceptance. The existing native checks remain separate, and `lab/first-project-check.ts` now uses automatically created production for the installed rehearsal.

Local validation: 931 Bun tests, seven Python policy tests and 28 Chromium interaction checks. Both TypeScript checks and the production UI build pass. Independent source and matched UI review reports are retained in the checkpoint export outside the source scopes. No public deployment or live installation policy change is part of this source delivery.
