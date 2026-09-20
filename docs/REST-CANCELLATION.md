# REST cancellation and retained admission

## Observed gap

The [baseline probe](evidence/gateway-cancellation-before.json) observed a real two-second RPC sleeping in PostgreSQL, then aborted its HTTP client. SQL remained active 540 ms after cancellation and disappeared at roughly 1.96 seconds after cancellation. Freeing a gateway slot on client abort therefore did not represent freed database capacity.

## Implemented mitigation

For REST routes with a configured service budget, request-body buffering still follows client cancellation and an aborted upload never dispatches. Once dispatched, the upstream fetch follows its own deadline and gateway signal instead of the client signal. The gateway handler waits for that upstream result. A disconnected client does not receive an immediate 408 from this path; its socket is already gone.

The HTTP adapter detects the disconnected client and cancels the returned response wrapper. That cancellation now drains the REST upstream body without accumulating chunks, while retaining service, environment and process admission. Completion, stream failure or the independent response deadline releases the counters once. Auth and Storage keep their existing cancellation behavior. Old REST registries without service budgets do not opt into this policy.

## Verification

[15 live checks](evidence/gateway-cancellation-checks.json) cover the real composed gateway, PostgREST and PostgreSQL. Three two-second RPCs were observed active before all clients were cancelled. At approximately 542 ms after abort all three remained active, the fourth request received 429, and the neighboring environment returned its correct value. SQL reached zero active fixture queries around 1.98 seconds after abort. Target recovery, cleanup and runtime shutdown completed.

Fifty-two unit tests and 269 assertions passed, including retained admission before headers, bounded abandoned-response draining and drain-error recovery. The unchanged non-detached HTTP fixtures still pass 24 lifecycle checks. A read-only independent review found no blocking issue and prompted a defensive drain-error cleanup.

## Limits and next work

This is admission retention during ordinary client disconnect, not active SQL cancellation. The native upstream timeout or transport failure can still settle before a long-running database query stops. Independent SQL execution deadlines and their interaction with role/function overrides remain necessary. Do not claim the gateway limits hard-cap all active database work.

PostgREST supports transaction-scoped role settings and hoisted function settings. An execution policy must account for those overrides rather than setting a timeout only on the connection login. Sources: [PostgREST transactions](https://postgrest.org/en/latest/references/transactions.html), [pinned major-version configuration](https://docs.postgrest.org/en/v14/references/configuration.html). A blanket cluster-wide policy is not established here.

The local probe is bounded and uses sleep RPCs. It does not prove cancellation of CPU, I/O, lock waits, commits or long transactions. Mixed SDK regression under the new service cap remains separate outstanding work.

Subsequent checkpoint: [per-login REST SQL deadlines](SQL-DEADLINES.md) now have live evidence for statement expiry and transaction termination despite a statement override. This narrows the earlier gap for the tested configuration; it does not create a hard boundary against SQL authors changing the transaction deadline itself.
