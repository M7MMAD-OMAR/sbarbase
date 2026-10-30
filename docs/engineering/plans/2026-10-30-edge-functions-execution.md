# Edge Functions workspace: execution plan for F1, F2 and F3

Status: plan, 2026-10-30. It turns the study ([edge-functions-workspace](2026-10-30-edge-functions-workspace.md)) into checkpoints. F0 (container, gateway counting and cap, sandbox options, egress rule generator) is committed. Labels: VERIFIED means read in code or in the owner's cloud project; INFERRED means reasoned, not run. Not a production readiness claim.

The cloud layout this follows was observed read only on 2026-10-30: [observation.md](../../evidence/functions-cloud-observation-2026-10-30/observation.md).

## Decisions

1. **Console native, one new top level section "Edge Functions"** in an environment (next to Database, Usage and API keys), like the cloud's left menu. It holds two pages, Functions and Secrets, and per function the tabs Overview, Logs, Code, Settings. The old "Edge Functions" block under Services is removed when the workspace ships, so there is one place. Nothing it does is dropped: the runtime switch (turn Edge Functions on or off, the state chip Off, Applying, On, Not applied, the failure text and the 3 second poll while pending) moves to the list page header; the deploy command and the textarea deploy stay reachable (an "Add function" menu on the list page) until the editor (F2) replaces them.
2. **No new installation wide setting.** Every route is under `/management/v1/environments/{id}/...`, resolved through `catalog.functions(actor, environment)`, which already restricts to owners and admins of that environment's project and to a ready environment. Files, secrets, digest keys, counters and logs are keyed by the environment's runtime id (`e_` plus 24 hex). A test calls each new route with another environment's id and a non member.
3. **Test runs in the browser**, from the console, against the environment's own public gateway address with a publishable key (the cloud does the same from its dashboard). No server side invoke route is added: the server never holds an invoking credential for a user. A secret key is never offered; without a publishable key the panel says so and links to API keys. A browser can read only the response headers the gateway exposes (`CORS_EXPOSED`, and it drops a function's own `access-control-*`), so the panel shows status, time, `content-type` and the exposed ones, and says so. Test calls are counted in the function's numbers and use one of the environment's function slots. The study listed a server side invoke; this replaces it because it adds no new authority.
4. **Studio proxy fix (fresh list, stable ids) is deferred** out of F1. Studio stays read only and native; the console is now the primary surface, and the proxy change touches `lab/studio.py` and a path the owner uses on the live server. It becomes its own checkpoint after F3 if the owner still wants it.
5. **Editor library**: CodeMirror 6, lazy loaded as its own chunk, read only in F1 and editable in F2. Decided by bundle measurement before F1's viewer is written (gate: main bundle growth under 10 KiB, editor chunk loaded only on the Code tab).
6. **Digests are HMAC-SHA256 with a per environment key**, not the cloud's plain SHA256 (not confirmed what the cloud uses). Two environments holding the same value show different digests, so a digest is no equality oracle across environments. The key and the per name updated times live in `.secrets/upstream/function-digests/` (one key file and one JSON per runtime, mode 0600), **not** in `.secrets/upstream/functions/<runtime>`: that whole folder is mounted into the functions container at `/run/sbarbase` (`lab/durable_runtime.py` `functions_mounts`), so a key placed beside `secrets.json` would be readable from inside the runtime. A test asserts the key path is outside every mounted folder. The key is created on first use and never returned.
7. **Per function counters in the gateway are memory only**, 1 minute buckets for 24 hours. A call is counted per function only when the name is in that environment's manifest (a keyless caller can send any name that matches the name rule, so unknown names must not take counter slots or push real ones out). The manifest is cached per runtime and reread only when its stamp (mtime, size, inode) changes, so a counted call costs one `stat`, never a read of the file. A name such as `constructor` is a valid function name and a property of every object, so every manifest lookup uses `entryOf` (own properties only). A deploy refuses a new name once an environment has 100 functions, so the counters of one environment are bounded; each environment also has its own bucket ceiling (40,000) and the installation a global one (400,000), past which the environment's own oldest minute makes room (the recent numbers are kept; an environment never takes another's room). A separate cumulative counter per function is reset by each deploy and gives "since the last deploy" (the day of buckets cannot: a function deployed three days ago would show zero); the page also shows when counting began, because a restart empties everything. Counting starts again when Sbarbase restarts; the page says so. The time shown is time to response headers, not to the end of a streamed body.

## Requirements

| ID | Requirement |
|---|---|
| R1 | A function list with search, sort (name, created, updated) and refresh; columns name, URL with copy, created, updated, deployments. |
| R2 | Per function Overview: range 15 min, 1 hour, 3 hours, 1 day; invocations, 5xx rate, 4xx rate, errors in 24 h, response time average and max; later CPU time and memory (F3). |
| R3 | Per function Logs: invocations with time, method, path, status, duration, filter and search, errors only; function output lines. |
| R4 | Code: file tree including shared files, viewer (F1), editor with Add File and rename and delete (F2). |
| R5 | Settings: name, verify JWT (shown in F1, toggle in F2), invoke snippets (cURL, JavaScript, Swift, Flutter, Python), develop locally commands, delete with confirmation naming the function. |
| R6 | Test panel: method, body, headers, query parameters, send, show status, the headers a browser may read, body and time. Download as ZIP through an authenticated fetch (a plain link cannot carry the bearer token). |
| R7 | Secrets page: list with name, digest and updated; default secrets table with descriptions; add or replace, delete (F1); several rows and bulk paste of `KEY=value` pairs, multi line values (F2). |
| R8 | Deploy from the editor and from templates with a boot check before the live version is replaced (F2). |
| R9 | Event worker: CPU time and memory per worker, boot failures, output lines attributed to a function (F3). |
| R10 | Strict per environment; no write by a viewer; no secret value or key in any answer, log line, audit row or evidence file. |
| R11 | Design: no borders on cards and components, tone steps and spacing, custom controls (no browser select), local SVG icons, tabs and sections with short guidance, desktop and phone, light and dark, and right to left. "Arabic" here means what the Authentication workspace's U0 capture meant: the layout rendered with `dir=rtl` using logical CSS properties, with English strings. The console has no translation layer and this work does not build one; whether to build it is the owner's decision. Renders come from the real components in a throwaway Vite page with a stub request (as U0 did), not from a running installation. |
| R12 | Bilingual docs with each phase, CHANGELOG entries in both languages, docs tests passing. |

## Review findings folded in

An independent design review (EGM, read only, against the code) found the points below; each changed this plan. Where an earlier paragraph disagrees, this section wins.

1. **Event worker output has no writable place.** All three mounts are read only and the root is read only (`lab/durable_runtime.py:483-489`, kept-container check at `:2067`). F3 therefore does not write a file: the event worker hands its aggregate to the main service, which serves it on a reserved route that only the control plane can call. Boot failures reach the deploy answer from F2's boot check, not from F3.
2. **The boot check is specified against the real code.** The main service reads its environment once at start, so a per call nonce held by the control plane has nothing to verify against; the route takes an HMAC over the version and a timestamp, keyed with the environment's JWT secret that the main service already has (a function worker never sees it: the probe showed `SB_JWT_SECRET` is not visible). The staging folder idea is dropped: the worker path includes the version, so it would boot code that is not what goes live; `deploy` already writes the real `v...` folder before switching the manifest, so the check boots that exact path and switches only on success, and `prune` cleans a failed one. The reserved route sits before the name check in `lab/functions/main/index.ts`. Changing that file and `--event-worker` both recreate the container, so **both land in one release**, once.
3. **Content Security Policy.** The console is served with `style-src 'self'; connect-src 'self'` (`lab/ui-static.ts:14`). Inline `style` attributes are blocked (the charts use attributes and classes), CodeMirror must work with its injected styles or be replaced, and the Test fetch must go to the console's own origin, which is what the Project URL already is. A gate: the built console is served through `uiStatic` and the Code tab and a Test send load with no CSP violation (read from the console messages). A CodeMirror and CSP spike runs before F1.2.
4. **`ui/api.ts`** turns every 409 into "not provisioned yet", drops the message of 400, 422 and 5xx, and always parses JSON, so neither a boot error nor a ZIP can pass. F1.2 adds a raw (blob) call and passes server messages through, additively, and `tests/` covers both.
5. **Memory and hot path.** Folded into decision 7 above: a deploy cap of 100 functions, per environment and global bucket ceilings, cumulative counters for "since last deploy". The gate has a number: 100 functions with a full day of traffic ask for 144,000 buckets; the per environment ceiling keeps 40,000 of them and the heap used must stay under 20 MiB in a measured synthetic run (4.5 MiB measured on 2026-10-30). The per function ring of 200 recent calls is not under the bucket ceilings: 100 functions hold at most 20,000 call records. The per request cost is a constant: the last bucket is incremented; no minute list is scanned.
6. **Logs per function.** Invocation rows are per function. Function output in F1 is the environment's whole log and is labelled that way (and filterable by text); attribution by function comes in F3 from the event worker. A stored path is redacted with the existing `redact` before it leaves the server, and the query string is never stored.
7. **Today's deploy paths stay.** The deploy command (multi file projects, `_shared`, `config.toml`) stays on the page permanently; the editor adds a way, it replaces none. The section is shown only to owners and admins (the `canWrite` gate in `Connection.tsx`), because viewers get 403.
8. **Wiring.** `functionsHandler` is built in `src/control/handler.ts` and now receives the shared `RequestLog`; the gateway counting is wired in `application.ts`. The audit `recordFunction` action union gains each new action with its phase.
9. **Digest files and recovery.** Only `functions/<e>` is mounted (`functions_binds`), so `function-digests/` is outside it; a Python test asserts that against `functions_binds`. `lab/installation_recovery.py` walks all of `.secrets/upstream`, so a backup includes `function-digests/`; the recovery suite (`lab/test_installation_recovery.py`) runs with digest files present and passes. Deleting an environment does not remove its function secret or digest files today (the same gap the secret files already have, noted in the Authentication plan); this plan does not close it and the docs say so.
10. **Falsifiable proofs.** Test counts are not a gate; the assertions are named in each checkpoint below. The archive check falls back to Python `zipfile` when `unzip` is absent. The leak test covers answers, the ZIP, log lines and the test path.
11. **Order.** The CodeMirror and CSP spike and the `api.ts` change come first (F1.0), then F1.1, F1.2, F1.3.

## Gates that apply to each checkpoint

- **Counters and memory**: the per function store is bounded (buckets per function, functions per environment by the manifest, a global backstop); a synthetic load records the memory it uses.
- **Isolation**: tests with two environments (runtime ids) prove that function lists, source, secrets and their digests, metrics and logs of one are not returned for the other, and that a viewer and a non member get 403 and 404. Counters are keyed by runtime.
- **No secret leak**: a test saves a sentinel secret value and greps every API answer, the audit rows and the catalog for it; the digest answer never contains the value or the key.
- **Do not break what works**: the existing `PUT /functions/{name}`, `DELETE`, `PUT /function-secrets`, `PUT /functions` routes keep their request and answer shapes (new fields are additive); `lab/functions-deploy.ts`, the Supabase `/v1` API and the hosted MCP keep passing their tests. The live server runs v0.9.2 with the Fanio project; nothing here is deployed by this work.
- **Rendered result**: screenshots of each new screen at desktop (1280 wide) and phone (390 wide), English and Arabic right to left, light and dark, stored under `docs/evidence/functions-<phase>-2026-10-30/`, judged by looking, not by test counts.
- **Independent review**: one fresh reviewer per checkpoint (EGM or a fresh general agent) with the contract, the rules and the source; one helper agent at a time; no test suites in parallel.

## Checkpoints

Every checkpoint ends in one commit with explicit paths. Shared files (`ui/Connection.tsx`, `ui/style.css`, `package.json`, `bun.lock`, the two CHANGELOGs) are changed by small additive edits and committed at once, because another session works in the same checkout.

### F1: read side

**F1.0 spike** (`ui/api.ts` raw call and message passthrough with tests; a CodeMirror size and CSP measurement written to `docs/evidence/functions-f1-2026-10-30/`): decides the editor library before F1.2.

**F1.1 server read model** (`src/control/functions.ts`, a new `src/control/functions-read.ts`, `src/gateway/observe.ts`, `src/control/application.ts` wiring, tests)
- Manifest entry gains `created_at` and `deployments`, kept by `deploy`; older entries without them read as `created_at = updated_at` and `deployments = 1`. A deploy of a new name is refused (400) once an environment has 100 functions.
- `GET .../functions` additive fields: `created_at`, `deployments` (the existing `path` stays the address).
- `GET .../functions/{name}/source`: file list with sizes and contents of the current version, `_shared` files as `../_shared/...`, a cap per file and in total with a `truncated` flag.
- `GET .../functions/{name}/download`: a ZIP (stored, no compression) named `<name>.zip`, laid out like a project: `<name>/index.ts` and `_shared/cors.ts`, never the viewer's `../_shared/` names (a `../` entry is a path traversal for whoever extracts it). A test fails on any entry containing `..`, a leading `/` or a backslash, and `unzip -t` must pass. The CRC32 comes from `node:zlib` if the pinned Bun has it, else a table.
- `GET .../functions/{name}/metrics?range=15m|1h|3h|1d`: buckets, totals, since-deploy invocations and errors from the counters.
- `GET .../functions/{name}/logs?range&q&errors&limit`: per function invocation rows from a per function ring (200), redacted path, no query string; function output lines (environment wide in F1) through the existing `/logs?source=functions`.
- `GET .../function-secrets`: name, HMAC digest, updated time; and the static default secrets list, marking `SUPABASE_DB_URL`, `SUPABASE_PUBLISHABLE_KEYS`, `SUPABASE_SECRET_KEYS`, `SUPABASE_JWKS` as not provided here (what a function actually receives is `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`; the list says which). `changeSecrets` also records the updated time per name.
- Proof: `bun test` on the new test files (min 25 tests): zip validity (read back with `unzip -t`), path safety of file names, truncation, bucket math, LRU cap, isolation, roles, no leak, digest stability and difference across environments.

**F1.2 console workspace** (`ui/FunctionsWorkspace.tsx` and parts, `ui/functions-workspace.css`, small edits in `ui/Connection.tsx`, remove the old section from Services, UI tests for pure helpers)
- List page, function page with tabs, Overview with range switch and SVG charts, Logs with filters, Code viewer, Settings with snippets and the delete confirmation, Test panel, Download, Secrets page.
- Custom select and menu components (no `<select>`), keyboard and focus handling, `aria` roles for tabs.
- Proof: `bun run typecheck:ui`, `bun run build:ui`, screenshot set as above, the helper tests.

**F1.3 docs** (`docs/guides/edge-functions.md` and `.ar.md`, `docs/engineering/EDGE-FUNCTIONS.md`, `docs/guides/logs-and-metrics*.md`, CHANGELOG pair, the evidence README)
- Proof: `lab/test_docs_bilingual.py`, `lab/test_docs_links.py`, `lab/test_doc_references.py`.

### F2: write side

- **F2.1 server write path**: `PATCH .../functions/{name}` for `verify_jwt` (additive), deploy with a **boot check**: the new version is written to a staging folder, the runtime is asked to boot it (a dry boot request against a candidate manifest entry, answered by a reserved route that never reaches the function's handler beyond module load), and only on success is the manifest switched. Any function in the same container can reach `127.0.0.1:9000` itself, and the gateway's rule against names starting with an underscore does not protect a route a function calls on its own address, so the boot route needs a per call nonce held by the control plane and accepts only staged versions that are not yet live; on failure the live version stays and the answer carries the boot error. Secrets: multi row and bulk paste parse (`KEY=value`, quotes, multi line, `export ` prefix, comments) validated on both ends, a cap of 100 total. Templates (hello, webhook, supabase-js, CORS) as static data. The security gates of the study (items 1 to 6) are already done in F0; F2.1 starts only after re-running the hardened runtime probe as the owner's gate (the editor ships only with evidence).
- **F2.2 editor UI**: file tree, Add File, rename, delete, CodeMirror editable, "Deploy updates", unsaved changes guard, verify JWT toggle with a save bar, Add or replace secrets with several rows and bulk paste, templates menu on "Deploy a new function".
- **F2.3 docs and acceptance**.

### F3: event worker

- **F3.1** the runtime starts with `--event-worker`; a small Deno event worker aggregates Boot, Shutdown (`cpu_time_used`, `memory_used`) and Log events per function in memory and hands the aggregate to the main service, which serves it on the reserved HMAC route; the control plane pulls it. Output lines are attributed to a function through the event's `service_path`. This lands in the same release as the F2.1 main service change.
- **F3.2** Overview Usage section (CPU time, memory), Logs gain attributed output, docs and acceptance.
- Per worker lifetime numbers, not per request; the page says so.
- Adding `--event-worker` changes `launch_spec`, so each environment's functions container is recreated at the next start (a brief interruption of that environment's functions; to be measured and written in the release notes). `lab/durable_runtime.py` is also in the Authentication session's uncommitted set; F3.1 starts only after that change is committed, and edits it by small additive hunks.
- The HS256 note: the runtime's `validJwt` accepts only HS256 tokens signed with the environment's JWT secret. The Test panel with a publishable key goes through the gateway, which replaces the key with the environment's anonymous token, so it passes; a caller's own asymmetric token on a `verify_jwt` function is refused with 401 and the panel shows that status as it is.

## Order and cadence

F1.1, F1.2, F1.3 (report to the owner), F2.1, F2.2, F2.3 (report), F3.1, F3.2 (report). The run state of the checkpoint engine lives outside the repository.

## Not established (to be closed or stated at each report)

- Whether the cloud's digest is a plain SHA256 (this plan does not copy it).
- Memory cost of the 24 hour counters at many environments (measured in F1.1 with a synthetic load; the cap is a ceiling, not a measurement).
- Whether the hardened container boots a dry-boot route (F2.1 proves it, F0 only proved boot).
- Behavior on the live v0.9.2 server: nothing here is deployed by this work.
