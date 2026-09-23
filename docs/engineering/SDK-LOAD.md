# Managed-gateway SDK workload checkpoints

Latest checkpoint: the same mixed workload passed after REST service admission, disconnect retention and SQL deadlines were added. See the policy regression below; earlier timings remain historical.

Two existing environments were exercised through the composed loopback gateway, real scoped publishable keys and the original Supabase JavaScript SDK. Requests included RLS-protected reads/inserts, Auth `getUser`, private object upload and verified private download. Fixtures used different payloads and identities in the two environments. Setup and cleanup are excluded from timings.

Each environment ran one worker, then four workers. Both phases lasted approximately ten seconds. Each worker paced starts roughly 100 ms apart and waited for completion, so this is a closed-loop workload that reduces its offered rate when responses slow. Nominal aggregate rates were 20 then 80 operations/second, with at most eight operations in flight. Files were approximately 2 KiB and the fixture tables were tiny.

## Measured results

All 1,010 measured operations returned correct results without reported errors: 202 in the first phase and 808 in the second. Each operation type had 40 or more observations per phase. Timings cover the SDK call and response validation over loopback, not internet-client latency.

| Operation | First phase p95, ms | Four workers per environment p95, ms |
|---|---:|---:|
| Read | 2.70 | 2.57 |
| Insert | 21.59 | 4.19 |
| Identity | 30.15 | 30.84 |
| Upload | 22.09 | 14.03 |
| Download | 7.72 | 7.52 |

[Raw per-operation timings and midpoint pressure snapshots](evidence/sdk-load-checks.json) are retained. Midpoint database/Storage PSI snapshots stayed below configured admission thresholds. They are not peak measurements. A preceding [unpaced 200-operation burst](evidence/sdk-burst-checks.json) also had zero errors, but its concurrent identity p95 was approximately 188 ms and upload p95 approximately 99 ms. The different pacing and very short burst prevent treating those runs as a controlled capacity comparison.

## Cleanup and limitations

Temporary rows/tables, added fixture policies, objects/buckets and Auth identities were removed. Probe keys were revoked, the loopback server closed and the owned runtime stopped; unrelated services remained running. The script checks cleanup before reporting success. Only revoked key metadata remains for audit.

This does not establish a sustained SLO, arrival-rate capacity, production sizing, large-dataset behavior, large uploads or capacity for 10/100 projects. No deliberate open-loop overload or prolonged disk pressure was tested. The user workstation and generator share a host with the runtime. Future tests need longer independent runs, realistic application mixes, external generators, overload/recovery phases and explicit acceptance targets.

## Combined-policy regression

Command: `bun lab/sdk-load-check.ts --policy-regression`. The workload and pacing were unchanged: one then four workers per environment, five SDK operation types, two ten-second phases. The durable runtime had REST admission 3, shared environment/process limits, retained REST cancellation slots, and 8s/12s SQL defaults.

[Raw policy-regression evidence](evidence/sdk-policy-regression.json) contains 1,001 correct operations, 201 in the serial phase and 800 in the concurrent phase. There were zero failed operations and no observed SDK rejection. Concurrent p95 values were 2.96 ms read, 12.54 ms insert, 28.18 ms identity, 18.43 ms upload and 7.62 ms download. Timing differences from earlier runs are not a controlled performance comparison. Midpoint pressure snapshots remained below admission thresholds.

The new `status` field records a status reported by the SDK where available, not an inferred HTTP success code. Null means the SDK omitted it; zero is the initial value when an operation throws before a response. Correctness is independently validated through returned data and identities. Fixture cleanup, key revocation and owned runtime shutdown completed. Earlier evidence files were not overwritten.

Four mixed workers do not imply four concurrent REST requests: operations rotate across REST, Auth and Storage. This success therefore does not supersede the deliberate REST overload rejection results. Longer runs, pure-service bursts and external arrival generators remain necessary for capacity claims.
