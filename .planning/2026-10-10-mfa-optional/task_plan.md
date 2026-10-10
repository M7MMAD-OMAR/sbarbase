# Task Plan: management MFA without a phone (revised after code reading)

## Goal
The owner does not want mandatory MFA because the phone is not always at hand. Decide how to give phone-free, low-friction management sign-in without breaking the security model, and list exact changes.

## Verified code facts (file:line in the repo at 8960363)
1. `hasManagementMfa` (src/control/auth.ts:64) is the single predicate. It needs native aal2, a verified TOTP amr timestamp, and a persisted grant row in `management_mfa_grant` (src/control/management-security.ts).
2. Management API gate: src/control/application.ts:74 returns 403 `mfa_required`; :40 builds the control identity from the same predicate; :82 rechecks per request.
3. Lifecycle authority (create/delete project, provisioning) binds a concrete verified TOTP factor id: src/control/lifecycle-authority.ts:78 (`Material.factor` must be a verified TOTP factor), :93 (`grantedFactor` call) and :96 (must be non-null), :122 (grant must match the same factor). A password-only (aal1) actor has no factor, so this path would refuse.
4. Console gate: ui/App.tsx:72 shows `MfaGate` until `sessionHasMfa`.
5. Enrollment rules: src/control/management-auth.ts:73 and :88 (cannot remove the last verified factor).
6. Studio sessions use the MFA epoch (`management_mfa_epoch`) as the invalidation contract.
7. Recovery when all factors are lost: lab/management-factor-recovery.ts (private host helper, no HTTP route). The docs say a different current owner runs it. No recovery codes by design (docs/engineering/MANAGEMENT-MFA.md:67).
8. Additional enforcement sites found by the Haiku review: src/control/transfer-history.ts:61 (rejects non-aal2), lab/lifecycle_native_authority.py:100 (Python mirror of the lifecycle gate), lab/sbarbase.ts:474 (CLI sessions require aal2), ui/api.ts:33 and ui/mfa-state.ts:2-3 (client side), src/control/management-auth.ts:110 (grant minting), src/control/studio.ts:79 and :186 (epoch checks).

## Options
### Option A (recommended): keep MFA, remove the phone dependency, zero code change
- Enroll the TOTP secret in a desktop-capable authenticator: a password manager with TOTP (Bitwarden, KeePassXC, 1Password) or the browser. Enroll a second factor as spare through Account security (the product supports spares).
- A user may hold several verified TOTP factors and the UI has "Add authenticator" under Account security (ui/Mfa.tsx:78-81). Removal needs another verified factor.
- Limit: a spare can only be added from a session that already passed MFA (management-auth.ts:73). If the only device is already lost, Option A is gone and only the host recovery helper remains. So enroll the second factor right after the first.
- Trade-off, not a code fact: keeping the TOTP seed in the same password manager as the password puts both factors behind one credential. Prefer a separate vault or a hardware-backed second factor for the spare.
- Cost: minutes, no code change.

### Option B: optional MFA per installation (code change, large)
- Add catalog setting `mfa_required` (default true), changeable only by the owner after a fresh verified factor.
- When false, accounts with NO verified factor may use aal1 password sessions; accounts that enrolled a factor still need aal2 (no downgrade).
- Must also redesign lifecycle authority (fact 3): either keep MFA mandatory for lifecycle and Studio (then password-only users cannot create projects, which defeats the purpose) or introduce a password-freshness authority bound to session id instead of factor id. This is a new security design and needs its own review.
- Update: auth.ts, application.ts, lifecycle-authority.ts, management-auth.ts, transfer-history.ts, studio.ts, lab/lifecycle_native_authority.py, lab/sbarbase.ts (CLI), ui/App.tsx, ui/Mfa.tsx, ui/api.ts, ui/mfa-state.ts, 5 test files, MANAGEMENT-MFA.md and .ar.md, CLI-MFA.md, evidence refresh, docs link tests.
- Exposure: sbarbase.sbarah.com is public, so password-only management access relies on one secret. Rate limits exist (10 password grants per account per 10 min, from the table in MANAGEMENT-MFA.md).

### Option C: edge gate instead of MFA
- Cloudflare Access (free tier) in front of sbarbase.sbarah.com with email one-time PIN or Google login. Does not remove the app MFA requirement (so it does not solve the phone problem alone).

## Recommendation
Option A now. Option B only after a written design review because it changes who can create and delete projects.

## Status
- Phase 1 research: complete
- Phase 2 plan written: complete
- Phase 3 independent review (Haiku 4.5, agent a500326ca5fa29b19): complete, 3 corrections applied (line :96, missed call sites, Option A limits)
- Phase 4 implementation: Option B selected by the owner on 2026-10-10; implemented in the current worktree, verification in progress

## Next Step
The owner selected optional MFA implementation. The expanded [implementation plan](../../docs/engineering/plans/2026-10-10-management-project-workflow.md) also covers automatic production creation, dashboard entry and documentation.

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| first plan assumed a UI toggle | 1 | replaced after reading lifecycle-authority.ts |

## Current implementation contract

The user's 2026-10-10 request selects Option B. Installation MFA remains required by default, with an owner-only fresh-code setting under Account security. Optional mode permits password-only access for accounts with no verified factors and supports removing the last factor from a verified session. Durable password session grants, explicit lifecycle authorization mode, policy generation and actor epoch protect HTTP, native lifecycle, transfer history, CLI and Studio. First factor verification invalidates older password and dashboard sessions. The comprehensive plan above supersedes the earlier request for a choice.
