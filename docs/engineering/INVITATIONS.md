# Invitations to an organization

Status: design record and implementation, 2026-09-24. Unit tested with a fake management Auth;
not run against the live management Auth container.

## Why

Signup is closed in the management Auth realm (`GOTRUE_DISABLE_SIGNUP=true`), so the only
account today is the first operator's, made by `lab/bootstrap.py`. An owner could not bring a
colleague in. Invitations are that path, and only that path: nothing else creates a management
account.

## Flow

1. **Invite.** An owner or admin of an organization creates an invitation for one email and one
   role (`POST /management/v1/organizations/{id}/invitations`). An admin may invite admins and
   viewers, never owners; nobody grants a role above their own. The answer carries the token
   **once**, inside a console link the inviter sends themselves (no mail is sent: the management
   realm has no mail service of its own).
2. **Redeem.** The invited person opens the link. The console asks for a password (12 characters
   or more) and calls `POST /management/invitations/redeem` with the token. The server creates the
   account through the management realm's admin API, with the email confirmed, adds the membership
   and marks the invitation used, in that order. The console then signs in with the new password.
3. **Already has an account.** If an account for that email exists, the redemption answers that
   the person should sign in first. Signed in, the same link redeems with their session: the
   session's email must equal the invitation's email.

## Rules

- **Token:** 32 random bytes, base64url. Only its SHA-256 is stored, as `KeyStore` stores keys.
  Single use, expires after 7 days, bound to one organization, role and email.
- **Same answer for every bad token:** unknown, expired, used and cancelled all get the same
  `400 This invitation is not valid`, so the route cannot confirm which invitations exist.
- **Rate limit:** at most 20 failed redemptions per minute per process, then `429`. The token's
  256 bits already make guessing hopeless; the limit only stops noise.
- **Secrets:** the password travels in the request body only, never in a URL, log or audit
  event. The service role token lives in the server process and never appears in an answer.
- **Cancel:** owners and admins cancel a pending invitation (`DELETE .../invitations/{id}`);
  the list shows pending invitations with email, role, inviter and expiry, never the token.
- **Audit:** `invitation.created`, `invitation.cancelled`, `invitation.accepted`, with the role
  only (no email in the audit detail).
- **Order and failure:** the account is created before the membership. If membership fails after
  the account exists, the invitation stays unused, and the same link finishes the job once the
  person signs in.

## Limits

- Not verified against the live management Auth container yet; the admin API calls are the ones
  `lab/bootstrap-auth.ts` already uses (`createUser`, and `getUser` for a session's email).
- No invitation email. The inviter sends the link.
- Removing a member does not delete their account; it only ends their membership.
