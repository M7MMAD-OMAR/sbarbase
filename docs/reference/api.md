[العربية](api.ar.md)

# API

Routes served by the loopback server (`lab/upstream-server.ts`). Paths starting with `/management/` are the management API, `/v1/` and `/mcp` are for agents, and everything else is the application gateway. Read from the code on 2026-10-19; the code is authoritative:

- Routing between the three groups: [src/control/application.ts](../../src/control/application.ts)
- Management metadata: [src/control/http.ts](../../src/control/http.ts)
- Keys and connection details: [src/control/key-http.ts](../../src/control/key-http.ts), dispatched by [src/control/handler.ts](../../src/control/handler.ts) with the Studio, Realtime, sign-in, Edge Functions, database, signing key, share and invitation routes beside them
- Access tokens, `/v1` and `/mcp`: [src/control/access-token-http.ts](../../src/control/access-token-http.ts), [src/control/supabase-api.ts](../../src/control/supabase-api.ts), [src/control/mcp.ts](../../src/control/mcp.ts)
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
| GET, PATCH | `/management/auth/v1/policy` | Whether the installation requires MFA, and whether the caller still needs a code. Only an installation owner who verified an authenticator code in the last five minutes may change it, with `{"mfa_required": bool}` |
| POST | `/management/auth/v1/factors` | Enroll an authenticator (TOTP) for the signed-in account |
| POST | `/management/auth/v1/factors/{id}/challenge`, `/management/auth/v1/factors/{id}/verify` | Ask for and check a code |
| DELETE | `/management/auth/v1/factors/{id}` | Remove an authenticator |

Any other `/management/auth/...` path is `404`; a wrong method is `405`. Sign-in attempts are rate limited and answer `429` past the limit.

## Management API

All routes need `Authorization: Bearer <management access token>`. The actor comes from that token, never from the body. IDs are UUIDs. Errors: `400` invalid input, `401` no valid token, `403` not allowed (also for an unknown ID), `404` unknown route, `405` wrong method, `409` conflict (environment not ready, name already used in that client or project, environment limit reached, not retryable, still holds projects or environments, provisioning or a service still active); nothing was changed, `503` identity check unavailable, `500` sanitized internal error.

