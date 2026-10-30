[العربية](sign-in.ar.md)

# Sign-in settings and OAuth providers

For a click by click walkthrough with screenshots of every page, see [Authentication in the console](authentication.md). This page is the reference.

Each environment has its own sign-in settings: where Auth sends people after they sign in or open an email link, who may sign up, and which providers (Google, GitHub, Apple and others) they can sign in with. Set them in the console: open the environment, then **Sign-in settings**. Only owners and admins of the client see this page.

## 1. Give the server its public address

Auth builds email links and OAuth callback addresses from the address people reach the server at. Set it once for the installation, then restart:

| Install | Where |
|---|---|
| Docker | `sudo SBARBASE_PUBLIC_URL=https://api.example.com deploy/compose.sh up`. The launcher reads no `.env` file, so give the same value on every later `up` |
| systemd | `Environment=SBARBASE_PUBLIC_URL=https://api.example.com` under `[Service]` in a drop-in such as `/etc/systemd/system/sbarbase.service.d/public-url.conf`, then `sudo systemctl daemon-reload && sudo systemctl restart sbarbase` |

Put HTTPS in front first ([server deployment](server-deployment.md)). Until this is set, links point at `http://localhost`, which only works on the server itself.

## 2. Set where people land

On the **URL Configuration** page:

- **Site URL:** your application's address, for example `https://app.example.com`. Auth sends people here after an email link or a sign-in when no other address is allowed.
- **Redirect URLs:** one per line. `*` and `**` match parts of an address, so `https://app.example.com/**` allows every page of that site. Mobile apps can use their own scheme, such as `myapp://callback`.

On **Sign In / Providers**, section **User signups**: **Allow new users to sign up**, **Allow manual linking** (people may link another sign-in method to their account) and **Allow anonymous sign-ins.**

## 3. Add a provider

1. On **Sign In / Providers**, open the provider from the list and copy **Callback URL (for OAuth)**. It looks like `https://api.example.com/<environment>/auth/v1/callback`.
2. In the provider's developer settings (for example GitHub → Settings → Developer settings → OAuth Apps), create an application and paste that callback address.
3. Back in the provider's panel, turn on **Enable Sign in with ...**, then paste its **Client ID** and **Client secret**. Keycloak also needs its server address; Azure, GitLab and WorkOS take one when you run your own. Press **Done**. Some providers show more switches: **Skip nonce checks** (Apple and Google) and **Allow users without an email**. Google takes a comma-separated list of client IDs. These two switches follow the names Supabase's own dashboard uses; they have not yet been confirmed against a running Auth, so test them with your provider before relying on them.
4. Press **Save and apply** in the bar at the bottom of the page.

In your application, sign in the usual way:

```js
await supabase.auth.signInWithOAuth({ provider: 'github', options: { redirectTo: 'https://app.example.com/welcome' } })
```

## 4. Choose how long a session lasts

Page **Sessions** sets three lifetimes. All three are optional, and an empty field means no cap: Auth sets no variable there, so the session does not end on that count. With all three empty, a person who signs in once stays signed in, which is what a store that never wants to log a buyer out wants.

| Field | What it sets | Empty means |
|---|---|---|
| **Access token expiry time (seconds)** | `GOTRUE_JWT_EXP`, how long one access token is valid | the pinned Auth default, 3600 seconds. The client refreshes it, so this does not sign anyone out |
| **Time-box user sessions (hours)** | `GOTRUE_SESSIONS_TIMEBOX`, the longest a session may live from the moment it started | no limit |
| **Inactivity timeout (hours)** | `GOTRUE_SESSIONS_INACTIVITY_TIMEOUT`, the idle time before a session ends | no limit |

A cap applies to sessions started after the change; a session already running keeps the lifetime it started with. The fields take whole numbers: hours for the two caps, seconds for the token.

## The Authentication page

In the console, open the environment, then **Sign-in settings**. The page has a menu on the left, like Supabase's Authentication pages:

