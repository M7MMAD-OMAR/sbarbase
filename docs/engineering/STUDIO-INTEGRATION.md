# Integration specification: upstream Supabase Studio plus postgres-meta as the per-environment administration surface

Status: 2026-09-21. Read-only study. No container was started, stopped or created. Nothing in
`/home/sbarah/R/Projects/P/sbarbase` was modified. `.secrets/` and `.lab/` were not read.

This document is the design record for the administration-surface redirect. The decision itself is
in [DECISIONS](../decisions/README.md) and the dated entries in [checkpoints](checkpoints.md).
Update 2026-09-24: the on-demand path is implemented. Studio and postgres-meta start per environment
through the management API, are served on `<id>.studio.localhost` behind a signed console session, and
use a per-start `<e>_studio` login (not a superuser, BYPASSRLS, read only on `auth` and `storage`). Code:
`lab/studio.py`, `src/control/studio.ts`; check: `lab/studio-check.ts` in the CI Docker job; operator
guide: [Studio](../guides/studio.md). Sections below remain the original study.

## 0. Evidence base, and how to read the labels

Labels used in this document:

- VERIFIED: read directly from a file or an upstream source in this session, quoted with its path.
- MEASURED (given): measured on this host by the user's agent before this task and supplied as
  input, not re-measured here.
- INFERRED: a conclusion drawn from the verified facts, not itself observed.

What was read:

| Source | What it establishes |
|---|---|
| `/tmp/sb-compose.yml` (upstream self-hosting compose copy) | the exact `studio`, `meta`, `api-gw` and `db` service environment variable names (VERIFIED) |
| `apps/studio` on `supabase/supabase` master: `lib/api/self-hosted/constants.ts`, `util.ts`, `query.ts`, `settings.ts`, `lib/constants/api.ts`, `lib/constants/index.ts`, `pages/_app.tsx`, `next.config.ts` | how Studio builds its connection string, which variables it reads, how it encrypts and sends them, that `BASE_PATH` exists (VERIFIED, but master, not the measured image) |
| `postgres-meta` master: `src/server/constants.ts`, `routes/index.ts`, `utils.ts`, `README.md` | the meta environment variable names, the per-request `x-connection-encrypted` decryption, the fallback connection, the body limit, that meta has no authentication (VERIFIED) |
| `apps/docs/content/guides/self-hosting/docker.mdx` and `remove-superuser-access.mdx` | dashboard access is HTTP basic auth via `DASHBOARD_USERNAME`/`DASHBOARD_PASSWORD`; `PG_META_CRYPTO_KEY` must be at least 32 characters; Logs and Analytics are an optional override (VERIFIED) |
| `docker/docker-compose.logs.yml` | `ENABLED_FEATURES_LOGS_ALL` is the mechanism that enables the log surfaces (VERIFIED); the base compose sets it to `false` |
| the repository: `lab/durable_runtime.py`, `lab/run.py`, `lab/connection_budget.py`, `lab/source_fence.py`, `lab/recovery-export.py`, `lab/recovery-restore-db.py`, `lab/provisioning_inspection.py`, `lab/verify_retained.py`, `lab/hba_generation.py`, `lab/combined_admission.py`, `lab/installation_runtime.py`, `lab/install_server.py`, `lab/pin_update.py`, `src/gateway/handler.ts`, `src/control/*.ts`, `ui/api.ts`, `ui/Connection.tsx`, `docs/*` | the full sbarbase side (VERIFIED) |

Images, as given by the user's agent (MEASURED, given): `public.ecr.aws/supabase/studio:2026.07.27-sha-cbb076d`
and `public.ecr.aws/supabase/postgres-meta:v0.96.6` were started against one existing environment
database and Studio loaded with its full navigation and a working Table Editor.

The compose copy at `/tmp/sb-compose.yml` is newer than that pair: it names
`supabase/studio:2026.09.07-sha-7996410` and `supabase/postgres-meta:v0.99.0`, and a `db` image of
`supabase/postgres:17.6.1.136` where sbarbase pins `17.6.1.166`. The version that is adopted has to
be one of these on purpose, not by copying the compose file.

## 1. The container set

### 1.1 Recommended: one Studio and one postgres-meta per environment

Add two containers per environment, named after the runtime identifier and following the existing
`PREFIX = 'sbarbase-durable'` convention (`lab/durable_runtime.py:29`):

```
sbarbase-durable-<e>-meta      # postgres-meta v0.96.6 (or the chosen pin)
sbarbase-durable-<e>-studio    # studio 2026.07.27-sha-cbb076d (or the chosen pin)
```

with label `io.sbarbase.owner=durable-upstream` and membership of the existing internal network
`sbarbase-durable-net`. No published ports, matching every other service in this runtime.

Why per environment, on the evidence:

1. Self-hosted Studio is single project by construction. `apps/studio/lib/api/self-hosted/constants.ts`
   reads one `POSTGRES_DB`, one `POSTGRES_PASSWORD`, one `POSTGRES_USER_READ_WRITE` and one
   `PGRST_DB_SCHEMAS` for the whole process, and `getConnectionString` in
   `lib/api/self-hosted/util.ts` builds one string from them. There is no project selector in the
   self-hosted build. One Studio process therefore serves one environment.
2. postgres-meta is multi-tenant (its README says so explicitly), so meta could in principle be
   shared. The reason not to share it is the key, not the process: Studio sends the connection
   string to meta in the `x-connection-encrypted` header, encrypted with `PG_META_CRYPTO_KEY`
   (`lib/api/self-hosted/util.ts` `encryptString`), and meta decrypts it with `CRYPTO_KEY`
   (`postgres-meta/src/server/routes/index.ts`, the `onRequest` hook). VERIFIED on both sides. That
   key is therefore an authorization boundary: whoever holds it can present any connection string
   to meta. A key shared by all environments moves the environment boundary from "his password,
   enforced by HBA" to "his password plus one installation-wide secret", and a single meta process
   can hold exactly one fallback connection (`PG_META_DB_*`, used when the header is absent).
   A separate key and a separate fallback per environment keeps the blast radius equal to one
   environment.
