# Supabase cloud Authentication pages, observed 2026-10-30

Read only observation of one cloud project (a Free plan project, signed in by the owner in their own Chrome). Nothing was saved or changed; the Google drawer was opened and cancelled. No project values, keys or secrets are recorded here.

## Navigation (left sub menu, in order)

MANAGE: Users, OAuth Apps. NOTIFICATIONS: Emails. CONFIGURATION: Policies, Sign In / Providers, Passkeys (beta), OAuth Server (beta), Sessions, Rate Limits, Multi-Factor, URL Configuration, Attack Protection, Auth Hooks (beta), Audit Logs, Performance. Pages are routes under `/auth/`: providers, url-configuration, sessions, rate-limits, mfa, protection, templates (Templates tab) and smtp (SMTP Settings tab), audit-logs. A page has a title, one grey sentence, and sections; each section has its own "Save changes" button (disabled until something changes).

## Sign In / Providers

Tabs: Supabase Auth, Third-Party Auth. Section User Signups, four switches, one Save: Allow new users to sign up ("If this is disabled, new users will not be able to sign up to your application"); Allow manual linking ("Enable manual linking APIs for your project"); Allow anonymous sign-ins; Confirm email ("Users will need to confirm their email address before signing in for the first time"). Section Auth Providers, a list with an ENABLED or DISABLED badge per row, in this order: Email (enabled), Phone, SAML 2.0, Web3 Wallet, Apple, Azure, Bitbucket, Discord, Facebook, Figma, GitHub, GitLab, Google, Kakao, KeyCloak, LinkedIn (OIDC), Notion, Twitch, X / Twitter (OAuth 2.0), Twitter (Deprecated), Slack (OIDC), Slack (Deprecated), Spotify, WorkOS, Zoom. Then Custom Providers (new): a table Name, Identifier, Type, Enabled, and a New Provider button.

A provider opens as a right side drawer (the URL gains `?provider=Google`), with the provider logo and name in the header and Docs, Cancel and Save in the footer. Rows are label on the left, control and hint on the right. Google: Enable Sign in with Google (switch); Client IDs (text, "Comma-separated list of client IDs for Web, OAuth, Android apps, One Tap, and Chrome extensions."); Client Secret (for OAuth) (masked, with a Reveal button); Skip nonce checks (switch, warns it is less secure); Allow users without an email (switch); Callback URL (for OAuth) (read only, with a Copy button and a "Learn more" link).

## URL Configuration

Site URL (text; "Wildcards cannot be used here"; also a template variable in email templates), Save. Redirect URLs list (wildcards allowed, example `https://*.domain.com`), Add URL button, "Total URLs: n".

## Sessions

User Sessions (Pro plan only, shown with an upgrade notice): Enforce single session per user; Time-box user sessions (hours, "Use 0 for never. Maximum 8760 hours (1 year)"); Inactivity timeout (same). Access Tokens: Access token expiry time (seconds, "Recommendation: 3600 seconds"). Refresh Tokens: Detect and revoke potentially compromised refresh tokens; Refresh token reuse interval (seconds, "Recommendation: 10 seconds. Maximum 300 seconds").

## Rate Limits

Rate limit for sending emails (emails/h); sending SMS messages (sms/h); token refreshes (requests per 5 min per IP, with the hourly equivalent shown, for example "1800 requests per hour"); token verifications; anonymous users (requests/h per IP); sign-ups and sign-ins (requests per 5 min per IP, excludes anonymous); Web3 sign-ups and sign-ins (per 5 min); Minimum interval per user (seconds, "Only applies when Custom SMTP is configured"). Section IP Address Forwarding: Enable IP address forwarding.

## Multi-Factor

TOTP (App Authenticator): Enabled or Verify Enabled or Disabled choice; Maximum number of per-user MFA factors. SMS MFA (Pro plan only): Enabled/Verify Enabled/Disabled, Phone OTP Length (digits), Phone verification message (`{{ .Code }}`). Enhanced MFA Security: Limit duration of AAL1 sessions (15 minutes).

## Attack Protection

Bot and Abuse Protection: Enable Captcha protection (DISABLED badge, "Configure in email provider" link); Prevent use of leaked passwords. Save.

## Emails

Tabs Templates and SMTP Settings. Templates, grouped. Authentication: Confirm sign up, Invite user, Magic link or OTP, Change email address, Reset password, Reauthentication. Security (notifications): Password changed, Email address changed, Phone number changed, Sign-in method linked, Sign-in method removed, MFA method added, MFA method removed. SMTP Settings: Enable custom SMTP ("Rate limits apply"); Sender details: Sender email address, Sender name; SMTP provider settings ("Your SMTP credentials will always be encrypted in our database."): Host, Port number (hint: common ports 465 and 587, avoid 25), Username, Password ("Stored password is hidden. Enter a new password to replace it.").

## Audit Logs

One setting, "Write audit logs to the database" (writes to `audit_log_entries`; can be turned off to reduce disk use while the Auth logs stay available), and a log viewer below.

## Design notes (from screenshots)

Dark UI, sections separated by space and a slightly lighter surface, not outlined cards; section content on a raised panel with rows divided by hairlines; switches on the right of each row; a single green primary button per section; provider list rows carry a logo, name and a badge; drawer for provider details. Defaults the cloud shows: sign-up allowed, manual linking off, anonymous off, access token 3600 s, refresh reuse interval 10 s.

## Differences that matter for Sbarbase

Sessions, SMS MFA and some features are Pro plan on the cloud; self-hosted Auth has no plan gate. Email rate limit and SMTP are one page set on the cloud. Cloud has a hosted Captcha and hooks (beta) that a self-hosted Auth reads from environment variables.
