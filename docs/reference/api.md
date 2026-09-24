[العربية](api.ar.md)

# API

Routes served by the loopback server (`lab/upstream-server.ts`). Paths starting with `/management/` are the management API; everything else is the application gateway. Read from the code on 2026-09-23; the code is authoritative:

- Routing between the three groups: [src/control/application.ts](../../src/control/application.ts)
- Management metadata: [src/control/http.ts](../../src/control/http.ts)
- Keys and connection details: [src/control/key-http.ts](../../src/control/key-http.ts), dispatched by [src/control/handler.ts](../../src/control/handler.ts)
- Gateway: [src/gateway/managed.ts](../../src/gateway/managed.ts), [src/gateway/handler.ts](../../src/gateway/handler.ts), [src/gateway/concurrency.ts](../../src/gateway/concurrency.ts)

The console and its static assets are served beside these routes by `lab/ui-static.ts`. The loopback server refuses any `Host` other than `127.0.0.1` or `localhost`; public exposure goes through a TLS proxy (see [server deployment](../guides/server-deployment.md)).

## Management login

Proxied to the dedicated management Auth realm, with the publishable routing key `sb_publishable_sbarbase_local_management`. Point the Supabase SDK at `<base URL>/management`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/management/auth/v1/token` | Log in (password grant) or refresh |
| GET | `/management/auth/v1/user` | Current operator |
| POST | `/management/auth/v1/logout` | Log out |
| GET | `/management/auth/v1/settings` | Auth settings |

Any other `/management/auth/...` path is `404`; a wrong method is `405`.

## Management API

All routes need `Authorization: Bearer <management access token>`. The actor comes from that token, never from the body. IDs are UUIDs. Errors: `400` invalid input, `401` no valid token, `403` not allowed (also for an unknown ID), `404` unknown route, `405` wrong method, `409` conflict (environment not ready, name already used in that client or project, environment limit reached, not retryable), `503` identity check unavailable, `500` sanitized internal error.

| Method | Path | Who | Result |
|---|---|---|---|
| GET | `/management/v1/organizations` | any operator | The caller's clients (organizations) with ID, name and role, and `operator: true` when the caller may create clients |
| POST | `/management/v1/organizations` | owner or admin of the client created at bootstrap | Body `{"name": "..."}`. `201` with `{id}`; the caller becomes its owner |
| GET | `/management/v1/organizations/{id}/members` | owner, admin | Members of that client with their role |
| PUT | `/management/v1/organizations/{id}/members/{member}` | owner | Body `{"role": "owner"|"admin"|"viewer"}` for an existing member. `404` for someone who is not a member (adding people waits for invitations); `409` when it would leave no owner |
| DELETE | `/management/v1/organizations/{id}/members/{member}` | owner | Removes the member; their management and Studio access ends at the next request. `409` for the last owner |
| GET | `/management/v1/organizations/{id}/invitations` | owner, admin | Pending invitations: email, role, inviter, expiry; never the token |
| POST | `/management/v1/organizations/{id}/invitations` | owner, admin | Body `{"email": "...", "role": "..."}`; an admin cannot invite an owner. `201` with `{id, token, expires_at}`; the token is shown once and lasts 7 days |
| DELETE | `/management/v1/organizations/{id}/invitations/{invitation}` | owner, admin | Cancels a pending invitation |
| POST | `/management/invitations/redeem` | anyone with the token | Body `{"token": "...", "password": "..."}` creates the account (password of 12 characters or more) and joins; with a session for the invited email, `{"token": "..."}` joins. `400 This invitation is not valid` for any unknown, used, cancelled or expired token; `409` when an account exists and must sign in first; `429` after 20 failures in a minute |
| GET | `/management/v1/organizations/{id}/projects` | member | Projects of that client |
| POST | `/management/v1/organizations/{id}/projects` | owner, admin | Body `{"name": "..."}`. `201` with `{id, state: "metadata_only"}` |
| GET | `/management/v1/projects/{id}/environments` | member | Environments of that project |
| POST | `/management/v1/projects/{id}/environments` | owner, admin | Body `{"name": "..."}`. `202` with `{id, state: "queued"}`; the worker provisions it |
| GET | `/management/v1/environments/{id}/provision` | member | Provisioning `state`, `attempt` and `failure` if any |
| POST | `/management/v1/environments/{id}/retry` | owner, admin | No body. `202 {state: "queued"}` for a failed or cancelled environment; `409` otherwise or at the environment limit |
| GET | `/management/v1/environments/{id}/connection` | member, when ready | `{environment, apiPath: "/<runtime>", services}` |
| GET | `/management/v1/environments/{id}/keys` | owner, admin | Key metadata only, never key material |
| POST | `/management/v1/environments/{id}/keys` | owner, admin | No body. `201` with a new publishable key, shown once |
| DELETE | `/management/v1/environments/{id}/keys/{keyId}` | owner, admin | `200 {revoked: true}`, or `404` if not an active key of this environment |
| GET | `/management/v1/environments/{id}/metrics` | member, when ready | Last hour at the gateway: `window` (`requests`, `clientErrors`, `serverErrors`, `p50` and `p95` in ms, `services` counts), `perMinute` (60 rows), `since`, and `services` (memory and processor use of Auth, REST, Realtime and Edge Functions). In memory; empty after a restart ([logs and metrics](../guides/logs-and-metrics.md)) |
| GET | `/management/v1/environments/{id}/logs?source=requests\|auth\|rest\|storage\|realtime\|functions&lines=1-1000&errors=1` | owner, admin, when ready | `requests`: the last gateway requests, newest first, without query strings. Other sources: `lines`, the service's last lines, oldest first, with keys, tokens and passwords replaced by `[redacted]`; Storage lines only when they name this environment. `503` when Docker cannot be reached |
| GET | `/management/v1/environments/{id}/functions` | owner, admin, when ready | Edge Functions `state` and `desired` (as for Realtime), the deployed `functions` with `verify_jwt`, `updated_at`, `size` and `path`, and the names of the `secrets` (never their values) |
| PUT | `/management/v1/environments/{id}/functions` | owner, admin, when ready | `{"enabled": true\|false}`. `202`; the supervisor starts or stops the environment's runtime |
| PUT | `/management/v1/environments/{id}/functions/{name}` | owner, admin, when ready | Deploy: `{"files": {"index.ts": "..."}, "shared"?: {...}, "verify_jwt"?: bool}`, text files at relative paths, at most 500 files and 10 MiB. `201`; the first deploy turns Edge Functions on |
| DELETE | `/management/v1/environments/{id}/functions/{name}` | owner, admin, when ready | Removes the function; `404` if there is none |
| PUT | `/management/v1/environments/{id}/function-secrets` | owner, admin, when ready | `{"secrets": {"NAME": "value" \| null}}`; `null` removes. Names are `A-Z`, digits and `_`, not starting `SUPABASE_` or `SB_`. Answers the names only |
| GET | `/management/v1/environments/{id}/database` | owner, admin, when ready | Direct database access `state` and `desired`, the `connection` (host, port, user, database) and a `url` with a password placeholder |
| PUT | `/management/v1/environments/{id}/database` | owner, admin, when ready | `{"enabled": true\|false}`. `202`; turning it on answers a new `password` and full `url` once |
| POST | `/management/v1/environments/{id}/database/password` | owner, admin, when ready | A new password, answered once; the old one stops working. `409` while access is off |
| GET | `/management/v1/environments/{id}/signing-key` | owner, admin, when ready | The JWT signing key's `state` (`never`, `pending`, `done`, `failed`), `failure` and `rotatedAt`; never the key |
| POST | `/management/v1/environments/{id}/signing-key/rotate` | owner, admin, when ready | A new signing key ([signing key](../guides/signing-keys.md)). `202`; `409` while a rotation is pending |
| GET | `/management/v1/environments/{id}/mail` | member | Non-secret mail state of the environment; no credential field |
| GET | `/management/v1/notifications` | owner or admin of any client | Undelivered count and recent operator events of the caller's own clients only. Events that belong to no client (installation start, worker restarts) go to owners and admins of the client created at bootstrap |
| GET | `/management/v1/organizations/{id}/audit` | owner, admin | What happened in that client, newest first (up to 100): time, actor, action, subject name and a short detail. A project moved in from another client shows only what happened since it arrived |
| GET | `/management/v1/environments/{id}/share` | member | The environment's guaranteed gateway share, the default and the ceiling; installation operators also get `total` and `allocated` |
| PUT | `/management/v1/environments/{id}/share` | installation operator | Body `{"share": n}`, 1 to 24. `409` when the shares of all ready environments would exceed 32. Applies at the next request |

Request bodies accept only `name`, at most 4 KiB, read within five seconds. Key and connection routes refuse any body. Responses are not cacheable. There are no routes for changing members or transferring projects; those stay internal until invitations and revocation of a moved environment's keys exist.

## Application gateway

```
{METHOD} /{runtime}/auth/v1/{path}
{METHOD} /{runtime}/rest/v1/{path}
{METHOD} /{runtime}/storage/v1/{path}
```

`{runtime}` is the environment's runtime ID returned as `apiPath` above. Point `supabase-js` at `<base URL>/{runtime}` with a publishable key.

- **Methods:** GET, HEAD, POST, PUT, PATCH, DELETE, and OPTIONS for a browser's preflight.
- **Browsers:** as on Supabase, a page on any domain may call the API. Every answer, refusals included, carries `Access-Control-Allow-Origin: *` and exposes `Content-Range`; a preflight gets `204` without a key. No cookies or credentials are used, so the API key and the user token still decide everything a call may do.
- **API key:** the `apikey` header must be an active publishable key of that environment. The only keyless requests are GET or HEAD on Storage `object/public/...` and on `object/sign/...` with exactly one `token` query parameter, where Storage itself enforces bucket visibility and signature validity; and the Auth steps a browser reaches by a link or a redirect: GET `verify`, GET `authorize`, and GET or POST `callback`, which Auth checks itself.
- **Authorization:** a `Bearer` user token is forwarded; if absent (or equal to the API key) the environment's anonymous token is used.
- **Storage tenant:** chosen by the gateway from the routing record and sent as a trusted header; a client cannot choose it.
- **Body:** at most 1 MiB, read within ten seconds. A file upload to Storage (POST or PUT) is passed on as it arrives, up to the upload limit (`SBARBASE_UPLOAD_LIMIT_MB`, 50 MiB by default), and fails if it stalls for thirty seconds.

| Status | Meaning |
|---|---|
| `401` | Missing or invalid API key, or a malformed `Authorization` header |
| `404` | Unknown environment, route or unconfigured service |
| `408` | Request cancelled by the client |
| `413` | Body over 1 MiB, or an upload over the upload limit |
| `429` | This environment is at its share and cannot borrow more right now, or at its ceiling; retry later |
| `503` | Environment in maintenance, server-wide request limit reached, or routing unavailable; `retry-after: 1` where retrying helps |
| `504` | Upstream deadline exceeded |

Admission limits are per gateway process and have no queue.

## Realtime

When an environment has Realtime turned on ([Realtime](../guides/realtime.md)):

| Route | Meaning |
|---|---|
| `GET /{runtime}/realtime/v1/websocket?apikey=<publishable key>&vsn=1.0.0` with `Upgrade: websocket` | The Realtime socket supabase-js opens. The key is checked first; Realtime receives the environment's anon token instead |
| `POST /{runtime}/realtime/v1/api/broadcast` | Broadcast from a server, with the `apikey` header |

With Realtime off, the socket answers `404`. A wrong or revoked key answers `401`. Every other Realtime path answers `404`.

## Edge Functions

When an environment has functions deployed ([Edge Functions](../guides/edge-functions.md)):

| Route | Meaning |
|---|---|
| `ANY /{runtime}/functions/v1/{name}[/...]` | Runs the function. With an `apikey`, the key is checked as for every service; without one, only a function deployed with `verify_jwt` off runs, and the caller's own headers reach it |

A name is letters, digits, `-` and `_`, at most 64, starting with a letter or digit; anything else, an unknown function or Edge Functions turned off answers `404`. A function that checks JWTs answers `401` to a missing or invalid token. The deadline is 150 seconds; bodies stream up to the upload limit.
