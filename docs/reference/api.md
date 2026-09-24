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
| GET | `/management/v1/environments/{id}/mail` | member | Non-secret mail state of the environment; no credential field |
| GET | `/management/v1/notifications` | owner or admin of any client | Undelivered count and recent operator events of the caller's own clients only. Events that belong to no client (installation start, worker restarts) go to owners and admins of the client created at bootstrap |

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
- **API key:** the `apikey` header must be an active publishable key of that environment. The only keyless requests are GET or HEAD on Storage `object/public/...` and on `object/sign/...` with exactly one `token` query parameter; Storage itself enforces bucket visibility and signature validity.
- **Authorization:** a `Bearer` user token is forwarded; if absent (or equal to the API key) the environment's anonymous token is used.
- **Storage tenant:** chosen by the gateway from the routing record and sent as a trusted header; a client cannot choose it.
- **Body:** at most 1 MiB, read within ten seconds.

| Status | Meaning |
|---|---|
| `401` | Missing or invalid API key, or a malformed `Authorization` header |
| `404` | Unknown environment, route or unconfigured service |
| `408` | Request cancelled by the client |
| `413` | Body over 1 MiB |
| `429` | This environment has too many requests in flight; retry later |
| `503` | Environment in maintenance, server-wide request limit reached, or routing unavailable; `retry-after: 1` where retrying helps |
| `504` | Upstream deadline exceeded |

Admission limits are per gateway process and have no queue. Realtime, Edge Functions and OAuth provider flows are not routed yet.
