# Resume checkpoint

Recorded 2026-09-20 for documentation handoff. This file supersedes older next-step paragraphs for the current unfinished change.

## Decision and research

Keep Supabase. Use installation > organization > project > environment, with ownership independent of server placement. The local candidate uses shared PostgreSQL, one database and service credentials per environment, original Auth/REST per environment and shared Storage. Independent PostgreSQL remains the fallback. See [decisions and alternatives](DECISIONS.md), [research](ARCHITECTURE-REVIEW.md) and [handoff](HANDOFF.md). No production approval or 10/100-project capacity claim exists.

## Current incomplete change

Baseline commit: `548d898`. Uncommitted files add `ConcurrencyGate`, a `node:http` loopback adapter under Bun, controlled HTTP tests, actual Supabase overload tests and an SDK regression mode. Preserve these files; do not overwrite them from the baseline.

- Application gateway defaults: 8 active requests per environment, 32 across its process, immediate 429/503 and Retry-After. A slot lasts through response consumption, cancellation or deadline. These are experimental request limits, not project capacity or cluster-wide limits. Management traffic has a separate lane.
- Controlled HTTP evidence currently records 19 passing checks: saturation, neighboring response, recovery, gzip, disconnect and detectable stream deadline failure.
- Actual Supabase overload evidence records 8 checks: eight concurrent RPCs admitted, ninth refused, neighbor returns correct data, target recovers. Temporary RPCs and keys were cleaned up.
- Previously observed unit run: 44 tests and 239 assertions passed. This handoff did not rerun tests.
- The newest paused-client/backpressure test FAILED before validating its intended assertion: installed Bun 1.3.14 does not expose `server.getConnections()` on this HTTP server. The adapter currently calls that unsupported method. Existing 19-check evidence predates this added test; it must not be reported as a pass for the latest script.
- Native fetch proxying uses `decompress: false` to preserve compressed bytes and matching headers. The HTTP adapter was introduced after local Bun.serve streaming probes showed deadline/error handling could appear as successful partial output. This observation is version-specific, not a general claim about Bun.
- A custom transport that ignores cancellation can still hold a pre-header slot indefinitely. Native fetch has its own timeout; a universal hard guarantee is not established.

## Next actions in order

1. Replace unsupported connection counting with supported owned-socket tracking; rerun `bun lab/gateway-http-check.ts`. Prove a paused client cannot leave a deadline-expired response stuck waiting for drain.
2. Recheck free host resources and existing owned containers. Run `bun lab/sdk-load-check.ts --overload-regression` against the durable runtime. Preserve earlier baseline evidence.
3. Run `bun test`; repeat supervisor smoke checks after the HTTP adapter change, subject to resource admission. Document failures rather than lowering admission thresholds.
4. Record exact final evidence and limitations, review the diff, commit only completed task files and refresh the handoff archive.
5. Continue sustained/open-loop load, failure recovery, off-host restore and transfer gates. Short local runs cannot size a public service.

## Runtime safety

At the preceding implementation checkpoint the owned durable runtime had been started with four retained environments; this documentation pass did not stop or change it. Reinspect actual state before acting. Configured container ceilings total 3840 MiB and 3.75 CPUs, not total host consumption. Startup may refuse below 6 GiB available RAM; do not bypass that guard. Preserve unrelated Docker services and all retained volumes. Credentials stay in ignored `.secrets/`, runtime state in `.lab/`.

## Saved artifacts

[Ten-project illustration](diagrams/ten-projects.png), [transfer and recovery illustration](diagrams/move-and-restore.png), [diagram assumptions](diagrams/README.md), [console images and QA](design/CONSOLE-QA.md). Illustrations describe intended operations, not completed transfer/backup functionality.

Sources for the current transport setting: [Bun fetch documentation](https://bun.sh/docs/runtime/networking/fetch), [Bun fetch request options](https://bun.com/reference/globals/BunFetchRequestInit). Reproductions and their scope are in `lab/gateway-http-check.ts`, `docs/evidence/gateway-http-checks.json` and `docs/evidence/gateway-overload-checks.json`.
