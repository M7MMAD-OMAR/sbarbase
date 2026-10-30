[العربية](sign-in.ar.md)

# Sign-in settings and OAuth providers

Each environment has its own sign-in settings: where Auth sends people after they sign in or open an email link, who may sign up, and which providers (Google, GitHub, Apple and others) they can sign in with. Set them in the console: open the environment, then **Sign-in settings**, section **Sign-in**. Only owners and admins of the client see this section.

## 1. Give the server its public address

Auth builds email links and OAuth callback addresses from the address people reach the server at. Set it once for the installation, then restart:

| Install | Where |
|---|---|
| Docker | `sudo SBARBASE_PUBLIC_URL=https://api.example.com deploy/compose.sh up`. The launcher reads no `.env` file, so give the same value on every later `up` |
| systemd | `Environment=SBARBASE_PUBLIC_URL=https://api.example.com` under `[Service]` in a drop-in such as `/etc/systemd/system/sbarbase.service.d/public-url.conf`, then `sudo systemctl daemon-reload && sudo systemctl restart sbarbase` |

Put HTTPS in front first ([server deployment](server-deployment.md)). Until this is set, links point at `http://localhost`, which only works on the server itself.

## 2. Set where people land

- **Site URL:** your application's address, for example `https://app.example.com`. Auth sends people here after an email link or a sign-in when no other address is allowed.
- **Other allowed redirect addresses:** one per line. `*` and `**` match parts of an address, so `https://app.example.com/**` allows every page of that site. Mobile apps can use their own scheme, such as `myapp://callback`.
- **Allow new users to sign up** and **Allow anonymous sign-ins.**

## 3. Add a provider

1. Under **Sign-in providers**, copy the **callback address to give each provider**. It looks like `https://api.example.com/<environment>/auth/v1/callback`.
2. In the provider's developer settings (for example GitHub → Settings → Developer settings → OAuth Apps), create an application and paste that callback address.
3. Back in the console, choose the provider, then paste its **client ID** and **client secret**. Keycloak also needs its server address; Azure, GitLab and WorkOS take one when you run your own.
4. Press **Save and apply**.

In your application, sign in the usual way:

```js
await supabase.auth.signInWithOAuth({ provider: 'github', options: { redirectTo: 'https://app.example.com/welcome' } })
```

## 4. Choose how long a session lasts

Section **Session** sets three lifetimes. All three are optional, and an empty field means no cap: Auth sets no variable there, so the session does not end on that count. With all three empty, a person who signs in once stays signed in, which is what a store that never wants to log a buyer out wants.

| Field | What it sets | Empty means |
|---|---|---|
| **Access token lifetime (seconds)** | `GOTRUE_JWT_EXP`, how long one access token is valid | the pinned Auth default, 3600 seconds. The client refreshes it, so this does not sign anyone out |
| **Signed in for at most (hours)** | `GOTRUE_SESSIONS_TIMEBOX`, the longest a session may live from the moment it started | no limit |
| **Sign out after (hours) of inactivity** | `GOTRUE_SESSIONS_INACTIVITY_TIMEOUT`, the idle time before a session ends | no limit |

A cap applies to sessions started after the change; a session already running keeps the lifetime it started with. The fields take whole numbers: hours for the two caps, seconds for the token.

## See everything Auth is set to

Under **Sign-in settings**, **Authentication configuration** shows what this environment's Auth is set to, grouped like the Supabase cloud's pages: Sign In / Providers, URL Configuration and Sessions, then every provider with its client ID and its callback URL. It is read only for now; change settings in the form below it.

- A value you never saved is marked **default**.
- A secret is never shown, only **Set (hidden)** or **Not set**.
- It shows this environment alone. Another environment's providers and secrets never appear here, and cannot be read through it.
- Only owners and admins see it.

Editing every page of this view in the console, the way the cloud does, is planned in [the Authentication workspace plan](../engineering/plans/2026-10-30-auth-providers-workspace.md). Studio's own Authentication settings pages stay hidden by upstream design, so the console is where they are built.

## What happens when you save

Saving recreates that environment's Auth with the new settings; other environments are not touched. Users, sessions and every row stay, because Auth keeps them in the environment's database. The section shows **Applying…**, then **Applied**. If Auth does not start with the new settings, the previous Auth keeps running and the section says **Not applied**.

Saving is safe against two people at once and against a bad file:

- A save made from a page that was opened before another save is refused with "These settings changed since you opened them", so it cannot overwrite the newer one. Reload and save again.
- Restarts of Auth, and replacements during an upgrade, use the **last applied** settings, never a change that failed or is still waiting. A saved file that cannot be read (damaged, from a newer version, a link, or readable by other users) stops that environment's start with a message instead of bringing Auth back on defaults; the other environments start normally.
- If an apply is interrupted after the old Auth was stopped, the next apply puts the old Auth back first.

The client secret is written to a private file on the server and handed to that environment's Auth. The console never shows it again: an empty secret field keeps the saved one.

Email links and OAuth steps reach Auth without an API key, as they do on Supabase, because a browser following a link cannot send one. Only those steps are open: `verify`, `authorize` and `callback`.

CI saves a GitHub provider on a clean machine with every change, starts a GitHub sign-in and opens an email link the way a browser does, and checks that email sign-up still works afterwards ([evidence](../evidence/docker-sign-in-checks.json)). No real provider is contacted there, so the first real sign-in with your provider is the one to try yourself.
