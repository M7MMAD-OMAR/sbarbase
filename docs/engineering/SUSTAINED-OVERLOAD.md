# Sustained arrival probe and service admission

Command: `bun lab/gateway-overload-check.ts --sustained`, after the owned durable runtime starts. This extends the short overload probe with 30 seconds of offered arrivals: 20 RPCs/second to the target, 2/second to its neighbor. The target RPC sleeps for two seconds; the neighbor returns immediately. Requests use distinct databases and return distinct integers. It tests admission and waiting, not CPU or disk saturation.

The generator does not wait for prior responses. It permits up to 100 ms scheduling jitter, skips later ticks and caps generator work at 128 pending requests. Consequently it is open-loop with bounded jitter, not a perfectly spaced arrival source. Dispatch times and scheduling lag are recorded. The successful earlier eight-request burst does not predict this result.

## Observed failure

The initial attempt failed response acceptance. A second attempt retained [sanitized raw failure evidence](../evidence/gateway-sustained-failure.json):

| Outcome | Count | p95 latency | Maximum latency |
|---|---:|---:|---:|
| Target correct HTTP 200 | 49 | 7977.94 ms | 9933.45 ms |
| Target expected HTTP 429 | 549 | 1.31 ms | 1.89 ms |
| Target client timeout | 2 | about 10 seconds | about 10 seconds |
| Neighbor correct HTTP 200 | 60 | 3.16 ms | 3.31 ms |

All 600 target and 60 neighbor arrivals were issued, with zero skipped ticks and a generator peak of 10 pending requests. Timeout samples have status 0, meaning no HTTP response was observed. The probe failed, so the post-sustained-load recovery assertion was not executed. Cleanup completed and the owned runtime stopped; fixtures were dropped and temporary keys revoked. No pass artifact was written for this attempt.

## Interpretation and next investigation

Fast overload rejection and correct neighbor responses are observed, but accepted target work can wait long enough to time out. Neighbor latency has measurements, not an accepted production SLO. The lab REST pool has 3 connections while the environment gateway admits up to 8 requests. Queueing and pool fairness are plausible causes, not yet established causally. Inspect active/waiting database requests and upstream cancellation under this load before changing limits. Do not hide client timeouts by reclassifying them as successful overload handling or by merely extending the client deadline.

Next: coordinate per-service admission with available connections and verify cancellation frees upstream work. Rerun the same arrival profile, require zero unexpected failures, retain rejection rate and neighbor latency, and verify post-load recovery. Production capacity, long-running soak behavior and 10/100-environment placement remain unproven.

A read-only agent review identified scheduling jitter wording, missing neighbor latency acceptance and teardown short-circuit risks. The wording and reporting now describe actual scope; cleanup attempts each teardown independently. No product latency promise was invented for this experiment.

## Service-budget mitigation

The gateway now atomically applies a trusted per-service cap alongside the 8-request environment and 32-request process ceilings. The durable installer derives REST admission from its PostgREST pool setting, currently 3. Existing container configuration drift fails startup. Shared counters span managed handler factories and API keys.

The [first rerun](../evidence/gateway-sustained-first-pass.json) of the same 600 target / 60 neighbor arrivals passed: 45 correct target results, 555 expected rejections and 60 correct neighbor results, with no skipped arrivals or unexpected failures. Target-success p95 was 2004.61 ms versus 7977.94 ms in the failed run. Neighbor p95 was 3.42 ms and rejection p95 1.28 ms. Peak generator concurrency was 5. Post-load recovery and cleanup passed. This is mitigation evidence, not proof of pool fairness internals or upstream cancellation.

The script now refuses sustained certification unless both published REST budgets equal the lab's three-connection pool. The prior failed artifact remains unchanged. Tests also cover cross-factory/key service enforcement, service/aggregate accounting and deadline release. Old registries without published service limits do not gain this protection automatically. Effective database-based PostgREST overrides remain outside the installer check.

The smaller service cap intentionally rejects more simultaneous REST requests. A three-connection local lab is not the final production default. The earlier mixed SDK run predates this cap, so its zero-error result must not be attributed to the current policy. Measure mixed bursts and client retry behavior rather than hiding this tradeoff.

The [verification rerun](../evidence/gateway-sustained-checks.json) passed all 14 checks, including explicit published-budget validation: 45 target successes, 555 expected rejections and 60 neighbor successes, zero skipped arrivals. Target-success p95 was 2004.37 ms; neighbor p95 was 3.00 ms. Recovery and cleanup passed.

A later run on the migrated generation, 2026-09-25, failed again with 14 target arrivals that ended without an HTTP status. The cause was the loopback listener, not admission: a refused connection was announced as closing but stayed open, Bun's fetch reused it, and a one-second cleanup cut off the next admitted request on it. The fix and its local reproduction are in [RESOURCE-POLICY.md section 5.0](RESOURCE-POLICY.md); the live run has not been repeated since the fix.
