[العربية](sign-in.ar.md)

# Sign-in settings and OAuth providers

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
- **Sessions** also holds **Enforce single session per user**, **Detect and revoke potentially compromised refresh tokens** and **Refresh token reuse interval** (seconds, up to 300).
- **Rate Limits**: token refreshes, anonymous sign-ins and Web3 sign-ins, each per IP address. An empty field keeps Auth's own default. The email, OTP and verification limits belong to the environment's mail settings, not to this page yet.
- **Attack Protection**: **Prevent use of leaked passwords** (the environment's Auth gets outbound network access while it is on, because Auth asks a public range service) and **Minimum password length** (6 to 72). The captcha setting of the cloud is not offered: its variable names could not be confirmed in the pinned Auth.
- **Multi-Factor**: whether people may enroll and verify with an authenticator app (TOTP), and the maximum number of factors per person. Both TOTP switches show **on** because that is the default written in the pinned Auth's own configuration; nothing is sent until you change one, and changing one back to its default and saving sends nothing either.

Honest limit of these four pages: the setting names were read from the pinned Auth program and from Supabase's own dashboard schema, not tried on a running Auth. The names for sessions, refresh tokens, the three rate limits and the TOTP and factor-count settings were found in the program; the names for leaked password protection and minimum length come only from the dashboard schema. A wrong name is ignored by Auth without an error, so check a setting with a real sign-in before relying on it.

One bar at the bottom of the page saves everything: it says when you have unsaved changes, **Discard** puts the page back to what is saved, and **Save and apply** saves and restarts this environment's Auth. The badge at the top shows **Applied**, **Applying…**, **Not applied** (with the reason) or **Default settings**.

- Saves are spaced: a second save of the same environment within 10 seconds of its last state change (a save, or the end of an apply) is refused ("Wait a few seconds before saving again"), because each save restarts that environment's Auth.
- The audit log lists the names of the settings a save changed (for example `SITE_URL` or `EXTERNAL_GITHUB_SECRET`), never their values.
- A secret is never shown again: a saved one reads "Saved. Leave empty to keep it.".
- Everything on the page belongs to this environment alone. Another environment's providers and secrets never appear here and cannot be read through it.
- Only owners and admins see the page.
- The logos are local files of the console, never loaded from another site; their source and terms are in `ui/assets/providers/SOURCES.md`. Microsoft (Azure) and WorkOS show a letter instead of a logo.

The remaining Authentication pages of the Supabase cloud (email templates and SMTP, rate limits, multi-factor, attack protection, audit logs) are planned in [the Authentication workspace plan](../engineering/plans/2026-10-30-auth-providers-workspace.md). Studio's own Authentication settings pages stay hidden by upstream design, so the console is where they are built.

## What happens when you save

Saving recreates that environment's Auth with the new settings; other environments are not touched. Users, sessions and every row stay, because Auth keeps them in the environment's database. The badge at the top of the page shows **Applying…**, then **Applied**. If Auth does not start with the new settings, the previous Auth keeps running and the badge says **Not applied** with the reason; **Save and apply** stays available so you can retry.

Saving is safe against two people at once and against a bad file:

- A save made from a page that was opened before another save is refused with "These settings changed since you opened them", so it cannot overwrite the newer one. Reload and save again.
- Restarts of Auth, and replacements during an upgrade, use the **last applied** settings, never a change that failed or is still waiting. A saved file that cannot be read (damaged, from a newer version, a link, or readable by other users) stops that environment's start with a message instead of bringing Auth back on defaults; the other environments start normally.
- If an apply is interrupted after the old Auth was stopped, the next apply puts the old Auth back first.

The client secret is written to a private file on the server and handed to that environment's Auth. The console never shows it again: an empty secret field keeps the saved one.

Email links and OAuth steps reach Auth without an API key, as they do on Supabase, because a browser following a link cannot send one. Only those steps are open: `verify`, `authorize` and `callback`.

CI saves a GitHub provider on a clean machine with every change, starts a GitHub sign-in and opens an email link the way a browser does, and checks that email sign-up still works afterwards ([evidence](../evidence/docker-sign-in-checks.json)). No real provider is contacted there, so the first real sign-in with your provider is the one to try yourself.
