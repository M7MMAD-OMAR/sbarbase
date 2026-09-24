# Edge Functions

One upstream edge-runtime container per environment that turns it on (`lab/functions-image.lock.json`, tier `production.functions`, 384 MiB, 0.5 CPU, 256 pids), started by the first deploy through the same desired/state row pattern as Realtime (`functions_settings`, applied by `lab/realtime.py apply <e> --service functions`). A shared runtime was rejected: it would put every environment's code and secrets within reach of one process that runs every client's code, and isolating clients inside it would rest on the runtime's worker sandbox alone.

## Layout

- Main service: `lab/functions/main/index.ts`, mounted read-only at `/home/deno/main`. It reads `functions.json` on each request, checks the JWT itself when the function's `verify_jwt` is true (HS256 with the environment's JWT secret, expiry honoured), and runs the function with `EdgeRuntime.userWorkers.create` (150 MiB, 150 s wall clock, 10/20 s CPU soft/hard). Workers get `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` and the environment's secrets, never the container's own variables.
- Code: `.lab/upstream/functions/<runtime>/`, written by the console (`src/control/functions.ts`), mounted read-only. Each deploy writes `v<time>-<random>/<name>/…` plus `_shared/…` and then switches `functions.json` atomically; a version nothing points at is removed five minutes later.
- Secrets: `.secrets/upstream/functions/<runtime>/secrets.json` (mode 600), mounted read-only at `/run/sbarbase`. Names starting `SUPABASE_` or `SB_` are refused.
- Networks: the internal runtime network, plus `sbarbase-durable-egress` for `npm:`/`jsr:` imports and outbound calls. No other service joins the egress network.

## Requests

The gateway sends `/<runtime>/functions/v1/<name>/…` to the runtime as `/<name>/…` with the caller's headers, less hop-by-hop and forwarding headers. A name must match `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`, so `/_sb/…` is never reachable from outside. With a key, the key is checked and the anonymous token stands in for it as for every service; without a key, nothing is added and the function's `verify_jwt` decides. Bodies stream up to the upload limit; the deadline is 150 s.

`SUPABASE_URL` points at the main service's `/_sb/<auth|rest|storage>/v1/…`, which forwards to the environment's own containers by name on the internal network (Storage with the environment's tenant host). The upstreams check every token, so the proxy holds no privilege of its own.

## Limits

Code is not in backups. `SUPABASE_DB_URL` is not provided. Deploy is the Sbarbase command (`lab/functions-deploy.ts`) or the console, not `supabase functions deploy`.
