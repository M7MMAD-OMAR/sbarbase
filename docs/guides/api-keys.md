[العربية](api-keys.ar.md)

# API keys

Each environment has two kinds of API key, as on Supabase. Both go in the `apikey` header; supabase-js sends it for you.

| Key | Starts with | Acts as | Where it belongs |
| --- | --- | --- | --- |
| Publishable | `sb_publishable_` | `anon`, or the signed-in user | Your web page, mobile app, anywhere. Row level security decides what each visitor sees |
| Secret | `sb_secret_` | `service_role` | Only on a server: your backend, scripts, cron jobs, Edge Functions and AI agents. It bypasses row level security and can manage users |

## Create a key

1. Open the environment's page in the console and choose **API keys**.
2. Press **Create key** for a publishable key, or **Create secret key** and give it a name, such as `backend` or `agent_claude`.
3. Copy the key.
   - **A secret key is shown only once.** Sbarbase keeps only its SHA-256 hash, so nobody, the operator included, can show it again. If you lose it, create another and revoke the old one.
   - **A publishable key can be copied again later** from the same page, as on Supabase, because it is public by design. This applies to keys created since 0.5.0; an older key keeps only its hash.

Only owners and admins of the organization can create, list and revoke keys. Every creation and revocation is in the organization's audit log, without the key.

## Use a secret key

```js
import {createClient} from '@supabase/supabase-js';

const admin = createClient('https://console.example.com/<runtime>', process.env.SUPABASE_SECRET_KEY, {
  auth: {persistSession: false, autoRefreshToken: false},
});
await admin.auth.admin.createUser({email: 'someone@example.com', email_confirm: true});
const {data} = await admin.from('orders').select('*'); // every row, whatever the policies say
```

It works wherever the `service_role` key works on Supabase: REST, Auth admin (`/auth/v1/admin/...`), Storage, Realtime and Edge Functions. Behind the gateway, the key is swapped for a short-lived `service_role` token signed with the environment's current signing key, so the database, Auth, REST and Storage never see the key itself. Edge Functions are the exception: like on Supabase, a function receives the caller's headers, the key included.

If a request carries a signed-in user's token in `Authorization` as well, that token wins and the request acts as that user, as on Supabase. Sending the secret key in both `apikey` and `Authorization: Bearer` is also accepted.

## Browsers are refused

A request that looks like it comes from a browser is refused with a secret key: `401` and the message "Secret API keys cannot be used in a browser". Sbarbase looks for what every browser sends and servers do not: a `Mozilla/` user agent, an `Origin` header, or a `Sec-Fetch-` header. So a secret key pasted into a web page fails at once instead of leaking to every visitor. Use a publishable key there.

## Rotate or revoke

- **Revoke** stops a key at once, for every request and every new Realtime socket. A Realtime socket already open with it keeps working until it closes, at most a day; restart the environment's Realtime (turn it off and on) to close such sockets at once.
- To rotate without downtime: create a new secret key, deploy it where the old one was used, then revoke the old one.
- Give each server, job or agent its own secret key, so one can be revoked without the others.
- Rotating the environment's [signing key](signing-keys.md) does not change API keys: they are not JWTs.

## Through the API

```bash
# a secret key named "backend"; the answer holds the key, once
curl -X POST -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"type":"secret","name":"backend"}' https://console.example.com/management/v1/environments/$ENVIRONMENT/keys
# list (names, kinds and dates; a publishable key's value too, never a secret key's) and revoke
curl -H "authorization: Bearer $TOKEN" https://console.example.com/management/v1/environments/$ENVIRONMENT/keys
curl -X DELETE -H "authorization: Bearer $TOKEN" https://console.example.com/management/v1/environments/$ENVIRONMENT/keys/$KEY_ID
```

A body is optional; with none, a publishable key named `default` is created, as before.

## Differences from Supabase

- A secret key is shown once. Supabase can reveal it again later; Sbarbase cannot, because it keeps only the hash.
- No legacy JWT keys (`anon` and `service_role` JWTs) are issued for applications.
- Supabase's exact browser test is not published; Sbarbase's is described above.
- An Edge Function called with a secret key receives the `service_role` token in `Authorization` and the raw key in `apikey`, as on Supabase. Keep functions you did not write away from secret keys.
