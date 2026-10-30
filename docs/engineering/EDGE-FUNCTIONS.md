# Edge Functions

One upstream edge-runtime container per environment that turns it on (`lab/functions-image.lock.json`, tier `production.functions`, 384 MiB, 0.5 CPU, 256 pids, launched locked down as described under Container hardening), started by the first deploy through the same desired/state row pattern as Realtime (`functions_settings`, applied by `lab/realtime.py apply <e> --service functions`). A shared runtime was rejected: it would put every environment's code and secrets within reach of one process that runs every client's code, and isolating clients inside it would rest on the runtime's worker sandbox alone.

## Layout

- Main service: `lab/functions/main/index.ts`, mounted read-only at `/home/deno/main`. It reads `functions.json` on each request, checks the JWT itself when the function's `verify_jwt` is true (HS256 with the environment's JWT secret, expiry honoured), and runs the function with `EdgeRuntime.userWorkers.create` (150 MiB, 150 s wall clock, 10/20 s CPU soft/hard, a 64 MiB `/tmp` quota and the `permissions` object under Worker permissions). `netAccessDisabled` and a top level `importMapPath` were passed here before; the pinned runtime (v1.77.4) ignores both, so they are gone ([probe](../evidence/functions-sandbox-probe-2026-10-30/results.json)). Workers get `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` and the environment's secrets, never the container's own variables.
- Code: `.lab/upstream/functions/<runtime>/`, written by the console (`src/control/functions.ts`), mounted read-only. Each deploy writes `v<time>-<random>/<name>/…` plus `_shared/…` and then switches `functions.json` atomically; a version nothing points at is removed five minutes later.
- Secrets: `.secrets/upstream/functions/<runtime>/secrets.json` (mode 600), mounted read-only at `/run/sbarbase`. Names starting `SUPABASE_` or `SB_` are refused.
- Networks: the environment's own internal network `sbarbase-durable-<runtime>-fn`, which holds only its Auth and REST, plus `sbarbase-durable-outbound` for `npm:`/`jsr:` imports and outbound calls, with traffic between its containers off. Functions never join the runtime network the other environments' services share, and the host firewall rules that keep them off the host and off private networks are generated, not applied, by `lab/functions_egress.py` ([FUNCTIONS-NETWORK-ISOLATION](FUNCTIONS-NETWORK-ISOLATION.md)).

## Requests

The gateway sends `/<runtime>/functions/v1/<name>/…` to the runtime as `/<name>/…` with the caller's headers, less hop-by-hop and forwarding headers. A name must match `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`, so `/_sb/…` is never reachable from outside. With a key, the key is checked and the anonymous token stands in for it as for every service; without a key, nothing is added and the function's `verify_jwt` decides. Bodies stream up to the upload limit; the deadline is 150 s (see Gateway).

`SUPABASE_URL` points at the main service's `/_sb/<auth|rest|storage>/v1/…`, which forwards Auth and REST to the environment's own containers by name on the environment network. Storage is shared and takes its tenant from a header the caller sets, so it is on no environment network: `/_sb/storage/v1` forwards to a listener of the loopback API at the environment network's gateway address, which admits only sources inside that network's subnet and sets the tenant itself (`src/control/functions-storage.ts`). The upstreams check every token, so neither the proxy nor the listener holds a privilege of its own. Details and what is not yet checked live in [FUNCTIONS-NETWORK-ISOLATION](FUNCTIONS-NETWORK-ISOLATION.md).

## Worker permissions

`PERMISSIONS` in `lab/functions/main/index.ts` is the pinned runtime's per worker `permissions` option, with snake case keys. A key left out is denied rather than defaulted: without `allow_import`, a `jsr:` import fails at worker boot, so every allow list is given.

| Key | Value | Effect |
|---|---|---|
| `allow_net` | `[]` | Every host: `npm:`, `jsr:` and `https:` imports, outbound calls and `SUPABASE_URL` all work. Egress is not limited here. |
| `allow_env` | `[]` | Every variable of the worker's own set. |
| `allow_read`, `allow_write` | `['/tmp']` | The worker's temporary directory only. |
| `allow_import` | `[]` | Every host. |
| `deny_net` | `169.254.169.254`, `[::ffff:169.254.169.254]`, `[fd00:ec2::254]` | Defence in depth only (below). |

