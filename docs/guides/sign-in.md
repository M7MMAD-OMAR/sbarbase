[العربية](sign-in.ar.md)

# Sign-in settings and OAuth providers

Each environment has its own sign-in settings: where Auth sends people after they sign in or open an email link, who may sign up, and which providers (Google, GitHub, Apple and others) they can sign in with. Set them in the console, on the environment's page, under **Sign-in**. Only owners and admins of the client see this section.

## 1. Give the server its public address

Auth builds email links and OAuth callback addresses from the address people reach the server at. Set it once for the installation, then restart:

| Install | Where |
|---|---|
| Docker | `SBARBASE_PUBLIC_URL=https://api.example.com` in a `.env` file next to `compose.yaml`, then `docker compose up -d` |
| systemd | `Environment=SBARBASE_PUBLIC_URL=https://api.example.com` in the service, then `sudo systemctl restart sbarbase` |

Put HTTPS in front first ([server deployment](server-deployment.md)). Until this is set, links point at `http://localhost`, which only works on the server itself.

## 2. Set where people land

- **Site URL:** your application's address, for example `https://app.example.com`. Auth sends people here after an email link or a sign-in when no other address is allowed.
- **Other allowed redirect addresses:** one per line. `*` and `**` match parts of an address, so `https://app.example.com/**` allows every page of that site. Mobile apps can use their own scheme, such as `myapp://callback`.
- **Allow new users to sign up** and **Allow anonymous sign-ins.**

## 3. Add a provider

1. Under **Sign-in providers**, copy the **callback address**. It looks like `https://api.example.com/<environment>/auth/v1/callback`.
2. In the provider's developer settings (for example GitHub → Settings → Developer settings → OAuth Apps), create an application and paste that callback address.
3. Back in the console, choose the provider, then paste its **client ID** and **client secret**. Keycloak also needs its server address; Azure, GitLab and WorkOS take one when you run your own.
4. Press **Save and apply**.

In your application, sign in the usual way:

```js
await supabase.auth.signInWithOAuth({ provider: 'github', options: { redirectTo: 'https://app.example.com/welcome' } })
```

## What happens when you save

Saving recreates that environment's Auth with the new settings; other environments are not touched. Users, sessions and every row stay, because Auth keeps them in the environment's database. The section shows **Applying…**, then **Applied**. If Auth does not start with the new settings, the previous Auth keeps running and the section says **Not applied**.

The client secret is written to a private file on the server and handed to that environment's Auth. The console never shows it again: an empty secret field keeps the saved one.

Email links and OAuth steps reach Auth without an API key, as they do on Supabase, because a browser following a link cannot send one. Only those steps are open: `verify`, `authorize` and `callback`.

CI saves a GitHub provider on a clean machine with every change, starts a GitHub sign-in and opens an email link the way a browser does, and checks that email sign-up still works afterwards ([evidence](../evidence/docker-sign-in-checks.json)). No real provider is contacted there, so the first real sign-in with your provider is the one to try yourself.