| Method | Path | Who | Result |
|---|---|---|---|
| GET | `/management/v1/organizations` | any operator | The caller's clients (organizations) with ID, name and role, and `operator: true` when the caller may create clients |
| POST | `/management/v1/organizations` | owner or admin of the client created at bootstrap | Body `{"name": "..."}`. `201` with `{id}`; the caller becomes its owner |
| PATCH | `/management/v1/organizations/{id}` | owner | Body `{"name": "..."}`. Renames the client |
| DELETE | `/management/v1/organizations/{id}` | owner | Deletes an empty client with its memberships and pending invitations. `409` while it has projects, and always for the client created at bootstrap |
| GET | `/management/v1/organizations/{id}/members` | owner, admin | Members of that client with their role |
| PUT | `/management/v1/organizations/{id}/members/{member}` | owner | Body `{"role": "owner"|"admin"|"viewer"}` for an existing member. `404` for someone who is not a member (adding people waits for invitations); `409` when it would leave no owner |
| DELETE | `/management/v1/organizations/{id}/members/{member}` | owner | Removes the member; their management and Studio access ends at the next request. `409` for the last owner |
| GET | `/management/v1/organizations/{id}/invitations` | owner, admin | Pending invitations: email, role, inviter, expiry; never the token |
| POST | `/management/v1/organizations/{id}/invitations` | owner, admin | Body `{"email": "...", "role": "..."}`; an admin cannot invite an owner. `201` with `{id, token, expires_at}`; the token is shown once and lasts 7 days |
| DELETE | `/management/v1/organizations/{id}/invitations/{invitation}` | owner, admin | Cancels a pending invitation |
| POST | `/management/invitations/redeem` | anyone with the token | Body `{"token": "...", "password": "..."}` creates the account (password of 12 characters or more) and joins; with a session for the invited email, `{"token": "..."}` joins. `400 This invitation is not valid` for any unknown, used, cancelled or expired token; `409` when an account exists and must sign in first; `429` after 20 failures in a minute |
| GET | `/management/v1/organizations/{id}/projects` | member | Projects of that client |
| POST | `/management/v1/organizations/{id}/projects` | owner, admin | Body `{"name": "..."}`. `201` with `{id, state: "metadata_only"}` |
| PATCH | `/management/v1/projects/{id}` | owner, admin | Body `{"name": "..."}`. `409` when the name is used in that client |
| DELETE | `/management/v1/projects/{id}` | owner | Deletes a project that has no environment; `409` otherwise |
| POST | `/management/v1/projects/{id}/move` | owner of both clients | Body `{"organization": "<id>"}`. Moves the project; its ids and runtimes stay. Queued provisioning is cancelled (retry it in the new client) and every API key of its environments is revoked. `200` with `{organization, cancelled, revoked}`; `409` on a name clash in the destination or while provisioning runs |
| GET | `/management/v1/projects/{id}/environments` | member | Environments of that project |
| POST | `/management/v1/projects/{id}/environments` | owner, admin | Body `{"name": "..."}`. `202` with `{id, state: "queued"}`; the worker provisions it |
| PATCH | `/management/v1/environments/{id}` | owner, admin | Body `{"name": "..."}`. `409` when the name is used in that project |
| DELETE | `/management/v1/environments/{id}` | owner | Removes the environment from management and revokes its keys; from then on the gateway answers `401` for its runtime. Its database, containers and files stay on the server, retained for seven days. `200 {deleted, revoked, retentionUntil}`. `409` while provisioning is queued or running, while Studio, Realtime, Edge Functions, database access, a sign-in change, a signing key rotation, or a paused or moved routing is on or in progress, and when the exact list of the environment's resources was never recorded, which on an ordinary installation is always (see below) |
| GET | `/management/v1/environments/{id}/lifecycle` | owner, admin | The deletion record: `state` (`active`, `deleting`, `deleted`, `restoring`, `purging`, `purged`), `retain_until`, the resources and what was done to each |
| POST | `/management/v1/environments/{id}/restore` | owner | Body `{"operation": "<new uuid>"}`. Brings a deleted environment back while it is retained. `202`; `409` once retention has expired |
| POST | `/management/v1/environments/{id}/purge` | owner who is an installation operator | Body `{"operation": "<new uuid>"}`. Removes a deleted environment's resources for good, only after retention and only with a complete recovery proof. `202` |
| POST | `/management/v1/environments/{id}/resume` | owner | Body `{"operation": "<uuid>"}` of the deletion, restore or purge under way. Renews its authorization so it can continue |
| GET | `/management/v1/projects/{id}/retained-environments` | owner, admin | The project's environments that are deleted or on their way |
| POST | `/management/v1/relink` | installation operator | Body `{"runtime": "e_...", "ownership": {...}}`, the `ownership` a backup's manifest records. Recreates that client, project and environment here with the same ids and runtime, and queues provisioning; `sbarbase relink` calls it ([backup and restore](../guides/backup-and-restore.md)). `409` for any conflict: a client with that name but another id, an id that belongs to something else here, a runtime in use or deleted here |
| GET | `/management/v1/environments/{id}/provision` | member | Provisioning `state`, `attempt` and `failure` if any |
| POST | `/management/v1/environments/{id}/retry` | owner, admin | No body. `202 {state: "queued"}` for a failed or cancelled environment; `409` otherwise or at the environment limit |
| GET | `/management/v1/environments/{id}/connection` | member, when ready | `{environment, apiPath: "/<runtime>", services}` |
| GET | `/management/v1/environments/{id}/keys` | owner, admin | Key metadata, and `token` for an active publishable key created since 0.5.0; never a secret key |
| POST | `/management/v1/environments/{id}/keys` | owner, admin | Optional `{type: "publishable"\|"secret", name}`; no body makes a publishable key named `default`. `201 {id, token, type, name}`; a secret key is shown only here ([API keys](../guides/api-keys.md)) |
| GET | `/management/v1/access-tokens` | signed-in person | The caller's own access tokens, never the tokens themselves |
| POST | `/management/v1/access-tokens` | signed-in person | `{name, scope?: {kind: "all"\|"organization"\|"project", id?}, read_only?, days?}` (1 to 365, 90 by default). `201 {data, token}`, the token shown once ([AI agents](../guides/agents.md)) |
| DELETE | `/management/v1/access-tokens/{id}` | signed-in person | Revokes one of the caller's tokens |
| POST | `/management/v1/access-tokens/revoke-all` | signed-in person | Revokes every token of the caller |
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
| PUT | `/management/v1/environments/{id}/share` | installation operator | Body `{"share": n}`, 1 to 24. `409` when the shares of all ready environments would exceed 64. Applies at the next request |
| GET | `/management/v1/environments/{id}/realtime` | owner, admin, when ready | Realtime `state` and `desired` ([Realtime](../guides/realtime.md)) |
| PUT | `/management/v1/environments/{id}/realtime` | owner, admin, when ready | `{"enabled": true\|false}`. `202`; the supervisor starts or stops it. `409` while a change is in progress |
| GET | `/management/v1/environments/{id}/scheduled-jobs` | owner, admin, when ready | Scheduled jobs `state` and `desired` ([Scheduled jobs](../guides/scheduled-jobs.md)) |
| PUT | `/management/v1/environments/{id}/scheduled-jobs` | owner, admin, when ready | `{"enabled": true\|false}`. `202`; the supervisor turns them on or off. `409` while a change is in progress |
| GET | `/management/v1/environments/{id}/data-api` | owner, admin, viewer, when ready | The saved Data API settings, their `state` and `failure`, what is `served`, and the last start's `report` ([Data API](../guides/data-api.md)) |
| PUT | `/management/v1/environments/{id}/data-api` | owner, admin, when ready | `{"schemas": [...], "extraSearchPath": [...], "maxRows": n\|null}`. `202`; the supervisor applies it. `400` with the reason for a refused name, `409` while a change is in progress |
| GET | `/management/v1/environments/{id}/sign-in` | owner, admin, when ready | Sign-in settings without provider secrets, their state and the `callback_url` to give OAuth providers ([sign-in](../guides/sign-in.md)) |
| PUT | `/management/v1/environments/{id}/sign-in` | owner, admin, when ready | New settings. `202`; the supervisor recreates only that environment's Auth. `400` names the setting to check |
| GET | `/management/v1/environments/{id}/studio` | owner, admin, when ready | Studio `state` and whether a `public` Studio address is set ([Studio](../guides/studio.md)) |
| POST, DELETE | `/management/v1/environments/{id}/studio` | owner, admin, when ready | No body. Start or stop Studio; `202` |
| POST | `/management/v1/environments/{id}/studio/session` | owner, admin, when ready | No body. `201 {host, public, path}`: the Studio address and a one-time entry ticket. `409` while Studio is not running |
| GET, POST, PUT | `/management/v1/environments/{id}/deployment-setup` | installation operator, when ready | A prepared public deployment plan (address, DNS records, TLS, site URL, redirects). `POST` previews, `PUT` with `If-Match: <revision>` saves (`202`). It is preparation only: nothing is applied to the runtime |
| GET | `/management/v1/server` | installation operator | The server snapshot the console shows: CPU cores, load, uptime, memory, swap and disk ([console design](console-design.md)). Not cached |
| GET | `/management/v1/notifications/telegram` | installation operator | `state` (`off`, `pending` or `linked`), the token's last four characters and `owner`; only an owner sees the pending link |
| PUT | `/management/v1/notifications/telegram` | installation owner | Body `{"token": "..."}`. Checks the bot token with Telegram and keeps it ([configuration](configuration.md#operator-notifications)) |
| POST | `/management/v1/notifications/telegram/link`, `/management/v1/notifications/telegram/test` | installation owner | No body. Bind the chat that opened the one-time link, or send a test message |
| DELETE | `/management/v1/notifications/telegram` | installation owner | Disconnects and forgets the token |

Request bodies accept exactly the fields listed for the route, at most 4 KiB, read within five seconds. Key and connection routes and every `DELETE` refuse any body. Responses are not cacheable. A move revokes API keys but does not rotate the environments' JWT signing key or direct database password; rotate them ([signing key](../guides/signing-keys.md)) when people of the old client must lose every kind of access. Deleting an environment does not free its slot in the worker's environment limit, because its runtime is kept. Deleting an environment is soft, and works on an ordinary installation. The server first derives the environment's exact inventory from facts it can verify at that moment, and never by a pattern across environments: its containers `sbarbase-durable-<runtime>-<service>` (Auth and REST, plus Realtime, Functions or Studio ones if they exist) carrying the durable runtime's owner label, the database and the logins named by its runtime id, its entry in the runtime secret, its Storage tenant record and its published endpoints entry. A new environment's inventory is also recorded when its provisioning settles, and is checked again at deletion. When anything is ambiguous or foreign (a container with the environment's prefix and another owner or an unknown name, a retained or recovery label, a login with its prefix that the platform does not create, a privileged login, a running optional service, a missing required piece) the answer is `409` with `Exact ownership inventory unavailable` and a closed `reason` such as `container_foreign:rest`, and nothing changes. Otherwise the answer is `200` and access is revoked at once; the worker then stops the environment's own containers, closes its own database logins and unpublishes its endpoints entry, so that nothing resumes it at the next start. Each step records its intent before it acts and is repeated safely after a crash, so an interrupted delete is finished and never leaves the environment half deleted. Nothing is destroyed: the database, the Storage tenant and its files, the secrets and the stopped containers stay for the seven day retention, and the shared PostgreSQL cluster, the Storage process, other environments and the retained source and recovery containers are never touched. Restore reverses exactly what delete recorded. Purge still answers `409` with "Shared writer isolation unavailable", because it would drop objects of the shared cluster and files of the shared Storage process, and nothing yet proves that only this environment writes them.

## Agents: the Supabase-compatible API and MCP

With an access token as `Authorization: Bearer sbp_...`: `/v1/...` answers the part of Supabase's Management API that agents and tools use, and `POST /mcp` runs Supabase's own MCP server against it. What each does here and its limits are in [AI agents and the API](../guides/agents.md). An access token is refused by every `/management/` route. A project `{ref}` is one environment, 20 lowercase letters.

| Method | Path |
|---|---|
| GET | `/v1/organizations`, `/v1/organizations/{id}` |
| GET, POST | `/v1/projects` |
| GET | `/v1/projects/{ref}`, `/v1/projects/{ref}/health`, `/v1/projects/{ref}/api-keys/legacy` |
| GET, POST | `/v1/projects/{ref}/api-keys` |
| DELETE | `/v1/projects/{ref}/api-keys/{id}` |
| POST | `/v1/projects/{ref}/database/query` |
| GET, POST | `/v1/projects/{ref}/database/migrations` |
| GET | `/v1/projects/{ref}/advisors/security`, `/v1/projects/{ref}/advisors/performance` (always empty), `/v1/projects/{ref}/analytics/endpoints/logs` |
| GET, PUT, PATCH | `/v1/projects/{ref}/functions` |
| POST | `/v1/projects/{ref}/functions/deploy` |
| GET, DELETE | `/v1/projects/{ref}/functions/{name}`; GET `/v1/projects/{ref}/functions/{name}/body` |
| GET, POST, DELETE | `/v1/projects/{ref}/secrets` |
| GET, PATCH | `/v1/projects/{ref}/postgrest`: the exposed schemas, extra search path and row limit ([Data API](../guides/data-api.md)) |
| GET | `/v1/projects/{ref}/config/storage`; `/v1/projects/{ref}/config/database/pooler` and `/v1/projects/{ref}/branches` answer an empty list |
| POST | `/mcp` |

Deleting, pausing or restoring a project and generating types answer `501` with what to do instead; any other `/v1` path answers `404`.

## Updates

The update channel's routes ([upgrades](../guides/upgrades.md)), in [src/control/http.ts](../../src/control/http.ts) and [src/control/updates.ts](../../src/control/updates.ts). Only the installation operator (an owner or admin of the client created at bootstrap) may call them; anyone else gets `403`. The console only reads and asks: the supervisor carries out each request after judging it again, and publishes its verdicts in `.lab/upgrades/current.json`.

| Method | Path | Result |
|---|---|---|
| GET | `/management/v1/updates` | `{data}` with `current` (`version`, `commit`), `available` (the release on offer: `version`, `tag`, `commit`, `class` of `safe`, `attended`, `rebuild` or `manual`, `signed`, `reasons`, `notes` in `en` and `ar`, `changes`) or `null`, `refusals`, `skipped` and `newest` (newer releases the check passed over), `checkedAt`, `checkError`, `settings`, `timezone` (`name`, `offset`), `last` (the last upgrade and its phase), `request` (the one under way, else the last finished), `install` (`possible`, `reason`, `acknowledgement`, or `null` when nothing is on offer) and `canRollback` |
| PUT | `/management/v1/updates/settings` | Body `{"check": bool, "automatic": bool, "window": {"start": "HH:MM", "end": "HH:MM"}}`, 24-hour server local time. `200` with the saved `data` and the `timezone`. `400` with a sentence for invalid settings, and when `automatic` is on without `check` |
| POST | `/management/v1/updates/check` | No body. `202 {state: "requested"}`: the supervisor runs one check |
| POST | `/management/v1/updates/apply` | Body `{"version": "X.Y.Z", "acknowledged"?: bool}`. `202 {state: "requested"}`. `400` without a valid version. `409` with a sentence when the supervisor has not judged the release yet, nothing is on offer, the version is not the one on offer, its verdict says it cannot be installed now (with the verdict's reason), or it is an `attended` release and `acknowledged` is not `true` |
| POST | `/management/v1/updates/rollback` | No body. `202 {state: "requested"}`, or `409` with the reason when the last upgrade cannot be rolled back |

Every request route answers `409` with "Another update request is still in progress" while another request holds the slot. While a new version waits for its health checks, reads, the management sign-in, `check`, `settings` and `rollback` pass; every other management change answers `409` with a sentence saying changes are paused, and application routes answer `503` with `Retry-After: 5`. The supervisor's own health probe passes the hold on REST and Auth only, with a per-start token and never with `x-forwarded-*` headers ([src/gateway/hold-bypass.ts](../../src/gateway/hold-bypass.ts)).

## Loopback health

`GET /health` (or `HEAD`) on the loopback listener, for `127.0.0.1` or `localhost` only, needs no token. It answers `200 {"status": "ok", "held": bool}` once the process serves and both the control catalog and the key store read, and `503 {"status": "unavailable"}` otherwise; any other method is `405`. `held` says whether application traffic is held for an update. The supervisor's health round after an update starts with this route ([src/http/health.ts](../../src/http/health.ts)).

## Application gateway

```
{METHOD} /{runtime}/auth/v1/{path}
{METHOD} /{runtime}/rest/v1/{path}
{METHOD} /{runtime}/storage/v1/{path}
```

`{runtime}` is the environment's runtime ID returned as `apiPath` above. Point `supabase-js` at `<base URL>/{runtime}` with a publishable key.

- **Methods:** GET, HEAD, POST, PUT, PATCH, DELETE, and OPTIONS for a browser's preflight.
- **Browsers:** as on Supabase, a page on any domain may call the API. Every answer, refusals included, carries `Access-Control-Allow-Origin: *` and exposes `Content-Range`; a preflight gets `204` without a key. No cookies or credentials are used, so the API key and the user token still decide everything a call may do.
- **API key:** the `apikey` header must be an active publishable or secret key of that environment. A secret key is refused with `401` when the request looks like a browser's (a `Mozilla/` user agent, an `Origin` or a `Sec-Fetch-` header). The only keyless requests are GET or HEAD on Storage `object/public/...` and on `object/sign/...` with exactly one `token` query parameter, where Storage itself enforces bucket visibility and signature validity; and the Auth steps a browser reaches by a link or a redirect: GET `verify`, GET `authorize`, and GET or POST `callback`, which Auth checks itself.
- **Authorization:** a `Bearer` user token is forwarded; if absent (or equal to the API key) the environment's anonymous token is used, or a short-lived `service_role` token for a secret key.
- **Storage tenant:** chosen by the gateway from the routing record and sent as a trusted header; a client cannot choose it.
- **Body:** at most 1 MiB, read within ten seconds. A file upload to Storage (POST or PUT) is passed on as it arrives, up to the upload limit (`SBARBASE_UPLOAD_LIMIT_MB`, 50 MiB by default), and fails if it stalls for thirty seconds.

| Status | Meaning |
|---|---|
| `401` | Missing or invalid API key, a malformed `Authorization` header, or an environment that was deleted |
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
| `GET /{runtime}/realtime/v1/websocket?apikey=<publishable key>&vsn=1.0.0` with `Upgrade: websocket` | The Realtime socket supabase-js opens. The key is checked first; Realtime receives the environment's anon token instead, or a `service_role` token for a secret key sent from a server |
| `POST /{runtime}/realtime/v1/api/broadcast` | Broadcast from a server, with the `apikey` header |

With Realtime off, the socket answers `404`. A wrong or revoked key answers `401`. Every other Realtime path answers `404`.

## Edge Functions

When an environment has functions deployed ([Edge Functions](../guides/edge-functions.md)):

| Route | Meaning |
|---|---|
| `ANY /{runtime}/functions/v1/{name}[/...]` | Runs the function. With an `apikey`, the key is checked as for every service; without one, only a function deployed with `verify_jwt` off runs, and the caller's own headers reach it |

A name is letters, digits, `-` and `_`, at most 64, starting with a letter or digit; anything else, an unknown function or Edge Functions turned off answers `404`. A function that checks JWTs answers `401` to a missing or invalid token. The deadline is 150 seconds; bodies stream up to the upload limit.