3. The measured blocker agrees with this reading. meta using the cluster superuser was refused
   because the environment database only accepts its own exact login/database pair (MEASURED, given),
   and with the environment's own scoped REST login the Authentication page failed with
   `permission denied for schema auth` (MEASURED, given). Both are the HBA and grant model doing its
   job; the fix belongs per environment.

Container-level facts to reuse: Studio listens on 3000 and its own compose healthcheck is
`node -e "fetch('http://localhost:3000/api/platform/profile')..."` expecting 200; meta listens on
`PG_META_PORT` (8080 in the compose, 1337 is its code default) and exposes `/health` and `/` for a
readiness probe. Both are VERIFIED from the sources above.

### 1.2 Alternative: one shared Studio, or one shared meta, with routing

Shared meta (one `sbarbase-durable-meta`, one `CRYPTO_KEY`) with one Studio per environment:

- Upstream-supported (meta is multi-tenant), saves one small process per environment.
- Requires the shared meta's fallback connection to be deliberately unusable, because the fallback is
  a real credential that any caller without the header would use. Verified: `PG_CONNECTION` is built
  from `PG_META_DB_*` and is used whenever `x-connection-encrypted` is absent.
- Requires accepting one installation-wide key as the boundary. Test T3 below is written to attack
  exactly this.
- Saves only the meta container (meta is small, see section 8). Does not save the Studio, which is
  the expensive one.

Shared Studio (one process, several environments) is not available in this distribution: it is
single project, as shown above. Reaching multi-environment from one shared Studio would mean editing
`apps/studio` and maintaining a fork, which `docs/engineering/reviews/alternatives-product.md:50` already treats
as a third-party project to evaluate and not a drop-in. Risk stated plainly: a fork of the
administration surface is a permanent maintenance obligation against a fast-moving upstream, and it
would put every environment's credentials behind one process.

Recommendation: per-environment Studio plus per-environment meta. Take the shared meta only with test
T3 passing and the fallback closed.

### 1.3 The admission arithmetic does not currently allow it

VERIFIED numbers, recomputed from the sources:

- `lab/combined_admission.py:11-13`: `MAX_MEMORY=6*1024**3` (6144 MiB), `MAX_CPUS=6`, `RESERVE=2560 MiB`.
- The placement it sums is an explicit container name list (`CombinedAdmission.__init__`): the source
  db (1024 MiB, 1 CPU), shared Storage (512 MiB, 0.5), management Auth (256 MiB, 0.25), each
  environment's Auth and REST (256 MiB / 0.25 each, `lab/durable_runtime.py:311`), and the recovery
  target's db/auth/rest/storage.
- With four environments that is 5888 MiB and 5.75 CPUs, which is exactly the figure in
  `docs/guides/server-deployment.md:24` and `lab/install_server.py:31-32` (`PLANNED_MIB=5888`,
  `RESERVE_MIB=2560`).

Adding two containers per environment changes it twice over:

1. The name list would not include them, so the ceiling check would under-count and a combined start
   could exceed the ceiling silently. That is a code change, not a policy change.
2. Even a modest Studio (512 MiB, 0.5 CPU) plus meta (128 MiB, 0.25 CPU) at four environments adds
   2560 MiB and 3 CPUs, taking the placement to 8448 MiB and 8.75 CPUs. That is over both constants
   and over the installed headroom figure of about 8.8 GiB (`docs/guides/server-deployment.md`, which adds
   the 2560 MiB reserve).

So under the current constants a per-environment Studio is refused, not started. Three honest
options, in order of preference:

