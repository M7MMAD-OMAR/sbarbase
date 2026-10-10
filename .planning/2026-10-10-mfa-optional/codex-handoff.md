# Handoff for Codex: remembered management sessions (owner request)

Written by Claude Code on 2026-10-10 after reading the working tree while Codex was editing it. Nothing in this note was applied by Claude. It lists what is still missing in the current uncommitted work, with evidence.

## What the owner asked
The console is live at https://sbarbase.sbarah.com. The owner does not want to be asked for a code (or the password) on every visit or reload, because the phone is not always at hand. Requested: remember the sign-in for about 30 days, and keep it safe and stable for simple and complex use. MFA optional per installation is already being built here; this note covers the remembered session.

## Already fixed in the working tree (no action)
- ui/App.tsx: the in-memory `mfaVerified` flag that was reset on every `SIGNED_IN` event is gone. `ManagementGate` now asks the server (`managementPolicy`, `authorized`). Reloads and tab refocus no longer fake a missing verification.

## Still missing (evidence at the working tree state seen)

### 1. The browser forgets the session on reload
`ui/api.ts:5` creates the client with `persistSession:false, autoRefreshToken:true`. The session lives only in memory, so every reload or new tab returns to the password screen, whatever the server allows. Needs a decision:
- Recommended: `persistSession:true` with a dedicated `storageKey`, plus a CSP check (the console already serves `script-src 'self'`, which limits the XSS exposure of a stored refresh token). Sign out (`signOut({scope:'local'})`) must clear it. Consider `sessionStorage` only if the owner prefers per-tab sessions.
- Risk to document in docs/engineering/MANAGEMENT-MFA.md and the Arabic sibling: a stolen refresh token inside the allowed window is a session. Logout already revokes grants and advances the actor epoch, which is the mitigation.

### 2. The server caps the password age at 12 hours in six places
All use the literal 43200 seconds:
- src/control/auth.ts:34 (`passwordClaims` returns null), :67 (`hasManagementMfa`), :77 (`hasManagementAccess`)
- src/control/transfer-history.ts:62
- src/control/management-auth.ts:127 (`grantPassword` expiry) and :137 (`grantManagementMfa` expiry)
- lab/management_access.py:24 (Python mirror of the lifecycle gate)
Proposal: one helper, for example `managementSessionSeconds()` in a new small module, reading `SBARBASE_MANAGEMENT_SESSION_SECONDS`, default 43200, accepted range 3600 to 2592000, anything else falls back to the default (fail closed). Use it in all six places and mirror the same rule and bounds in management_access.py (read the same environment variable). Keep the default so existing tests and docs stay true; add tests for the override and for out-of-range values.

### 3. Native Auth session settings
The management GoTrue container has no `GOTRUE_JWT_EXP`, `GOTRUE_SESSIONS_TIMEBOX` or inactivity setting in lab/*.py, compose.yaml or deploy/ (searched). Defaults mean a one hour access token refreshed by the stored refresh token, with no session timebox. Verify in the pinned Auth image (`lab/images.lock.json`, v2.196.0) that a refresh token older than a week still works, and that `amr` keeps the original `password` timestamp across refreshes (the cap in item 2 depends on it). If the timestamp moves on refresh, the age cap cannot work as designed.

### 4. Grants expire with the JWT
management-auth.ts:127 uses `checked.passwordAt+43200` for the password grant, and an earlier version used `Math.min(checked.expiresAt, passwordAt+43200)`. Confirm that `refreshCurrentManagement` in src/control/application.ts renews grants on each token refresh so a 30 day session does not die at the first one hour token expiry.

## Docs and checks to update together
- docs/engineering/MANAGEMENT-MFA.md and MANAGEMENT-MFA.ar.md (the twelve hour statement, the new variable, the storage trade-off)
- docs/reference/configuration.md and configuration.ar.md (new variable row)
- bun test, `/usr/bin/python3 -m unittest discover -s lab -p 'test_*.py'`, lab/test_docs_links.py, lab/test_docs_bilingual.py, lab/test_doc_references.py

## Deployment note
The server (187.7.70.170, alias `sbarbase-vps`) runs commit 8960363 with a clean checkout. Do not push Claude's or Codex's uncommitted files to it by copying. Commit, push, then `git pull --ff-only` as user `sbarbase`, `bun run build:ui`, and `systemctl restart sbarbase`. After that, set `Environment=SBARBASE_MANAGEMENT_SESSION_SECONDS=2592000` in a systemd drop-in if the owner confirms 30 days.

## Do not touch
Claude's five commits (1d0f8eb through c2881c3) are documentation and installer text only. No overlap with these files.