- **Sign In / Providers**: **User signups** (allow new users, allow anonymous sign-ins) and the list of **Auth providers**, each with its logo and an **Enabled** or **Disabled** badge. Opening a provider shows a panel with its switch, client ID, client secret, server address where the provider takes one, and the **Callback URL (for OAuth)** with a Copy button.
- **URL Configuration**: **Site URL** and **Redirect URLs**.
- **Sessions**: the three lifetimes.
- **Sessions** also holds **Enforce single session per user**.
- **Attack Protection**: **Prevent use of leaked passwords** (the environment's Auth gets outbound network access while it is on, because Auth asks a public range service) and **Minimum password length** (6 to 72). The captcha setting of the cloud is not offered: its variable names could not be confirmed in the pinned Auth.
- **Multi-Factor**: whether people may enroll and verify with an authenticator app (TOTP), and the maximum number of factors per person. Both TOTP switches show **on** because that is the default written in the pinned Auth's own configuration; nothing is sent until you change one, and changing one back to its default and saving sends nothing either.

What was tried on a running Auth: `lab/auth-names-check.py` starts the pinned Auth on a private network with a throwaway database and checks each setting above with a real request (a sign-up refused, an expiry that changes, a second factor refused, a leaked password refused). The evidence is in `docs/evidence/auth-live-names-2026-10-30/`. Two things it found are why some cloud settings are missing here:

- **Detect and revoke potentially compromised refresh tokens** and **Refresh token reuse interval** are not offered: with the pinned Auth, turning the first off, or setting the second to 0, changed nothing a client can observe.
- **Rate limit for token refreshes, anonymous sign-ins and Web3** are not offered: the pinned Auth applies them per client address only when it is told which request header carries that address, and the gateway does not forward a trusted one yet. The mail limits on the **Emails** page count for the whole environment instead.

Not tried: the two provider switches **Skip nonce check** and **Allow users without an email**, whose effect needs a real provider sign-in.

One bar at the bottom of the page saves everything: it says when you have unsaved changes, **Discard** puts the page back to what is saved, and **Save and apply** saves and restarts this environment's Auth. The badge at the top shows **Applied**, **Applying…**, **Not applied** (with the reason) or **Default settings**.

- Saves are spaced: a second save of the same environment within 10 seconds of its last state change (a save, or the end of an apply) is refused ("Wait a few seconds before saving again"), because each save restarts that environment's Auth.
- The audit log lists the names of the settings a save changed (for example `SITE_URL` or `EXTERNAL_GITHUB_SECRET`), never their values.
- A secret is never shown again: a saved one reads "Saved. Leave empty to keep it.".
- Everything on the page belongs to this environment alone. Another environment's providers and secrets never appear here and cannot be read through it.
- Only owners and admins see the page.
- The logos are local files of the console, never loaded from another site; their source and terms are in `ui/assets/providers/SOURCES.md`. Microsoft (Azure) and WorkOS show a letter instead of a logo.

The plan and its limits are in [the Authentication workspace plan](../engineering/plans/2026-10-30-auth-providers-workspace.md). Studio's own Authentication settings pages stay hidden by upstream design, so the console is where they are built.

## Emails: custom SMTP

On the **Emails** page, turn on **Enable custom SMTP**, then enter **Sender email address**, **Sender name**, **Host**, **Port number**, **Username** and **Password** and press **Save changes**. Saving writes this environment's private mail file on the server and restarts its Auth with it; if Auth does not start, the previous Auth keeps running, the mail file goes back to what it was, and the badge says **Not applied**. **Applied** means Auth started with the settings, not that your SMTP provider accepts them: try a test sign-up.

- Confirm email and the email limits do not need custom SMTP: see [Confirm email and the email limits](#confirm-email-and-the-email-limits) below.
- The password is never shown again: a saved one reads "Stored password is hidden. Enter a new password to replace it." Leaving the field empty keeps it.
- Turning the switch off and saving removes the stored SMTP settings and password from this environment. Confirm email and the email limits stay as they are. The settings you do not see here (code length and lifetime, the trusted client address header, reply-to) keep their file values, or these defaults for a new file: a 6 digit code valid for one hour and no reply-to address.
- The same file can still be written from the host with `lab/mail_config.py` ([environment email](../engineering/ENVIRONMENT-EMAIL.md)).
- Without SMTP no email is sent: with nothing saved, sign-ups are confirmed automatically, as on any Auth with no mail relay.
- Email templates have their own sub page, below.

### Confirm email and the email limits

Under **Confirmation and limits** on the same page, whether or not custom SMTP is on:

- **Confirm email** makes new people confirm their email address before their first sign-in. People who already signed up are not affected. With nothing saved and custom SMTP off, it is off, because this environment sends no email. The first time you turn custom SMTP on, the switch turns on with it, as it always did; you can turn it back off before saving. With it off, new sign-ups are not checked against the address they give (anyone can register an address that is not theirs), recovery and magic link emails lose the confirmation throttle, and Auth does not apply the hourly email limit. With it on and custom SMTP off, nothing can be sent, so new people cannot confirm unless a Send Email hook delivers the message; the page says so.
- Four numbers set the limits: emails per hour (default 30), sign-in email requests and token verifications in 5 minutes (default 30 each; counted for the whole environment, not per visitor), and the minimum seconds between two emails to one user (default 60). An empty field puts the limit back to its default.
- The live check showed the hourly limit and the minimum interval working. It did not show the two 5 minute limits (sign-in emails, token verifications) refusing anything on the paths it tried, with or without a client address header, so treat those two as unproven.
- These are sign-in settings (`MAILER_AUTOCONFIRM`, `RATE_LIMIT_EMAIL_SENT`, `RATE_LIMIT_OTP`, `RATE_LIMIT_VERIFY`, `SMTP_MAX_FREQUENCY`), saved with the same request as custom SMTP and restarting Auth once. Only what you changed is saved.
- **Environments installed before this change keep what they had.** A mail file written by an earlier version still carries these values, and they stay in force until the settings name them. The first save from this page moves them into the sign-in settings and takes them out of the file; turning custom SMTP off moves them too, so Confirm email and the limits are no longer reset by it. A value the page cannot hold (a limit written by hand as `30/1h`, an interval such as `1h30m`) stays in the file, shows as "Set in the mail file", and is lost only if you turn custom SMTP off.

## Emails: templates

Under **Emails**, open **Templates**. The six messages Auth sends (Confirm sign up, Invite user, Magic link, Change email address, Reset password, Reauthentication) each have a **Subject heading** and an HTML message, with a **Source** and a **Preview** view and the list of variables Auth fills in (`{{ .ConfirmationURL }}`, `{{ .Token }}`, `{{ .Email }}` and the others). **Reset to default** brings back Supabase's own text; a message left as the default is stored as nothing, and Auth keeps using its own.

- The pinned Auth does not take a message body in its settings: it takes the address of a page and fetches it each time it sends. The console serves each environment's messages on its internal route, `/<environment>/mail-templates/<token>/<message>`, on the host's address on the runtime network. Other containers on this host's Docker networks can reach that address, so the token is the protection, not the network. The token is random, one per environment, kept in a private file (`<runtime>-templates.json`, mode 0600) and in that environment's Auth only; it is never shown. A request that names another environment, a wrong token or a message with no custom body gets the same plain 404.
- Saving restarts this environment's Auth, like every Authentication change, because the addresses and subjects are Auth settings. If it does not start, the previous Auth keeps running and the badge says **Not applied**.
- A message needs **custom SMTP** to be delivered; templates alone send nothing, and Auth is given them only while custom SMTP is on. The page says so when SMTP is off.
- The route answers only while the console is running and the host firewall lets containers reach the runtime network address (the same port Studio uses, 25432 unless `SBARBASE_STUDIO_UPSTREAM_PORT` says otherwise). A firewall rule that blocks it makes Auth fall back to its own text although the badge says **Applied**; Applied does not probe the route.
- A template that Auth cannot parse (for example a `{{` with no `}}`) is refused when you save. A variable name Auth does not know is not caught here; send a test message to check.
- Auth keeps a body in memory after it first fetches it. The live check in `docs/evidence/auth-live-names-2026-10-30/` stopped the template server after Auth had fetched the six messages and asked for a password recovery: the message was sent with the custom subject and the custom body. That is why every save restarts Auth: a changed body is picked up when Auth starts again. What Auth sends when the first fetch fails was not tested.
- A custom body saved here wins over the older `magic_link_template` address in the mail file.

## Phone

**Phone** turns on sign-up and sign-in with a phone number and a code sent by SMS. Pick a **Provider** (Twilio, Twilio Verify, MessageBird, Textlocal or Vonage), enter its credentials, and press **Save changes**. Credentials are written to a private file on the server (`<runtime>-sms.json`, mode 0600) and never shown again; a stored one reads "Stored value is hidden". Changing the provider needs that provider's own credentials: the old ones are not carried over. **Enable phone confirmations** is on by default; turning it off signs new numbers in without checking that the number is theirs. **Rate limit for sending SMS** caps the messages this environment sends per hour, across all numbers, which bounds what the provider can bill you.

- While phone sign-in is on, this environment's Auth can reach the internet, because every provider is an HTTPS service. Turning it off removes the credentials from the file and that access again (unless mail or another provider needs it).
- The live check (`lab/auth-names-check.py`) showed that each provider's variables reach the provider call and that the hourly limit stops the second message. It did not deliver a message through a real provider: sign up with a real number to check the credentials.
- Not offered: the minimum interval per number, the message template and the code length and lifetime. The live check could not observe them without a real provider.

## Hooks

**Hooks** lists the seven Auth hooks of the Supabase cloud (Customize Access Token claims, Send Email, Send SMS, MFA Verification Attempt, Password Verification Attempt, Before User Created, After User Created). Turn one on and name a Postgres function by schema and name; Auth calls it in this environment's database as its own Auth login (`<runtime>_auth`). Create the function in the SQL editor, then run the two `grant` lines the page shows so that login may run it. The live check ran a hook that adds a claim to the access token and saw the claim in the token; a hook naming a missing function makes sign-up fail with an error, so test a hook before you leave it on.

- Only Postgres function hooks are offered. An HTTPS hook needs the environment's Auth to reach the internet and a shared secret, which the console does not set up.
- The live check tried the access token hook; the other six use the same variable names and were not tried one by one.

## Single Sign-On (SAML)

**Single Sign-On** turns on SAML 2.0 for the environment and manages its identity providers. The first time you turn on **Enable SAML 2.0 Single Sign-On** and save, the console makes the environment's SAML signing key (a private file, `<runtime>-saml.json`, mode 0600) and keeps it when SAML is turned off, because identity providers trust its certificate. Once applied, the page shows the **SAML metadata URL** and the **ACS URL** to give to the identity provider, lists the providers, adds one from pasted **Metadata XML** with the **Domains** whose people it serves, and removes one. People whose email is on a listed domain can sign in through that provider when your app asks for single sign-on (`signInWithSSO`).

- A metadata web address is not fetched: Auth would need to reach the internet for that. Download the XML from the identity provider and paste it.
- The providers live in the environment's Auth and are managed through its admin API, so the environment's database access does not need to be on. Adding and removing a provider is in the activity log by provider id.
- The metadata and ACS addresses are served through the gateway without an API key, as an identity provider's administrator and the visitor's browser cannot send one (a gateway test covers the paths).
- The signing key file is kept when SAML is turned off. If it is removed or damaged, saves that keep SAML on are refused with a message, and a start of that environment is refused until the file is fixed on the host; removing it makes a new key, and every identity provider must then be given the new certificate.
- The live check turned SAML on with a generated key, created a provider from metadata XML, listed and removed it. It did not complete a sign-in through a real identity provider.

## Audit Logs

**Audit Logs** lists what the environment's Auth recorded (sign-ins, sign-ups, token refreshes, account changes), newest first, 50 to a page, with who, what, the IP address and when. The list comes from Auth's own admin API, so the environment's database access does not need to be on. **Write audit logs to the database** is the switch; off stops new entries and keeps the old ones, and the page says so. The people in the list are that environment's users: only owners and admins see it.

## What happens when you save

Saving recreates that environment's Auth with the new settings; other environments are not touched. Users, sessions and every row stay, because Auth keeps them in the environment's database. The badge at the top of the page shows **Applying…**, then **Applied**. If Auth does not start with the new settings, the previous Auth keeps running and the badge says **Not applied** with the reason; **Save and apply** stays available so you can retry.

Saving is safe against two people at once and against a bad file:

- A save made from a page that was opened before another save is refused with "These settings changed since you opened them", so it cannot overwrite the newer one. Reload and save again.
- Restarts of Auth, and replacements during an upgrade, use the **last applied** settings, never a change that failed or is still waiting. A saved file that cannot be read (damaged, from a newer version, a link, or readable by other users) stops that environment's start with a message instead of bringing Auth back on defaults; the other environments start normally.
- If an apply is interrupted after the old Auth was stopped, the next apply puts the old Auth back first.

The client secret is written to a private file on the server and handed to that environment's Auth. The console never shows it again: an empty secret field keeps the saved one.

Email links and OAuth steps reach Auth without an API key, as they do on Supabase, because a browser following a link cannot send one. Only those steps are open: `verify`, `authorize` and `callback`.

CI saves a GitHub provider on a clean machine with every change, starts a GitHub sign-in and opens an email link the way a browser does, and checks that email sign-up still works afterwards ([evidence](../evidence/docker-sign-in-checks.json)). No real provider is contacted there, so the first real sign-in with your provider is the one to try yourself.