1. Do not run Studio as part of the always-on placement. Start it on demand for one environment at a
   time through an explicit operator command, with the same admission check applied, and stop it
   when done. This matches the way the lab is already used ("stop the lab between test sessions to
   preserve workstation headroom", `lab/README.md`) and keeps the ceiling and the installed headroom
   figure unchanged.
2. Raise `MAX_MEMORY`, `MAX_CPUS`, `RESERVE`, `PLANNED_MIB` and the `RESERVE_MIB` in
   `lab/install_server.py`, and the documented headroom, after measuring. That is a capacity
   decision for the operator, not a detail.
3. Use the shared meta variant to pay for one container per environment instead of two.

## 2. Environment variables, with their source in sbarbase today

### 2.1 Studio

Names VERIFIED from `/tmp/sb-compose.yml` (studio service) and from the Studio source. Values are the
sbarbase equivalents, not the compose defaults.

| Variable | Value in sbarbase | Where the value comes from today |
|---|---|---|
| `HOSTNAME` | `0.0.0.0` | compose literal, keep |
| `STUDIO_PG_META_URL` | `http://sbarbase-durable-<e>-meta:8080` | new, derived from the container name and `PG_META_PORT` |
| `POSTGRES_HOST` | `sbarbase-durable-db` | `lab/durable_runtime.py:30` (`DB`), the same host every environment's Auth and REST already use (`f'@{DB}:5432/{e}'`, `lab/run.py:126,135`) |
| `POSTGRES_PORT` | `5432` | VERIFIED from `lab/run.py:126` and from the compose `db` service default |
| `POSTGRES_DB` | `<e>`, the runtime identifier, for example `e_f61bf85dccd73890ec63997c` | the environment database name, created by `lab/provision_environment` and used by every scoped login |
| `POSTGRES_PASSWORD` | the credential for the Studio login | new secret in the existing private per-environment credential set, `.secrets/upstream/runtime.json` under `environments[e]` (`lab/durable_runtime.py:287,293`). Never printed; the existing loader already refuses to log it |
| `POSTGRES_USER_READ_WRITE` | `<e>_studio` | new scoped login, section 3. Must be set explicitly: the Studio default is `supabase_admin` (`lib/api/self-hosted/constants.ts`), a cluster role that exists in this distribution but is refused by the environment's HBA |
| `POSTGRES_USER_READ_ONLY` | `<e>_studio` (or a second, read-only login) | Studio defaults to `supabase_read_only_user`, which does not exist in this cluster. Set it explicitly, see section 3.4 |
| `PG_META_CRYPTO_KEY` | at least 32 characters of randomness, one value per environment | new secret in `.secrets/upstream/runtime.json`. Generate with `openssl rand -base64 24`, per the upstream documentation. Must equal the meta container's `CRYPTO_KEY` |
| `PGRST_DB_SCHEMAS` | `public` | sbarbase's PostgREST is configured `PGRST_DB_SCHEMAS: 'public'` (`lab/run.py:136`). The upstream default is `public,graphql_public`; `graphql_public` does not exist here |
| `PGRST_DB_MAX_ROWS` | `1000` (compose default) or the adapter's own row cap | `lab/run.py` does not set a row cap for PostgREST; keep the upstream default or match it deliberately |
| `PGRST_DB_EXTRA_SEARCH_PATH` | `public` | compose default, consistent with the environment's schema |
| `DEFAULT_ORGANIZATION_NAME`, `DEFAULT_PROJECT_NAME` | the organization and environment display names | `organizations` and `environments` tables in the SQLite catalog, `src/control/catalog.ts:22,32` |
| `SUPABASE_URL` | `http://127.0.0.1:<gateway port>/<e>` | server-side calls go through the composed gateway, which serves `/<env>/auth/v1` and `/<env>/rest/v1` (`src/gateway/handler.ts:30`). supabase-js keeps the path in `supabaseUrl`, so this works for server-side calls |
| `SUPABASE_PUBLIC_URL` | the public origin of the installation | see section 5: Studio keeps only the origin of this value (`lib/constants/api.ts`: `PROJECT_REST_URL = ${PUBLIC_URL.origin}/rest/v1/`), so a path prefix here is discarded and the displayed project URL would not resolve on sbarbase unless the environment is served at the root of its own admin origin |
| `SUPABASE_ANON_KEY` | an issued publishable key for this environment | `src/control/keys.ts`, issued through `/management/v1/environments/<id>/keys` (`src/control/key-http.ts:25`). See the risk in section 3.5: this is an opaque token, and Studio builds `apikey` from it while server-side admin calls use the service key |
| `SUPABASE_SERVICE_KEY` | the environment's `service_role` JWT signed with `v['jwt']` | `lab/durable_runtime.py:320` builds exactly this token for the Storage tenant (`token(v['jwt'], 'service_role')`). See section 3.5 |
| `AUTH_JWT_SECRET` | `v['jwt']` for this environment | same private per-environment credential set; the same secret PostgREST validates with (`lab/run.py:137`) |
| `ENABLED_FEATURES_LOGS_ALL` | `"false"` | VERIFIED mechanism, and the base compose default. sbarbase has no analytics service |
| `SNIPPETS_MANAGEMENT_FOLDER` | unset (or a read-only empty directory) | sbarbase has no `/app/snippets` volume. Unset means the SQL snippets feature fails; leaving it unset is more honest than mounting a folder that nothing manages |
| `EDGE_FUNCTIONS_MANAGEMENT_FOLDER` | unset, or an empty read-only directory | the self-hosted Edge Functions code asserts this variable is set (`lib/api/self-hosted/functions`), and sbarbase has no edge runtime. See section 6 |
| `OPENAI_API_KEY` | unset | no AI features are in scope |
| `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY` | unset for now | these are upstream's newer opaque API keys. sbarbase's own opaque keys are its own format, not this format. Whether the image maps them into the API keys page is not verified |

### 2.2 postgres-meta

Names VERIFIED from `/tmp/sb-compose.yml` (meta service) and `postgres-meta/src/server/constants.ts`.

| Variable | Value | Note |
|---|---|---|
| `PG_META_PORT` | `8080` | the port Studio's `STUDIO_PG_META_URL` points at |
| `PG_META_DB_HOST` | `sbarbase-durable-db` | the fallback connection host |
| `PG_META_DB_PORT` | `5432` | |
| `PG_META_DB_NAME` | `<e>` | |
| `PG_META_DB_USER` | `<e>_studio` | must never be `postgres`, `supabase_admin`, or any cluster-wide role, see section 3 |
| `PG_META_DB_PASSWORD` | the Studio login's password | |
| `CRYPTO_KEY` | the same value as Studio's `PG_META_CRYPTO_KEY` | the variable name differs between the two containers; this is the measured blocker 2 and it is VERIFIED in both sources |

Optional meta knobs worth setting deliberately, all VERIFIED to exist in `src/server/constants.ts`:
`PG_META_MAX_BODY_LIMIT_MB` (default 3 MiB), `PG_QUERY_TIMEOUT_SECS` (default 55),
`PG_CONN_TIMEOUT_SECS` (default 15), `PG_META_DB_SSL_MODE` (`disable` here, the network is internal),
`PG_META_HOST`.

Variables that must NOT be set: `PG_META_DB_URL` (it overrides the assembled fallback),
`PG_META_EXPORT_DOCS`, `PG_META_GENERATE_TYPES`. Do not add `POSTGRES_PASSWORD` or the cluster admin
password to either container; the Studio login in this design never needs it.

### 2.3 What the gateway will do with Studio's own calls, and the strongest open risk

Studio's server-side admin client is `createClient(process.env.SUPABASE_URL!, process.env.SUPABASE_SERVICE_KEY!)`
(`apps/studio/lib/api/self-hosted-admin.ts`, VERIFIED). supabase-js puts that key in the `apikey`
header and in `Authorization: Bearer <the environment service_role JWT>`. The `service_role` key here is a JWT signed with the
environment's `jwt` secret.

The sbarbase gateway decides admission from `apikey` against the catalog's issued opaque publishable
keys, not against JWTs (`src/gateway/handler.ts:45-51`, `verifyKey` from
`src/gateway/managed.ts`/`src/control/keys.ts`). It then forwards `Authorization` unchanged only when
the bearer is not the apikey, and otherwise substitutes the environment's anonymous token
(`handler.ts:72-75`).

Therefore, INFERRED and high risk: a Studio whose `SUPABASE_SERVICE_KEY` is the service_role JWT
would send `apikey: <service_role JWT>`, which the gateway would reject with `401 Invalid API key`.
Making Studio's server-side admin calls work requires one of:

- a small gateway change to accept a service_role JWT signed with the environment's own secret as an
  admission key, which widens the data-plane admission boundary and must be treated as a security
  change with its own review, or
- configuring Studio so the key it sends as `apikey` is an issued opaque publishable key while the
  `Authorization` bearer carries the service_role JWT. In the self-hosted build both come from
  `SUPABASE_ANON_KEY`/`SUPABASE_SERVICE_KEY`, so this needs the image's actual behaviour to be
  checked, not assumed.

This is the single item to test first (test T11) because it decides whether the Authentication and
Storage admin pages can work at all. It was not observable in the user's measurement, which loaded
the Table Editor, a path that goes through postgres-meta and not through the gateway.

## 3. The scoped login, its privileges, and the exact changes

### 3.1 Reusing an existing login is not enough, and was measured to fail

- The cluster superuser was refused: the environment's HBA admits only its own exact login/database
  pairs and ends with a blanket reject (MEASURED, given; the same structure is VERIFIED in
  `lab/durable_runtime.py:159-168`).
- The environment's own `<e>_rest` login loaded the schema list but failed the Authentication page
  with `permission denied for schema auth` (MEASURED, given). The cause is VERIFIED in the code:
  `<e>_rest` is created `LOGIN NOINHERIT` (`lab/run.py:109`), and the `USAGE ON SCHEMA auth` grant
  goes to the group roles `anon, authenticated, service_role` (`lab/run.py:116`), which a NOINHERIT
  login does not implicitly use. PostgREST is unaffected because it uses `SET ROLE`, which NOINHERIT
  does not block.

Reusing `<e>_rest` is also wrong on design grounds: it is the data-plane login that application
traffic flows through, and widening its grants widens the application's blast radius.

### 3.2 Create a fourth scoped login

Add one login per environment, created the same way as the others:

```
CREATE ROLE <e>_studio LOGIN NOINHERIT PASSWORD '<32 byte hex from secrets.token_hex(32)>';
```

Placement in code: `lab/durable_runtime.py:provision_database` (lines 256-267) is where `<e>_storage`
is created today; the same shape applies. The credential belongs in the per-environment private set
next to `auth`, `rest`, `storage`, `jwt` (`lab/durable_runtime.py:293`), and the effect-receipt
claim and the export and restore paths must learn the fourth key.

### 3.3 HBA

The published file is assembled in one place, `lab/durable_runtime.py:159-168` (`Runtime.hba`):

```
local all supabase_admin trust
host storage_metadata storage_control 0.0.0.0/0 scram-sha-256
host management management_auth 0.0.0.0/0 scram-sha-256
host <e> <e>_auth    0.0.0.0/0 scram-sha-256
host <e> <e>_rest    0.0.0.0/0 scram-sha-256
host <e> <e>_storage 0.0.0.0/0 scram-sha-256
host all all 0.0.0.0/0 reject
host all all ::/0 reject
```

The change is to add, inside the per-environment block and before the two reject lines:

```
host <e> <e>_studio 0.0.0.0/0 scram-sha-256
```

and to extend the role tuple in the loop (`for role in ('auth', 'rest', 'storage')` becomes
`('auth', 'rest', 'storage', 'studio')`).

A new rule is a new publication under the same generation pin, not a new generation: the pin binds
the container identity, and publication is a compare-and-swap on the file digest
(`lab/hba_generation.py`, `lab/hba_authority.py`, `lab/atomic_hba.py`). Retained containers therefore
keep their pin, but every recorded digest and rule inventory that mentions the file becomes stale,
which is section 9.

### 3.4 Grants for `<e>_studio`

Minimum set to make the pages that were measured to fail, work, and to keep the Table Editor
functional. Statement timeouts match the existing REST posture (`lab/durable_runtime.py:254`).

```
ALTER ROLE <e>_studio CONNECTION LIMIT 6;
ALTER ROLE <e>_studio IN DATABASE <e> SET search_path TO public, extensions;
ALTER ROLE <e>_studio IN DATABASE <e> SET statement_timeout = '8s';
ALTER ROLE <e>_studio IN DATABASE <e> SET transaction_timeout = '12s';
GRANT CONNECT ON DATABASE <e> TO <e>_studio;

GRANT USAGE ON SCHEMA public, auth, storage, extensions TO <e>_studio;
GRANT SELECT ON ALL TABLES IN SCHEMA auth, storage, extensions TO <e>_studio;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO <e>_studio;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO <e>_studio;
```

For a write-capable Table Editor, add, and only if the operator wants it:

```
GRANT INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO <e>_studio;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO <e>_studio;
```

Two consequences to state rather than discover:

- Grants are direct, not via `anon`/`authenticated`/`service_role` membership, because the login is
  NOINHERIT. That is deliberate: membership in `service_role` would make the login BYPASSRLS through
  the group, which is exactly what must not happen.
- Grants are on existing objects only. New tables created later by application migrations will not be
  visible to Studio unless the environment adds default privileges for this role, for example
  `ALTER DEFAULT PRIVILEGES FOR ROLE <e>_auth IN SCHEMA public GRANT SELECT ON TABLES TO <e>_studio`.
  Whether that is wanted is an operator decision; without it Studio silently shows stale content.

Read-only versus read-write logins. `getConnectionString({ readOnly })` picks
`POSTGRES_USER_READ_ONLY` or `POSTGRES_USER_READ_WRITE` (`lib/api/self-hosted/util.ts`, VERIFIED), so
both variables must name a role that exists. If one login is used for both, the read-only intent is
not enforced anywhere. A second login `<e>_studio_ro` with the SELECT grants only is the honest
option, at the cost of a fifth HBA rule and a fifth role per environment.

### 3.5 What must NOT be granted or set

- Not `SUPERUSER`, not `CREATEDB`, not `CREATEROLE`, not `REPLICATION`, not `BYPASSRLS`. The export
  path already asserts exactly this shape for the existing logins (`lab/recovery-export.py:109`).
- Not membership in `service_role`, `supabase_admin`, `authenticator` or any `pg_*` predefined role.
- Not `CREATE` on the database (PostgreSQL 15+ grants it to PUBLIC by default and this cluster
  revokes it, `lab/run.py:115`) and not `CREATE` on schema `public` (`lab/run.py:116`).
- Not ownership of any schema or database. Ownership would let Studio's SQL editor change the
  environment's authorization model, and in this cluster the schemas deliberately belong to the
  scoped service logins (`lab/run.py:116`, `lab/durable_runtime.py:267`).
- Not `CONNECT` on `management` or `storage_metadata`; those have their own single scoped logins
  (`lab/durable_runtime.py:200-203, 347`). The HBA blanket reject is the second line of defence and
  must stay last.
- Not a fallback `PG_META_DB_USER` of `postgres` or `supabase_admin` on the meta container. The
  fallback is a live credential used on any request without `x-connection-encrypted`.
- Not the cluster admin password in either container, and not the environment `jwt` secret in
  Studio's `PG_META_CRYPTO_KEY` by reuse. Generate a separate key.
- Not a shared `PG_META_CRYPTO_KEY` across environments unless the shared-meta variant is taken
  deliberately and T3 passes.

### 3.6 Connection budget

`lab/connection_budget.py` currently has `SERVICE_LIMIT = 6`, `ENVIRONMENT_LIMIT = 3*SERVICE_LIMIT`
(18), and the admission formula `environments*18 + 12 + 10 <= max - superuser_reserved - reserved`.
`lab/durable_runtime.py:264` applies `ALTER DATABASE <e> CONNECTION LIMIT 18`. A fourth login means
either borrowing from the same 18 or raising `ENVIRONMENT_LIMIT` to `4*SERVICE_LIMIT = 24` and
updating the formula and `docs/engineering/CONNECTION-BUDGET.md`. meta uses a pool of one connection per
connection string by default (`DEFAULT_POOL_CONFIG`, `max: 1`, VERIFIED), so its real demand is small,
but the declared limit is what the admission math counts.

## 4. How access to Studio is authenticated

Facts:

- Upstream, Studio has no login of its own. The dashboard is protected by HTTP basic authentication
  at the gateway, configured with `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD` (VERIFIED in
  `apps/docs/.../docker.mdx` and in the `api-gw` service of `/tmp/sb-compose.yml`, which carries
  both variables). Studio itself accepts anything that reaches its port, which is consistent with
  the MEASURED observation that it has no login screen when reached directly. meta is worse: its
  README states "What security does this use? None. Please don't use this as a standalone server."
- sbarbase has no equivalent gate yet, and no session cookie to reuse. The console holds a
  management access token in memory: `ui/api.ts` creates the client with
  `auth: { persistSession: false, autoRefreshToken: true, detectSessionInUrl: false }` and adds
  `authorization: 'Bearer ' + token` to each `fetch`. VERIFIED. There is no `document.cookie` path,
  and a bearer token cannot be attached to a top-level browser navigation to Studio.

Options:

- A. Per-environment basic auth at the admin proxy (recommended first step). One credential per
  environment, generated with the environment's other secrets, stored in `.secrets/upstream`,
  never printed, compared with `timingSafeEqual` (the helper already exists in
  `src/gateway/handler.ts:15`). The proxy answers `401` with `WWW-Authenticate: Basic` and sets no
  cookie. It mirrors upstream exactly, it is per environment, and it needs no new session store.
  Its costs: credentials are typed once per browser, they can be cached by the browser, and there is
  no per-request identity in the access log beyond the fact that the gate passed.
- B. Gate on the management session. Requires new surface: the console would have to mint an
  HttpOnly, Secure, SameSite=Lax cookie at login (signed with an installation key), and the admin
  proxy would verify it, or call the management Auth `/user` endpoint with the bearer taken from
  that cookie. The management Auth realm already exists and is reachable only through the loopback
  gateway (`src/control/application.ts:39-47`, `/management/auth/v1/*`), so the verification path is
  real; only the cookie issuing and checking is new. This gives one login for the console and Studio
  and real per-user identity, at the price of inventing a session mechanism that the console
  deliberately does not have today.
- C. No gate, loopback only, reach Studio through an SSH tunnel. Cheapest and the most honest for a
  single operator; no link from the console works, and there is no per-environment separation
  beyond the port.

Recommendation: A first, with C acceptable for the first rehearsal, and B only if the console
surface gains a cookie for other reasons. Do not ship Studio reachable on a port with no gate.

## 5. Routing

### 5.1 Studio cannot ride the existing data-plane gateway unchanged

VERIFIED in `src/gateway/handler.ts`:

- The route pattern is narrow: `^/([a-z][a-z0-9_]{1,30})/(auth|rest|storage)/v1(/.*)?$` (line 30).
  There is no room for a Studio page path, and Studio needs many paths (`/_next/*`, `/api/*`,
  `/project/default/*`).
- The forwarded header allowlist (line 14) has `accept`, `content-type`, `prefer`, `range`,
  `range-unit`, `accept-profile`, `content-profile`, `x-client-info`, `x-upsert`, `cache-control`,
  `if-none-match`, `if-modified-since`. There is no `cookie`, `referer`, `origin`, `accept-language`
  or `sec-fetch-*`. Studio, being a Next.js app, needs several of those; a session or CSRF cookie
  would be dropped.
- Bodies are capped at 1 MiB (line 83) and upstream calls time out at 15 s (line 112). meta's own
  body limit defaults to 3 MiB and its query timeout to 55 s. A SQL editor paste or a CSV import
  through the gateway would be rejected by the gateway, not by Studio.
- `Authorization` is replaced with the environment's anonymous token whenever the bearer equals the
  apikey (line 75). That is correct for the data plane and wrong for an admin surface with its own
  session.

### 5.2 The path-prefix trap

VERIFIED in `apps/studio/lib/constants/api.ts`:

```
const PUBLIC_URL = new URL(process.env.SUPABASE_PUBLIC_URL || 'http://localhost:8000')
export const PROJECT_REST_URL = `${PUBLIC_URL.origin}/rest/v1/`
export const PROJECT_ENDPOINT = PUBLIC_URL.host
```

Only the origin survives. A `SUPABASE_PUBLIC_URL` of `https://host/<env>` produces
`PROJECT_REST_URL = https://host/rest/v1/`, which does not exist on sbarbase, because every
environment lives under `/<env>/rest/v1`. The Settings page would display a URL that does not work,
and any client-side construction from `PROJECT_REST_URL` would call the wrong path.

`NEXT_PUBLIC_BASE_PATH` does exist (`lib/constants/index.ts` `BASE_PATH`, and `next.config.ts` sets
`basePath: process.env.NEXT_PUBLIC_BASE_PATH` and an asset prefix). It is a `NEXT_PUBLIC_` variable,
so the client bundle is built with it; whether the published image can be served under a runtime
supplied sub-path without a rebuild is NOT verified.

### 5.3 Recommended routing

R1, recommended: a dedicated admin origin per environment, not a path under the data gateway.

- Each environment's Studio is reached on its own loopback port, for example `127.0.0.1:<port>`,
  with the environment's API proxied at the root of that origin, so Studio's origin-based URL
  building is truthful, or with the public URL left pointing at the installation origin and the
  Settings page labelled as informational.
- The basic auth gate of section 4 sits in the same proxy.
- The console advertises it the way it already advertises the data path: `src/control/key-http.ts:25`
  returns `apiPath: '/<runtime>'` and a `services` list from a `ServiceDiscovery` typed
  `readonly ('auth'|'rest'|'storage')[]` (line 9). Adding `'studio'` there and rendering a link in
  `ui/Connection.tsx` (which already prints `Available services`) is a small, honest change.
- Terminate TLS in front of it. The shipped `deploy/console-tls-proxy.ts` forwards everything to one
  loopback upstream and deliberately drops client supplied `X-Forwarded-*` and `Host`
  (`docs/guides/server-deployment.md`), so it needs a second upstream and a host allowlist, or the operator
  uses their own proxy or an SSH tunnel.

R2, path mount `/<env>/studio` on the shared gateway: requires widening the route regex, adding a
header allowlist that includes cookies and fetch metadata, raising the body cap and the timeout, and
either building the image with a matching `NEXT_PUBLIC_BASE_PATH` or accepting broken asset paths.
Highest risk, and it puts an admin surface and a cookie on the same handler that enforces the data
plane's API key rule. Not recommended.

R3, host based routing (`studio-<env>.<host>`): the more standard shape and it survives Studio's
origin-only URL building, but it needs the same TLS proxy work as R1 plus DNS or host entries, and
it exposes the admin surface to whatever resolves that name. Only with the same gate.

Under all three, do not reach Studio through the data route pattern, and do not publish a container
port (`docs/guides/server-deployment.md`: the installer creates no published ports).

## 6. Surfaces whose backing service does not exist

State each one in the console copy and in the operator documentation rather than leaving the user to
find a broken page. The `ENABLED_FEATURES_*` environment family is the upstream mechanism for hiding
a surface (VERIFIED for `ENABLED_FEATURES_LOGS_ALL` in the base compose and the logs override); other
keys for other surfaces are NOT verified.

| Studio surface | Backing service in sbarbase | What happens | Honest plan |
|---|---|---|---|
| Realtime inspector | none. `docs/guides/server-deployment.md`: "Realtime, Functions, the connection pooler and cron are not implemented" | empty or erroring page | label it in the console and in the Studio handoff note; hide it if a verified feature key exists |
| Logs and Analytics | none. The base compose already sets `ENABLED_FEATURES_LOGS_ALL: "false"` for the same reason (Logflare and Vector are an optional override) | the explorer is already off | keep `false`; this is upstream's own default, so nothing is being hidden that should work |
| Edge Functions | none, and the self-hosted code asserts `EDGE_FUNCTIONS_MANAGEMENT_FOLDER` | page fails closed | mount an empty read-only directory so it shows an empty list, or leave the variable unset and document the failure. Do not create a folder that implies functions are supported |
| SQL editor | postgres-meta, present | works, running real SQL as the scoped login | real, but scoped: statements that need superuser or schema ownership (`CREATE EXTENSION`, `ALTER SYSTEM`, role changes) will fail. Say so; do not grant the privilege to make the page look complete |
| Advisors and lints | postgres-meta (`lib/api/self-hosted/lints.ts` runs SQL through `executeQuery`) | the SQL level lints can run; advisories that expect Supabase platform data or `pg_stat_statements` may be empty | present as SQL lints only |
| Authentication | the auth schema in the environment database, read through meta | fails today with `permission denied for schema auth` (MEASURED, given); fixed by section 3.4 | the read grant is the fix; the Auth page is otherwise real, and the environment already runs an original GoTrue with its own scoped login |
| Storage | one shared, tenant aware Storage process, not one per environment, reached at `/<env>/storage/v1` through the gateway | the Storage page derives its endpoint from the project URL and will not reflect the shared tenant as a project-local bucket API | out of scope for the first integration; label it |
| Data API and API docs | PostgREST per environment, present | works, if `PGRST_DB_SCHEMAS` names a schema that exists | set `public` only, since `graphql_public` is not present here |
| Database, Table Editor, Roles, Extensions, Migrations, Query performance | postgres-meta plus the environment database | the Table Editor is the part the user's agent already saw working | keep it as the reason Studio is being added at all |

## 7. Theme

- VERIFIED: the design system supports Light, Dark (classic dark), Deep dark, and a System theme
  that follows the operating system or browser preference (supabase.com design system theming page),
  and issue #5675 records that the self-hosted dashboard's account popover exposes only the theme
  option. There is no theme environment variable anywhere in `/tmp/sb-compose.yml`.
- INFERRED: the theme is a client-side choice (a cookie or localStorage key), its default is most
  likely System, and it is per browser profile, so two tabs on two environments share it. A light or
  dark experience therefore needs nothing added to the container; it needs one click in the Studio
  popover, or the browser's own preference.
- NOT VERIFIED: the exact storage key and the default value, and whether Studio honours
  `prefers-color-scheme` on the very first load before any stored choice exists. To check: load one
  environment's Studio with a fresh browser profile, then inspect `localStorage` and the root
  element's class or data attribute.
- Per environment theming does not exist and would not be worth adding.

## 8. Memory and CPU

Configuration ceilings are known; usage is not. For Studio, upstream publishes no figure, so nothing
here should be guessed.

What is measured and recorded today (VERIFIED as data, MEASURED when produced):
`docs/evidence/source-stage-footprint.json` records the source stage's actual resident usage sampled
with the daemon: 315 MiB across 9 containers at the revision in the checkout, of which
`sbarbase-durable-db` 101 MiB, `sbarbase-durable-storage` 147 MiB, management Auth 9 MiB, and per
environment Auth and REST 8 to 10 MiB each. `docs/reference/deployment-readiness.md` quotes 279 MiB for an
earlier sample, which is the point: this number moves, so it is re-sampled rather than remembered.

The method to measure the new pair on this host (no guess, no container started by this study):

1. Resident usage, the same way the existing footprint file is produced
   (`lab/installation_runtime.py:sample_usage` uses
   `docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}' <names>`):

   ```
   docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' \
     sbarbase-durable-<e>-meta sbarbase-durable-<e>-studio
   ```

2. Exact cgroup v2 readings, which do not depend on `docker stats` formatting:

   ```
   docker exec sbarbase-durable-<e>-studio cat /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.peak
   docker exec sbarbase-durable-<e>-studio cat /sys/fs/cgroup/cpu.stat   # sample twice, 30 s apart
   ```

3. Pressure, reusing the existing probe: `lab/pressure_admission.py` already reads
   `cpu.pressure`, `io.pressure` and `memory.pressure` inside the database and Storage containers.
   Extend its `CONTAINERS` tuple with the new pair so the same thresholds apply to them.

4. Sample at three states, because one number will not do: fresh start with no page loaded, after
   login with the Table Editor open, and during a SQL editor query over a few thousand rows.

5. Write the result in the shape of `source-stage-footprint.json` (`sampled_containers`,
   `total_mib`, `per_container_mib`, `note`) into `docs/evidence/`, so the admission constants are
   raised from a measurement rather than an estimate.

Then, and only then, set container limits for the pair and update, in one change:
`lab/combined_admission.py` (`MAX_MEMORY`, `MAX_CPUS`, and the container name list, which currently
omits any name it is not told about), `lab/install_server.py` (`PLANNED_MIB`, `RESERVE_MIB`) and the
headroom figure in `docs/guides/server-deployment.md`.

## 9. Pinning and upgrade implications

Under `docs/engineering/UPSTREAM-UPDATE-POLICY.md`:

- Two new components, two new pins, one at a time (rule 5). Studio and postgres-meta are separate
  entries even though they are useless apart.
- New lock file(s) and every consumer of the pin set:
  `lab/pin_update.py:24` (`LOCKS`), `lab/install_server.py:36` (`LOCKS`, which
  `lab/pinned_images_check.py` imports), and the pin table in `docs/engineering/UPSTREAM-UPDATE-POLICY.md`.
  The pin identity is the local image `Id` (`sha256:`), which is what `pin_update.py verify`
  checks, so a tag that moves fails as it should.
- One dated `docs/upstream/YYYY-MM-DD-<component>-<version>.md` entry per component, with the four
  sections (what changed, which surfaces are affected, breaking changes, adopt or defer and why),
  and the previous digest recorded as the rollback pin.
- The entry has to say which of the two versions was not taken and why: the measured pair
  (`studio:2026.07.27-sha-cbb076d`, `postgres-meta:v0.96.6`) against the newer pair in
  `/tmp/sb-compose.yml` (`studio:2026.09.07-sha-7996410`, `postgres-meta:v0.99.0`). The compose's
  `db` image (`17.6.1.136`) must not be taken at all; sbarbase pins `17.6.1.166`.
- Adoption gate (rule 4): the full Python and Bun suites, the live checks, and an adversarial review
  of the affected scope. Neither image is covered by any existing evidence today, so no existing
  gate result may be reused for them.
- Rollback (rule 6): restoring the previous Studio and meta pins is enough, because nothing in this
  design writes to the environment database outside the usual migrations; state that in the entry.

Checkpoints and evidence this adoption could invalidate (the rule 3 obligation, stated concretely):

| Affected artifact | Why |
|---|---|
| `docs/evidence/retained-source-adoption.json`, `retained-target-adoption.json` | they assert byte-for-byte preservation of the HBA rules with an exact expected digest (`lab/verify_retained.py:100-101,110`). A fourth rule per environment changes both the file and the expected rule inventory |
| `docs/evidence/hba-authority.json`, and every HBA journal/digest record | a new publication under the same generation pin moves the file digest |
| `lab/recovery-export.py` (`exact scoped logins exported`, `len(roles)==3`) | the role inventory is exactly three per environment |
| `lab/recovery-restore-db.py` (lines 66-79, 173: `Unsupported role inventory`, HBA list rebuilt from the same three names) | restore refuses an inventory it does not recognise |
| `lab/source_fence.py` (`is_fenced` line 14, `prepare_export` line 49) | the fence and the export fence enumerate exactly `auth`, `rest`, `storage` |
| `lab/provisioning_inspection.py:136` and `docs/engineering/PROVISIONING-INSPECTION.md` report (`scoped_roles`) | it counts the three known names; a fourth is invisible to it, which is worse than a failure |
| `lab/connection_budget.py` and `docs/engineering/CONNECTION-BUDGET.md`, `docs/evidence/connection-limit-checks.json` | the per-database limit of 18 and the admission formula assume three service logins |
| `lab/combined_admission.py` name list and `docs/evidence/combined-runtime-admission.json` | the placement ceiling would omit the new containers |
| `docs/guides/server-deployment.md` headroom (about 8.8 GiB) and `docs/evidence/deployment-rehearsal.json` | the installed requirement changes |
| `docs/evidence/combined-gateway-checks.json` (14 checks) and the smoke route list in `lab/install_server.py:298-311` | if a Studio route is added to discovery or to the smoke output, the counts change |
| `docs/evidence/source-stage-footprint.json` | the sampled container set grows |

Anything in `docs/engineering/checkpoints.md` that cites those files inherits the staleness and must be
re-stated rather than re-used.

## 10. Isolation tests that must exist before this is called safe

Environment identifier is `<e>` as usual; two environments A and B are assumed available (the lab
retains four, `lab/README.md`).

T1. Two Studio tabs, two environments. One browser, tab one on A's Studio, tab two on B's Studio,
each with its own gate. Assert: each tab's Table Editor lists only its own tables; the request log
for each tab shows only its own origin; changing a row in A leaves B unchanged.

T2. Cross environment denial at the database. From A's Studio credentials, attempt
`psql -h sbarbase-durable-db -U <A>_studio -d <B>`. Assert the failure is an HBA refusal
(`no pg_hba.conf entry`), not an authentication or a permissions surprise. Repeat for every pair of
roles and databases, which is 4 logins times 4 databases once the Studio login exists.

T3. A neighbour's Studio credentials cannot read a neighbour. Give meta B's connection string while
it is configured with A's credentials, and assert the request fails and returns no rows. Run this in
both variants: with a per-environment crypto key (decryption should fail) and, if the shared meta
variant is chosen, with the shared key (the password is then the only barrier, and the test proves
whether that is enough).

T4. meta's fallback is closed. Send a request to meta with no `x-connection-encrypted` header.
Assert it either fails or stays inside its own environment, and assert by direct inspection that
`PG_META_DB_USER` is not `postgres`, `supabase_admin`, `authenticator` or any BYPASSRLS role.

T5. Forged connection string. With the environment's crypto key, produce an encrypted connection
string pointing at a neighbour database and assert meta refuses it.

T6. HBA exactness. Assert `pg_hba_file_rules` has exactly one rule for each `(database, login)` pair,
that the two blanket reject lines are last, and that no rule names a cluster-wide role for an
environment database.

T7. Inventory regression. Run `lab/verify_retained.py`, `lab/recovery-export.py` and
`lab/provisioning_inspection.py` against a disposable environment carrying the fourth login. Each
must fail loudly rather than silently ignoring the new role, and each must pass after the inventory
is updated. This is the test that keeps section 9's table honest.

T8. Admission. Assert `lab/combined_admission.py` counts the new containers, and that a placement
that exceeds `MAX_MEMORY` or `MAX_CPUS` is refused before any container is created.

T9. Discovery and the console link. Assert
`/management/v1/environments/<id>/connection` reports the administration route, that
`ui/Connection.tsx` renders it, and that a session with the `viewer` role (or no management
session at all) cannot reach the admin surface.

T10. Pin and rollback. `lab/pin_update.py verify` refuses a floating tag for the new pins, and
restoring the previous Studio and meta pins leaves the environment's Table Editor working.

T11. The gateway admission question of section 2.3. Load the Authentication page and the Storage
admin page and assert that the server-side calls that carry `apikey: <service_role JWT>` are either
accepted deliberately or replaced by the documented design. This test decides whether those pages
work at all, and it should run before any other Studio work is invested.

T12. Resource isolation. While one environment's Studio runs a heavy SQL editor query, assert a
neighbour environment's Auth, REST and Storage endpoints still answer, using the same shape as
`lab/noisy-neighbor-check.py` and `lab/connection-limit-check.py`.

## 11. Not verified, and therefore not claimed

- The Studio image version actually measured is `2026.07.27-sha-cbb076d`; the source read for this
  document is `supabase/supabase` master. The code shapes quoted (origin-only `PROJECT_REST_URL`, the
  `POSTGRES_USER_READ_ONLY` default, `ENABLED_FEATURES_*`, the self-hosted admin client) are from
  master and must be confirmed against the image that is pinned.
- The exact `ENABLED_FEATURES_*` keys for each surface other than logs.
- Whether the published image can serve under a runtime supplied sub-path without a rebuild.
- Studio's actual resident memory and CPU. Nothing was started, so no number is offered; section 8
  is a method, not a result.
- Whether Studio's server-side admin calls survive sbarbase's gateway admission (section 2.3, test
  T11). This is the largest open risk in the whole integration.
- Whether `supabase_admin`, `authenticator` and other cluster roles beyond `anon`, `authenticated`
  and `service_role` exist in this installation's cluster; the feasibility audit
  (`docs/engineering/reviews/supabase-feasibility.md`) says the distribution's bootstrap creates them, but this
  cluster was not queried.
- Whether postgres-meta's `/query` with the scoped login is enough for every SQL editor convenience
  and for the advisors, or only for plain SQL.
- The theme storage key and the default theme value.
- Whether a single Studio process can be made to serve more than one environment without a fork.
  The evidence says it cannot; a fork was not attempted.