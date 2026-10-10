# Findings: making management MFA optional

## How MFA is enforced today (read-only research)
- Design doc: docs/engineering/MANAGEMENT-MFA.md (+ .ar.md). Rule: every management account enrolls native TOTP and verifies before any management access. No bypass, no recovery codes, no public reset by design.
- Single predicate: `hasManagementMfa` in src/control/auth.ts:64 (needs native aal2 + verified TOTP factor + persisted grant).
- Enforcement call sites: src/control/application.ts:40, :74, :82; src/control/lifecycle-authority.ts:92, :122; src/control/management-auth.ts:73, :88 (enroll/unenroll rules).
- Grants and epochs stored in src/control/management-security.ts (tables management_mfa_*).
- UI gate: ui/App.tsx:72 (MfaGate before Console), ui/Mfa.tsx, ui/mfa-state.ts.
- Studio signed sessions depend on the MFA epoch (docs say epoch is the invalidation contract).
- Tests touching it: tests/management-mfa.test.ts, management-native-refresh, management-initial-factor-binding, database-workflow, transfer-history. CLI also has MFA (docs/engineering/CLI-MFA.md).
- Recovery if all factors lost: private host helper lab/management-factor-recovery.ts run by a different owner.

## Conclusion
Optional MFA is not a UI toggle. It changes the security model: password-only sessions would reach management APIs, lifecycle authority, Studio session issuance and key operations. Needs a catalog-level policy flag, changes at ~7 enforcement points, UI gate, tests, both language docs, and invalidates scoped evidence.

## Update (later the same day): another agent is implementing the optional policy
- The working tree now has 15 modified files plus lab/management_access.py (untracked) that were not there when the plan was written: management_auth_policy (mfa_required), management_password_grant, hasManagementAccess, a UI "Installation sign-in policy" section. A Codex process (ChatGPT app) is running on this machine. These edits are not mine and are uncommitted.
- The plan facts above describe HEAD (8960363, c2881c3), where MFA is mandatory.
- Still open in that work (seen in the diff): session age cap is still 43200 seconds in auth.ts, transfer-history.ts, management-auth.ts and lab/management_access.py; ui/App.tsx resets the verified state on every SIGNED_IN event and keeps it only in memory, so a reload or tab refocus asks for the code again.