`tmpFsConfig: { quota }` limits `/tmp` to 64 MiB per worker, not per container. In the hardened container the effective bound is lower: `/tmp` is one 32 MiB tmpfs shared by every worker of the environment, so the 64 MiB quota is never reached and a function that writes past 32 MiB in total fails with no space left. The sandbox probe measured eight workers writing 40 MiB each at once, 384 MiB in total, without error ([evidence](../evidence/functions-sandbox-hardening-2026-10-30/results.json)); that run had no container tmpfs and does not apply to the hardened container. Isolation between functions, and from the container's own files, comes from the runtime's virtual file system; `allow_read` and `allow_write` limited to `/tmp` are a second layer.

`deny_net` is not an egress boundary. It takes literal addresses (a CIDR range makes worker creation fail). It blocks the listed addresses and the other spellings the runtime parses to them (measured blocked: hex `0xa9fea9fe`, octal `0251.0376.0251.0376`, `169.254.43518`, a trailing dot, mapped and expanded IPv6 forms). Measured as not blocked: the bare decimal form `2851995902`, the NAT64 form `[64:ff9b::a9fe:a9fe]`, and any host name that resolves to a listed address, such as `169.254.169.254.sslip.io`. The boundary is `allow_net: []` together with the host egress rules in [FUNCTIONS-NETWORK-ISOLATION](FUNCTIONS-NETWORK-ISOLATION.md), which are generated and not yet applied or proven on a real server.

## Container hardening

`resource_policy.hardening_flags` gives the `production.functions` tier, and no other, these `docker run` arguments: `--cap-drop ALL`, `--security-opt no-new-privileges`, `--read-only`, `--user <uid>:<gid>`, a `/tmp` tmpfs of 32m and a `/deno-dir` tmpfs of 32m (`DENO_DIR`), both `rw,nosuid,nodev,noexec`. The user is the owner of the environment's private directory (`functions_user`), read when the container is made, never a fixed number and never root: `secrets.json` has mode 600, and a user that cannot read it would see no secrets and no error. A console run as root refuses to start Functions.

- **Checked on reuse.** A container a planned upgrade restart left running is kept only if `functions_kept` finds the image, launch spec, networks, settings, mounts and unchanged main service code as `functions_start` would make them, and `hardening_applied` finds the lock down in `docker inspect` (not privileged, read-only root, all capabilities dropped and none added, exactly `no-new-privileges`, the user, exactly the two tmpfs mounts), not only the label that says so.
- **Readable files.** `functions_readable` runs `docker exec` in a new or kept container and removes it if its user cannot read the secrets, the main service or any deployed file. Rootless Docker, a remapped user namespace and SELinux are refused here as unreadable; none of them was tested.
- **Memory.** A tmpfs is charged to the container's 384 MiB and never reclaimed. Measured with both tmpfs completely full and swap off (`memory-swap` equal to `memory`, as the production launch sets it): with 64m and 96m tmpfs, one 130 MiB worker call on top of four warm functions (about 150 MiB) was killed in 3 of 3 runs (exit 137). With 32m and 32m the same call survived 3 of 3 runs at a peak of 355 to 365 MiB, so even in that worst case the margin is thin ([evidence](../evidence/functions-container-hardening-2026-10-30/results.json)).
- **Cache size.** The Deno cache tmpfs is 32 MiB; supabase-js, zod and ms used 16 MiB. A set of functions whose imports exceed 32 MiB fails its later imports until the container is recreated.
- **Not tested** ([evidence](../evidence/functions-container-hardening-2026-10-30/results.json)): native-addon npm packages under the `noexec` tmpfs (they may fail to load); the kept-container path against a real upgrade restart; a host that gives these containers swap (it hides the out of memory kill, and the production launch forbids it); rootless Docker and a remapped user namespace (the container user could not read the mode 600 secrets; the `functions_readable` check should refuse the start, not run); SELinux enforcing with labelled bind mounts (the binds carry no `:z` or `:Z`); Docker Desktop or another OS. The measurements come from one Fedora 44 workstation with rootful Docker.
- **After an out of memory kill** nothing restarts the container: it stays down until the next `functions_start` (a deploy, turning Functions on again, or a runtime start).

## Gateway

Function calls pass through the same gateway gate as the other services (`src/gateway/handler.ts`, `concurrency.ts`, `observe.ts`, `shares.ts`):

- **Counted.** `functions` is in the gateway path of `observe.ts`, so calls reach Usage with status and time to the response headers, not to the end of a streamed body. The log keeps the path only: no query, header or key.
- **Capped.** At most 4 calls in flight per environment (`FUNCTION_CONCURRENCY`); the next is refused with `429` and `retry-after: 1`, like any service over its cap. A route's `serviceConcurrency.functions` replaces the 4. The cap keeps long calls from taking the share the same environment's REST and Auth need (default share 8).
- **Deadline.** 150 s (`FUNCTION_DEADLINE_MS`) for the wait for headers and for the streamed body; the transport timeout is 150 s for functions and 15 s for every other service. A client that leaves before the response headers arrive is answered `408` and counted as `408`; one that leaves during a streamed body frees its slot and keeps the status already counted (for example `200`). Both cases are in `tests/functions-gateway.test.ts`.
- **Order.** The key is checked before admission, so an invalid key never takes a slot.
- Unit tests: `tests/functions-gateway.test.ts`. Not measured under load on a real server.

