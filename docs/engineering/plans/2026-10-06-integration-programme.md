# Full integration programme

The owner requested completion of the whole system, with independent tasks,
builders, reviewers and relevant skills. This programme continues
[the durable goal](../../../PROJECT_GOAL.md) and the complete
[product backlog](2026-10-03-product-and-portability-plan.md). It does not
replace that backlog with the changes already passing.

The public service reference remains official Supabase `self-hosted/v0.8.2`,
commit `564eab8ad7840b13324f68b1bfac074ef8d51c21`. Use the
[frozen source record](../benchmarks/supabase-v0.8.2.source.json) and pinned
image manifests for actual comparisons. A versioned fragment result accepts
only that fragment on its exact source and environment.

## Current work owners

| Work | Task | Current acceptance |
|---|---|---|
| Source integration, Studio revocation, merge and final verification | Main integration task | Studio epoch integration accepted with 64 focused tests and two independent reviews on the merged source; whole-system acceptance is open |
| SB-01 capability and evidence registry | Independent registry task | Source delta merged and 37 focused checks pass; independent merged-source review pending; runtime and production rows remain unproven |
| SB-04 and dependent SB-05 backup/recovery work | Independent recovery task | Concurrent overwrite/delete reproduced; repair and disposable recovery drill in progress |
| SB-06 administrative protection | Independent management-security task | Server and console source accepted with native Auth/browser evidence; dependent CLI support and integrated runtime acceptance are open |
| SB-13a native placement prerequisites | Existing native task | Public source fragments have their own reviewed evidence; full placement, private operation and native continuity remain open |

These workstreams use separate checkouts. Changes are reviewed and integrated
as a delta against the imported source baseline. A result in a separate
checkout does not accept the merged source. Tests needing Docker take turns
with exact owned resource names, bounded memory/CPU and private disposable
data. Existing shared and retained resources are outside those fixtures.

## Current observations

The [standard portable source run](../../evidence/integration-source-2026-10-06.json)
executed 1511 Python tests and reported 35 errors in the public private-FIFO
fixtures. The same run passed 362 Bun tests, both UI and control typechecking,
the UI build, planning integrity and Python compilation. It is a failed source
verification, not an accepted result. The native task owns diagnosis of the
FIFO fixture admission discrepancy. Its separately accepted harness evidence
does not turn the standard runner failure into a pass.

The [Studio source-contract evidence](../../evidence/studio-revocation-2026-10-06.json) records 52 passing focused tests, a successful control typecheck and two independent reviews. It does not accept live vendor or MFA integration. Studio regression tests reproduced authorization gaps across delayed mutation
bodies, deferred responses and subsequent streaming reads. The repair also
checks session expiry during workspace switching. The proxy acquires a complete
mutation body before dispatch, with a default 16 MiB byte limit and 30-second
read deadline. Workspace switching remains limited to 512 bytes. Internal
configuration can set a positive body limit up to 64 MiB and a positive read
deadline up to 120 seconds. Response streaming checks access at consumer demand
and after the upstream read, without prefetching a downstream chunk. Revocation
cannot undo an effect already dispatched or bytes already delivered.

The [Studio epoch evidence](../../evidence/studio-epoch-2026-10-06.json)
records a later accepted source checkpoint with 64 passing focused tests,
a clean control typecheck and two independent reviews. Tickets and sessions
must carry the actor's durable management revocation epoch. Entry and switching
preserve that original epoch; requests and undelivered streams recheck it with
current membership. Production issuance and proxy access use the shared control
catalog. The regression exercises two database connections in one process,
not a separate-process runtime. Legacy credentials without an epoch are refused.
The earlier 52-test record remains historical after this integration changed
its source scope. Actual integrated Studio/vendor/Auth behavior is still open.

The management-security delta was integrated without importing its baseline
commit. Its separate server and console checkpoint reports 77 native Auth and
browser checks, 376 repository tests and clean type/build checks on its own
source. This does not accept the merged checkout or complete SB-06: native MFA
support in the administrative CLI remains a dependent task. Its raw host-wide
container inventory comparison was false and remains visible; exact fixture
resource cleanup was successful. Root's concurrent source helpers completed
during that window, but the missing inventory IDs were not recorded, so the
false comparison cannot be conclusively attributed to those helpers.

The [merged Bun component run](../../evidence/integrated-bun-2026-10-06.json)
then passed all 420 repository Bun tests without failures or skips, plus the
control typecheck, in the bounded public verifier image. Exact test containers
and image were removed and their absence checked. This expands component
verification after the merge; it does not repair the open Python FIFO gate or
accept native application startup, full recovery or the integrated live Studio.

The [merged capability registry component run](../../evidence/sb01-merged-2026-10-06.json)
passed 37 focused tests and the control typecheck in the bounded public image.
Registry validation initially refused the catalog checksum after MFA changed
its schema. Integration deliberately updated that one binding to the current
catalog source and structural validation then passed. All 105 acceptance and
production rows remain unproven. The prior task acceptance was not transferred
to this merged source; its independent integration review is pending. The
upstream reference metadata packet retains unresolved image and parser
diagnostics, so structural validation does not certify that runtime reference.

## Full completion contract

All product backlog entries remain required at their declared scope. Source
checks and isolated fixtures are useful evidence, but do not substitute for
any of the following outcomes.

| IDs | Required result and evidence |
|---|---|
| SB-01, SB-02, SB-03 | Versioned truthful capabilities, source-bound proof, nonmutating unsupported-host refusal and applicable security release checks |
| SB-04, SB-05, SB-16, SB-17 | Coherent database/object capture, complete encrypted fresh-host recovery, management recovery and safe handling of external effects |
| SB-06 | Native management MFA, server-side rate/assurance enforcement, stale-access denial, credential-transfer policy and tested recovery procedure |
| SB-07 | Audited deletion, retention and resource purge with neighbor safety and interruption reconciliation |
| SB-08, SB-18 | Guided DNS/TLS/email, actual browser/Auth/WebSocket and renewal probes, plus host maintenance/reboot/reconnection evidence |
| SB-09, SB-10, SB-15 | Pooling and migration budgets, explicit import inventory, schema/ACL/RLS preservation and replayable developer workflow |
| SB-11, SB-13a | Original Supabase feature admission and dedicated placement, with real SDK flows and Cron/Queues/Vault/TUS continuity/recovery evidence for each enabled feature |
| SB-12, SB-14 | Sustained mixed workload, noisy-neighbor and preview isolation, bounded disk/WAL/log failure behavior and usable diagnostics |
| SB-13b and phase E | Fenced second-host movement with one writer, reviewed failed cutover, declared recovery targets and separately tested availability/PITR profiles |
| Release integration | Final merged-source tests, clean supported-host installation, reproducible public distribution, current CI and actual pilot/soak results |

Keep failed, missing, skipped and stale required evidence visible. Update the
durable evidence ledger only after inspecting actual artifacts and independent
review. The complete programme stays open until every required outcome has
current evidence at its own scope.
