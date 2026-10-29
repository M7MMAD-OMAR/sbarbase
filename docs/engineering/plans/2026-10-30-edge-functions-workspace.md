# Edge Functions workspace in the console

Status: study and proposal, 2026-10-30. Nothing is built. Two research agents, a read of the code, and a sandbox probe on the pinned runtime ([evidence](../../evidence/functions-sandbox-probe-2026-10-30/results.json)) back it. Labels: VERIFIED means read in code or run; INFERRED means reasoned, not run. Not a production readiness claim.

## The question

The owner wants creating and managing Edge Functions to be as easy in the UI as on Supabase's cloud: a function list, a per-function Overview with metrics, Logs, a Code tab with a file tree and editor, Settings, a Test button, Download, and a Secrets page. It has to stay safe between projects.

## Verdict

1. **Do not obtain the write UI from Studio.** The pinned Studio is the upstream image with `IS_PLATFORM` false. Every write surface (the "Deploy a new function" dropdown, Add File, an editable editor, the deploy footer, the verify JWT toggle, delete, adding or deleting secrets) and the Overview tab are client side `IS_PLATFORM` checks (VERIFIED in the 2026.09.28 bundle, same route set in 2026.07.27 and 2026.10.12; the 10.12 gates are INFERRED from the identical routes). Answering the HTTP calls from the Studio proxy changes nothing there. Forcing the flag on sends the whole app to the cloud sign in page (VERIFIED by experiment). Patching minified chunks on every pin update is a fork in practice, and it contradicts the rule that native Studio stays native ([STUDIO-INTEGRATION](../STUDIO-INTEGRATION.md)).
2. **Build a Functions workspace in the Sbarbase console**, laid out like the cloud's: same tabs, same list, same Secrets page. Functions already live in the console (deploy, delete, secrets in `src/control/functions.ts`), because Studio cannot write them. Studio keeps its read only list and code view.
3. **Harden the runtime first.** A web editor does not change who can run code: only owners and admins can deploy or set secrets today (`src/control/catalog.ts:1118`), and the console already has a textarea. But the probe shows the sandbox is weaker than the docs assume, and an easy editor makes the weakness easier to use. The gate list is below.

## What was found

### Studio (read only agent, live probes on 2026.09.28)

- Works with no fork: the list with search, sort and refresh, the read only Monaco code view, Test, Download as ZIP, invoke snippets, the default secrets list.
- Stale: `lab/studio.py` `copy_functions` copies code into the Studio container only when Studio starts, so a later deploy never shows on Refresh; the function `id` is a new uuid on every request. A proxy answering `GET /api/v1/projects/{ref}/functions`, the detail and `body` routes from the live manifest fixes both, and `src/control/supabase-api.ts:373` already serves those routes. Whether its `body` multipart equals Studio's was not compared (INFERRED).
- `logs.all` and `functions.combined-stats` need Logflare and return 500 here. A proxy could special case the known queries; Overview stays hidden by its gate either way.

### Sandbox probe (pinned edge-runtime v1.77.4, run on this host)

| Question | Result |
|---|---|
| Does `netAccessDisabled: true` (`lab/functions/main/index.ts:97`) block the network? | No. It is not an option of this runtime and is ignored: `fetch` to the internet answered 200. [FUNCTIONS-NETWORK-ISOLATION](../FUNCTIONS-NETWORK-ISOLATION.md) option 2 reasons about a knob that does not exist. Top level `importMapPath` is also ignored. |
| Can a function read `/proc`, `/run/sbarbase`, another function's code, `/etc`? | No, all denied. Subprocess spawning is denied. `SB_JWT_SECRET` is not visible. |
| Can a function fill the disk through `/tmp`? | Yes today: 200 MiB written with no error. With `tmpFsConfig: { quota }` the same write fails with "filesystem quota exceeded". |
| Can it reach the host? | Yes. A request to the bridge gateway address reached a host port. Only the host firewall stands between a function and the host. |
| Does a per worker `permissions` option work? | Yes, with snake case keys. A `permissions` object with only some keys denies the rest, so all of `allow_net`, `allow_env`, `allow_read`, `allow_write` must be given. `allow_net: []` means all hosts; `deny_net` of literal addresses is enforced. |
| Can `deny_net` block private ranges? | No. CIDR is refused at worker creation in this version. And a host name that resolves to a denied address, such as `172.17.0.1.sslip.io`, **bypasses** the check and connected. |
| Does the event worker give CPU and memory? | Yes: a Shutdown event with `cpu_time_used` and `memory_used`, and a Boot event; per worker lifetime, not per request (three requests used one worker). |

Consequence: per worker permissions are worth using for `tmpFsConfig`, file access and literal denies, but they are **not** an egress boundary. Blocking the host, the metadata address and private ranges has to happen on the network, outside the worker.

### Container (read from `lab/durable_runtime.py:754`)

Limits are set (memory 384m, 0.5 CPU, 256 pids, IO weight and caps). Absent: `--cap-drop`, `no-new-privileges`, `--read-only`, a non root `--user`, a sized tmpfs. The functions container runs as root with Docker's default capabilities (VERIFIED in the launch arguments; the image's `USER` was read by the agent, not run). `functions_kept` compares a spec label, so new flags must also enter `launch_spec`.

### Observability

- Function calls are **not counted today**: `GATEWAY_PATH` in `src/gateway/observe.ts:76` has no `functions`, so invocations, 4xx and 5xx never reach Usage (VERIFIED). One word fixes it, and `ms` there is time to headers, not to completion.
- The gateway log keeps one hour in memory. The cloud's 15 minutes, 1 hour, 3 hours and 1 day ranges need a longer coarse series.
- Function logs, with redaction, already exist as source `functions` (`src/control/observe.ts`).
- CPU and memory per function need the event worker (above), aggregated per worker.

