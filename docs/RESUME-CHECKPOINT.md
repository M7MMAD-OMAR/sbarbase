# Resume checkpoint

Recorded 2026-09-20 for documentation handoff. This file supersedes older next-step paragraphs for the current gateway change.

## Decision and research

Keep Supabase. Use installation > organization > project > environment, with ownership independent of server placement. The local candidate uses shared PostgreSQL, one database and service credentials per environment, original Auth/REST per environment and shared Storage. Independent PostgreSQL remains the fallback. See [decisions and alternatives](DECISIONS.md), [research](ARCHITECTURE-REVIEW.md) and [handoff](HANDOFF.md). No production approval or 10/100-project capacity claim exists.

## Gateway verification checkpoint

Baseline commit: `548d898`. Gateway files add `ConcurrencyGate`, a `node:http` loopback adapter under Bun, controlled HTTP tests, actual Supabase overload tests and an SDK regression mode. Preserve these files; do not overwrite them from the earlier baseline.

- Application gateway defaults: 8 active requests per environment, 32 across its process, immediate 429/503 and Retry-After. A slot lasts through response consumption, cancellation or deadline. These are experimental request limits, not project capacity or cluster-wide limits. Management traffic has a separate lane.
- Controlled HTTP evidence currently records 24 passing checks: saturation, neighboring response, recovery, gzip, disconnect, detectable stream deadline failure and a paused TCP client.
- Actual Supabase overload evidence records 8 checks: eight concurrent RPCs admitted, ninth refused, neighbor returns correct data, target recovers. Temporary RPCs and keys were cleaned up.
- Repeated unit run: 52 tests and 269 assertions passed. Strict type checking passed for the HTTP adapter, handler and concurrency gate.
- Fixed the unsupported Bun `server.getConnections()` call using owned socket tracking. The paused-client test now waits for server acceptance and proves the socket closes after its deadline while the client remains paused.
- The SDK regression passed 1,000 operations with no failures across the two paced phases. Fixture cleanup completed and the owned runtime stopped. Raw results: `docs/evidence/sdk-overload-regression.json`.
- Native fetch proxying uses `decompress: false` to preserve compressed bytes and matching headers. The HTTP adapter was introduced after local Bun.serve streaming probes showed deadline/error handling could appear as successful partial output. This observation is version-specific, not a general claim about Bun.
- Pre-header waiting now has a separate 30-second deadline. Tests verify slot recovery even if an injected transport never settles, cancellation of late response bodies, and HTTP 504/recovery. The gate cannot force arbitrary underlying work to terminate.

## Next actions in order

1. Review the [service-budget mitigation and retained failed baseline](SUSTAINED-OVERLOAD.md). REST admission now follows its configured pool of 3, alongside environment/process limits. [Actual SQL observation](REST-CANCELLATION.md) found client abort does not promptly stop SQL; configured REST now retains admission through upstream response settlement and draining. Verify SQL execution deadlines after upstream failure, and representative mixed traffic with this new cap before production sizing. The earlier 1,000-operation SDK regression predates this service cap.
2. Audit management traffic limits separately from application traffic and measure actual upstream cancellation under sustained overload.
3. Recheck free host resources and existing owned containers before running probes; preserve earlier baseline evidence and do not lower admission thresholds.

## Runtime safety

The SDK regression and subsequent eight-check supervisor smoke test stopped the owned durable runtime with four retained environments. Reinspect actual state before acting. Configured container ceilings total 3840 MiB and 3.75 CPUs, not total host consumption. Startup may refuse below 6 GiB available RAM; do not bypass that guard. Preserve unrelated Docker services and all retained volumes. Credentials stay in ignored `.secrets/`, runtime state in `.lab/`.

## Saved artifacts

[Ten-project illustration](diagrams/ten-projects.png), [transfer and recovery illustration](diagrams/move-and-restore.png), [diagram assumptions](diagrams/README.md), [console images and QA](design/CONSOLE-QA.md). Illustrations describe intended operations, not completed transfer/backup functionality.

Sources for the current transport setting: [Bun fetch documentation](https://bun.sh/docs/runtime/networking/fetch), [Bun fetch request options](https://bun.com/reference/globals/BunFetchRequestInit). Reproductions and their scope are in `lab/gateway-http-check.ts`, `docs/evidence/gateway-http-checks.json` and `docs/evidence/gateway-overload-checks.json`.
