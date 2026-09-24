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
- **Rate limit:** at most 20 failed redemptions per minute per process, then `429` for further
  failures only. A valid token is looked up first and never limited, so noise from anyone cannot
  block a real invitation. The token's 256 bits already make guessing hopeless.
- **Body:** the redeem route answers anyone, so it reads at most 8 KiB and refuses more unread.
- **The inviter's authority must still hold at acceptance.** Demoting or removing a member
  cancels the invitations they issued, and accepting re-checks that the inviter can still grant
  that role. A removed admin cannot return through a link they made earlier.
- **Only owners invite into the bootstrap organization,** whose members are installation operators.
- **A new owner by invitation** raises the same critical `membership.owner_changed` notice as any
  other change of owner.
- **Signed-in joining is confirmed:** the console first shows which organization and role the link
  offers (a preview that changes nothing) and joins only when the person confirms.
- **Account errors:** only `email_exists` or `user_already_exists` from the realm mean an account
  exists; `weak_password` asks for a stronger password; anything else fails without detail.
- **Secrets:** the password travels in the request body only, never in a URL, log or audit
  event. The service role token lives in the server process and never appears in an answer.
- **Cancel:** owners and admins cancel a pending invitation (`DELETE .../invitations/{id}`);
  the list shows pending invitations with email, role, inviter and expiry, never the token.
- **Audit:** `invitation.created`, `invitation.cancelled`, `invitation.accepted`, with the role
  only (no email in the audit detail).
- **Order and failure:** the account is created before the membership. If membership fails after
  the account exists, the invitation stays unused, and the same link finishes the job once the
  person signs in.

## Trust, stated plainly

The token goes back to the inviter, who sends the link. So an inviter can redeem it themselves and
create a confirmed account for any email with a password they choose. Inviters are owners and
admins of a client, already trusted with that client's data; they cannot reach another client this
way, because the membership is only the one they could grant anyway. What they can do is occupy an
email in the management realm: a later invitation to that email answers that an account exists.
If that happens, the installation operator resets that account's password **and signs out all its
sessions** before handing it to the real person, so the squatter keeps nothing. Delivering the
token only by email to the invited address would close this, and needs a mail service for the
management realm, which does not exist yet.

## Limits

- Not verified against the live management Auth container yet; the admin API calls are the ones
  `lab/bootstrap-auth.ts` already uses (`createUser`, and `getUser` for a session's email).
- No invitation email. The inviter sends the link.
- Removing a member does not delete their account; it only ends their membership.
- If an invitation is cancelled between account creation and membership, the account is left
  without a membership; a new invitation to the same email then signs in and joins.
- Adversarial review on 2026-09-24 found seven problems; each is fixed with a test in
  `tests/invitations.test.ts` or `tests/audit.test.ts`, except the trust point above, which is by
  design and documented.
