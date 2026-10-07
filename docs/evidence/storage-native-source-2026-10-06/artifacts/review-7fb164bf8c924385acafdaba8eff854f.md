# Independent operational source review

Reviewer: native-source-ops-v3. Axis: operational-correctness. Verdict: fail.
The source checkpoint has one unresolved major correctness finding, OPS-V3-001.
All three approved checks passed. These results establish source checks only.
Original native SS3, SS4 and SS5 remain open.

## Contract and independence

Input was ops-packet-v3.json and the frozen profile.json. I read the
checkpoint-workflow skill, roles.md, Codex adapter and CLI schema, and the
applicable available rules. The requested /home/sbarah/AGENTS.md is absent.
The available ancestor rules and the supplied task require no long dashes.

I inspected the scoped Python authority, drill, source protocol and their test
files; the TypeScript receipt codec and its tests; the original image lock;
package and Bun lock identities; the source map; PROJECT_GOAL.md; the scoped
Storage and product plans; and actual Runtime launch, start, tenant publication
and relevant callers. Full scoped plans were authorized architectural input.
Historical acceptance statements in those plans were treated as historical
context and supplied no current acceptance.

I used existing graft cards first where available. The native authority and
new drill test were absent from that index. I did not invoke the refreshing
graft CLI or rebuild its graph. I did not read earlier candidate reviewer
reports, verdict artifacts, external evidence notes, journals, status/export
outputs or task inventories. This role received no implementer conversation.
No implementation or profile file was changed.

## Actual checks

The only test executions were the three exact profile commands, sequentially.
Each begins with env LC_ALL=C PYTHONUTF8=1 PYTHONMALLOC=malloc and the frozen
bundled Python path, invokes bounded-checks.py, and selects its declared check.
No additional reproduction, native SDK execution, network, Docker, database,
service, VM or retained-resource operation ran.

| Check | Total | Failed | Skipped | Exit | Raw log |
|---|---:|---:|---:|---:|---|
| python | 142 | 0 | 0 | 0 | check-python-25.log |
| hybrid | 3 | 0 | 0 | 0 | check-hybrid-26.log |
| bun | 78 | 0 | 0 | 0 | check-bun-27.log |

Raw SHA256 values returned by the supervisor:

- Python: a09f8f8aaff20fb29a7366dfc5bb6a67b43ab2a92710fecabd2e22e73167505a
- Hybrid: ff38cd5bb3c8c4b7a0f74473165296b4900d8efc9470cc9738fdcdc63d643456
- Bun: 0e43f1cd998b9218f69d83f4f3a762a233e6b5edfd15bfa2bd021cd412a8f593

The final wrapper result reported cumulative shared role active time
5.070447442013 seconds and output 263056 bytes. That cumulative ledger includes
other prior wrapper invocations; I did not inspect their logs or claim those
executions. Peak owned process-group RSS reported for my commands was 29196288,
127598592 and 62742528 bytes respectively. All results explicitly returned
native_accepted false and no bound failure reason.

## Execution controls

I read bounded-checks.py and run-python.py before execution. The supervisor
verifies its frozen bindings, takes an exclusive budget ledger lock, enforces
120-second per-command and 180-second cumulative active-time limits, combines
stdout/stderr into a shared 16 MiB output budget, and tracks aggregate owned
process-group RSS against 768 MiB. It creates its own child process group and
kills that group on a limit failure and after normal completion. Python has a
256 MiB address-space soft ceiling; the pure Python child also has that hard
ceiling. The hybrid Python child retains the declared larger hard ceiling so
the fixed Bun child setup can set Bun's separate 8 TiB virtual-space ceiling.
Bun is separately bounded by the same aggregate resident-memory monitor.
The hybrid loader selects exactly three named source tests and rejects a
different count. Its Bun subprocess shim rejects non-Bun commands and caller
preexec setup. CPU and file-size limits also apply to owned test children.