## Each cloud screen, and what backs it

| Screen | Now | To build | Needs |
|---|---|---|---|
| List: search, sort, refresh, URL with copy, created, updated, deployments | name, JWT flag, updated, size | created time and deployment count in the manifest, search and sort | S |
| Overview: invocations, 5xx and 4xx rate | not counted | count functions at the gateway, per function; a 24 hour series | S, M |
| Overview: execution time | none | gateway latency now; true execution from the event worker | S, M |
| Overview: CPU, memory | container totals only | event worker, per worker | M |
| Logs | source `functions` exists | filter by function, search, time range | S |
| Code: file tree, Add File, editor | a single `index.ts` textarea | tree over the stored files, Add File, rename, delete, editor, deploy | M, L |
| Code: deploy validation | none | boot check after deploy; the event worker reports a boot failure | M |
| Settings: verify JWT toggle | only at deploy | a settings route that changes the manifest flag | S |
| Settings: invoke snippets (cURL, JS, Swift, Flutter, Python) | none | static templates | S |
| Settings: delete | exists | keep, add confirmation naming the function | S |
| Test | none | server side invoke through the gateway with a chosen key, method, headers, body | M |
| Download | none | ZIP of the current version | S |
| Secrets: add or replace, list, delete | names only, write only values | digest and updated time stored at write time; bulk paste of key value pairs | S |
| Secrets: default secrets table | none | static list of what a function receives, marking `SUPABASE_DB_URL` not provided | S |
| "Deploy a new function": editor, CLI, templates | textarea, command | the editor, the existing command, and starter templates (hello, webhook, supabase-js, CORS) | M |

A digest of a secret should be an HMAC with an installation key, not a plain SHA256: a plain hash of a short secret can be guessed offline. Whether the cloud uses a plain hash was not confirmed.

## Security gates before the editor ships

Ranked; each says what proves it. Items 1 to 4 are one change to `lab/functions/main/index.ts` and the launch arguments.

1. **Drop the dead options and set real ones.** Remove `netAccessDisabled` and `importMapPath`; pass `tmpFsConfig.quota` (for example 64 MiB), and all four `allow_*` keys with `allow_read` and `allow_write` limited to `/tmp`. Add a unit test that fails on an option name the pinned runtime does not read (the probe's shape check is the basis). Proof: the `/tmp` fill probe fails; env and file probes still fail. Effort S.
2. **Container flags**: `--cap-drop ALL`, `--security-opt no-new-privileges`, `--read-only` with a sized `--tmpfs /tmp`, a non root `--user`; add them to `launch_spec`. Proof: `docker inspect` plus a failed root write; the runtime still boots (to be tested, not assumed). Effort M.
3. **Egress filtering on the network.** The worker check cannot stop DNS rebinding or CIDR ranges. Drop traffic from the outbound bridge to the host's own addresses, link local (169.254.0.0/16) and private ranges in DOCKER-USER, accept only the Storage listener port from the environment bridges, and ship rules for nftables, ufw and firewalld from `deploy/` (any provider, not only this server). A forward proxy on an internal network is the portable alternative. Proof: the probe's host, rebinding and metadata fetches fail while an `npm:` import and an HTTPS call still work. Effort L.
4. **One loopback exception to design.** Functions reach `SUPABASE_URL` through the main service on `127.0.0.1:9000`, so a blanket loopback deny breaks supabase-js. Either keep the main service reachable by a name that is not denied, or deny by network rules (item 3) rather than per worker.
5. **Gateway cap for functions and counting them.** `src/gateway/handler.ts:274` gives functions no per service concurrency cap, so long calls can take an environment's admission share from its REST and Auth. Add `functions` to `GATEWAY_PATH`, a cap, and `--max-parallelism` on the runtime. Proof: a flood of slow calls leaves REST answering. Effort S to M.
6. **Disk watermark** on the Docker root that pauses Functions, and a lower CPU hard limit once 1 and 5 are in. Effort M and S.
7. **Run the pending live isolation check** ([FUNCTIONS-NETWORK-ISOLATION](../FUNCTIONS-NETWORK-ISOLATION.md), "Live check") with the probe added to it, on a running installation, since what is established so far is the standalone runtime only.

State in the editor, in plain words: a function author holds the environment's `SUPABASE_SERVICE_ROLE_KEY`, as on the cloud. The boundary is the environment, not the function.

## Phases

- **F0, gates 1 to 6 and the `GATEWAY_PATH` fix.** Nothing visible, everything else depends on it. Also correct the option 2 text in FUNCTIONS-NETWORK-ISOLATION.
- **F1, read side.** List, detail with Overview (gateway metrics), Logs, Code viewer, Settings snippets, Test, Download, Secrets page with defaults. Also the Studio proxy fix for fresh list and stable ids.
- **F2, write side.** Editor with file tree, deploy with a boot check, verify JWT toggle, templates, secrets digest and bulk paste.
- **F3, event worker.** CPU and memory series, boot failures in the deploy result, shutdown reasons.

Reader docs and the console copy are bilingual in the same change as each phase.

## Not established

- The 2026.10.12 Studio gates, the `body` shape match, and whether `node:child_process` is really blocked (the probe's `id` command failed, which proves nothing).
- That a locked down container (`--read-only`, non root, no capabilities) boots the runtime and still writes its tmp.
- Host reach from a function on a real environment network; the probe used the default bridge, and starting a host listener for it was declined, so it shows a connection attempt, not a served response.
- Per request CPU and memory; the event worker gave per worker numbers.
- Disk growth from `/tmp` writes in the container layer, and the cloud's secret digest algorithm.
- An editor library (CodeMirror or Monaco) and its bundle cost; decide when F2 starts.