## Console workspace and read model (F1, 2026-10-30)

Plan: [edge-functions-execution](plans/2026-10-30-edge-functions-execution.md). The console section is `ui/FunctionsWorkspace.tsx` with `ui/functions-workspace/`; everything it reads is under `/management/v1/environments/{id}/`, owners and admins only, through `catalog.functions` (a viewer and a non member get 403):

- `GET functions` adds `created_at` and `deployments` (a manifest entry written before them reads as created at its last deploy and deployed once). A deploy of a new name is refused at 100 functions. Every manifest lookup uses `entryOf`: `constructor` and `toString` are valid function names and properties of every object.
- `GET functions/{name}/source` and `download`: `functions-read.ts` reads the current version, never follows a link, caps inline content at 256 KiB a file and 2 MiB in all. The ZIP is stored (no compression), `<name>/...` and `_shared/...`, and refuses an entry name with `..`, a leading slash or a backslash.
- `GET functions/{name}/metrics` and `logs`: `RequestLog.recordFunction` in `src/gateway/observe.ts`, called by `observedGateway` only for a name in that environment's manifest (`functionNames`, a `stat` per counted call). One bucket a minute for a day per function; at the environment's own ceiling (40,000 buckets), or the installation's (400,000), the environment that needs room gives up its own oldest minute; it never takes another's. A cumulative counter, reset by each deploy (`deployedFunction`), gives "since the last deploy" and says whether this process saw the deploy. `forgetFunction` runs on delete. Call logs keep method, path without query, status and time; the answer passes the path through `redactCallPath` and `redact`, and the `q` filter runs on that redacted text. Kept: 40,000 of the 144,000 buckets that 100 functions called every minute for a day ask for; the test gates the heap under 40 MiB, and 4.5 MiB was seen once on 2026-10-30 ([record](../evidence/functions-f1-2026-10-30/results.json)).
- `GET function-secrets`: names, an HMAC-SHA256 digest (`createHmac` over name, NUL and value, with a 32 byte key per runtime) and the update time, plus the default secrets list. The key and the times live in `.secrets/upstream/function-digests/<runtime>.key` and `.json`, mode 600, outside `functions/<runtime>`, the only folder mounted into the container (`lab/test_functions_digest_mount.py` asserts it against `functions_binds`). The recovery collection takes the folder with the rest of `.secrets/upstream` (`lab/test_installation_recovery.py` passes with digest files present).
- Test runs in the browser with a publishable key, through the public gateway address, so it adds no server authority. Download is fetched with the session token (`download()` in `ui/api.ts`).

Not done: edit and deploy from the console (F2), CPU and memory and attributed output (F3), the Studio proxy fix (deferred in the plan). Tests: `tests/functions-read.test.ts`, `tests/functions-workspace-ui.test.ts`, `tests/api-helper.test.ts`. Renders: [functions-f1-2026-10-30](../evidence/functions-f1-2026-10-30/results.json).

## Limits

- A function with `verify_jwt` off accepts calls with no key, so anyone who can reach the gateway can hold the 4 slots of that environment and make its other function calls answer `429`. REST and Auth keep their share. There is no per caller limit.
- Four workers of 150 MiB each do not fit the 384 MiB container; the cap of 4 admits more busy workers than the container holds, and an out of memory kill takes down every function of the environment until the next `functions_start`. Reasoned from the figures above, not measured under concurrent load.
- `/tmp` is one 32 MiB tmpfs shared by all workers of the environment, charged to container memory; the 64 MiB per worker quota is never reached. The Deno cache is a separate 32 MiB tmpfs.
- The listed `deny_net` entries are not an egress boundary (Worker permissions).
- The egress rules, rootless Docker, a remapped user namespace and SELinux have no live proof on a real server.
- The `serviceConcurrency` entry in `src/control/handover-formats.json` still says "auth, rest and storage" and does not name `functions`. Changing a declared meaning needs a new handover protocol, so that wording is deferred.

Code is not in backups. `SUPABASE_DB_URL` is not provided. Deploy is the Sbarbase command (`lab/functions-deploy.ts`) or the console, not `supabase functions deploy`.