These controls are a trusted local supervisor, not an adversarial sandbox.
The RSS observation is sampled and process-group based; it does not authenticate
review identity or control a deliberately escaping process. The selected tests
use private temporary files, patched refusal boundaries and fixed source child
commands. No installed service admission follows from the supervisor.

## Requirement assessment

NS1: The matrix retains all 29 case IDs. Fourteen rows name actual helpers:
nine SDK/HTTP protocol helpers and five concrete local controller/observation
helpers. Fifteen rows explicitly retain pending implementations. Prerequisite
availability and implementation status are separate; disabled features keep
their rows. Lost acknowledgement and changed generation have explicit missing
fault stages. Helper success, supplied structural observations and fixture
receipts all retain false native admission.

NS2: Exact parsing binds writer CIDs, configuration, process start ticks and
namespaces, original image config/registry association, volume descriptor
identity, protected neighbors and original database configuration/incarnation.
OwnerLedger records and fsyncs intent before issuing stop, keeps ambiguous
stops for observation rather than replay, and refuses incomplete/private-file
identity failures. Direct reconciliation requires a matching observed stop,
detached stopped observations before and after capture, and matching original
DB authority. Private file capture preserves bytes, nanosecond mtime and
xattrs; row reconciliation retains orphan/missing/effect-barrier gaps. Missing
installed authority refuses before subprocess or ledger effects. However,
OPS-V3-001 makes a valid multiwriter observation unusable for reconciliation.
NS2 source correctness is therefore not accepted.

NS3: The approved source checks are frozen, bounded and meaningful, and passed
in this role. The read-only Runtime proposal names the actual launch/start/
tenant publication spans, current-generation checks, durable intent, immutable
reobservation and shared all-tenant requirements. Actual source confirms the
direct launch callers named in the proposal. It also contains direct restart
and publication paths, and the plan explicitly leaves complete installed
guard integration to SS4/SS5. This review is one independent critic; it does
not assert that the second critic passed or that shared integration exists.

## OPS-V3-001 source trace

The concrete source contradiction is:

1. validate_manifest accepts unique manifest writers in any order.
2. DockerLocalCommands.containers inspects sorted container IDs at line 370.
3. decode_inventory appends writers in inspection order at lines 142 and 198.
4. stopped_observation zips that result against manifest order at lines 229
   through 234 and requires positional CID equality.
5. LocalNativeAuthority.reconcile and direct SQL capture use this validator.

A reverse-CID manifest [b repeated 64 times, a repeated 64 times] with valid
matching enrollments and cleanly exited writers produces decoded [a, b]
observations from a sorted Docker inventory. The complete set passes inventory
decoding but fails the first positional comparison before file/row capture.
The existing native authority candidate enrolls only one writer, so the passing
checks do not discriminate this composition. This is a static counterexample;
I did not execute an unapproved probe. Current installed guards still prevent
all original effects, so this finding claims a source correctness failure and
future integration obstruction, not an observed native incident or bypass.

Repair should normalize to manifest order or compare complete unique writer
sets by CID. A frozen private regression should cover reversed two-writer
ordering and preserve missing, duplicate, foreign and running-writer refusal.

## Final identity

Before writing this report I executed the authorized hosts.py review-state
command for checkpoint source. It returned revision 36. A final recheck after
writing the report returned revision 37 with unchanged exact hashes, project
/home/sbarah/.codex/worktrees/09c0/sbarbase, and exact packet matches:

- Plan: 9af5054961109ad1f0cc09655b44d97929bb7c33093d071dcf9569aaec552d03
- Source: 6ecf6f0539a4865fbd8db515d6facabc4e00c7befbfa70c48f37adcfc67876f5

No source/profile repair or graph refresh occurred during this review.
Passing source checks do not close the finding, installed launch/publication
authority, original interruption/cleanup, enabled provider coverage, all-tenant
migration, consumer integration, full runtime/product acceptance or production.
