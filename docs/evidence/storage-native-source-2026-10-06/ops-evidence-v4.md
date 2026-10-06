# Independent operational source review, version 4

Reviewer: native-source-ops-v4. Axis: operational-correctness. Verdict: fail.
Date: 2026-10-06.

The three approved source checks passed, but NS3 has two unresolved runner-control defects. I found no blocking source defect in the narrower NS1 and NS2 contract. This verdict does not accept original-native SS3, installed integration SS4, shared migration or full product SS5.

## Independence and rules

I received the frozen input packet in a fresh independent context. I did not inspect prior reviewer reports, verdict artifacts, evidence notes, workflow status/export, journals, task inventories or other agents. I read checkpoint-workflow SKILL.md, references/roles.md, the Codex adapter and the report schema in references/cli.md. The home and repository AGENTS.md files are absent on this filesystem; the supplied prohibition on long dashes was applied. Existing graft cards were read before actual source navigation. The graph was not refreshed.

The full scoped product and source plans were architectural input. Their historical acceptances were treated as historical context. No implementation, profile or helper was changed. No Docker, service, VM, network, original-native resource, or additional dynamic reproduction was used. Only this report and evidence note were authored by this reviewer, apart from the approved runner's own logs and accounting.

## Actual source inspection

I inspected the actual scoped storage_native_authority.py and its tests; disposable-storage-settlement-drill.py and its tests; storage_write_settlement.py and its tests; the TypeScript receipt codec and tests; the Storage image pin, package and Bun dependency pins; PROJECT_GOAL.md; the full product and Storage source plans; the original writer source map; and the relevant Runtime implementation and launch/publication callers.

NS1: the matrix retains all 29 case IDs. Fourteen entries expose helpers, nine SDK/HTTP helpers and five local controller/observation helpers; fifteen retain pending implementation descriptions. Lost stop delivery and actual generation mutation remain separately declared pending stages. Missing real prerequisites and disabled features remain visible, including both standard and signed prerequisites for signed cases. The helper results, source receipt comparator and structural observation validator all keep native admission false. The execute CLI refuses before allocation.

NS2: observation parsing binds exact writer CIDs, enrolled process start ticks and namespaces, image configuration ID with registry pin association, effective configuration digest, owner/runtime/operation labels, mount identity, restart policy, complete daemon inventory and protected neighbors. The separate database incarnation includes its own image association, configuration, start and process identity. Parsed stopped writers are restored to durable manifest order. The private owner ledger persists and fsyncs stop intent before the stop command, rejects interrupted or corrupt records and prevents ambiguous automatic stop replay. Direct reconciliation requires the locked completed observed-stop record for the same spec, validated detached stopped observations, descriptor-bound original file capture, empty database sessions/prepared transactions and identical observations before and after capture. Enabled TUS/multipart/queue/Redis/provider barriers remain unresolved. The installed guard is an unconditional refusal before subprocess or original resource capture. The source contract and strict codec never install a native verifier.

NS3: the Runtime proposal names real launch, start and activate_services spans, their direct and indirect callers, complete shared coverage, before-effect intent, post-effect reobservation and final publication authority. Actual Runtime.launch can inspect, remove, start, create volumes and run containers; Runtime.start starts shared Storage; activate_services mutates tenant configuration and atomically publishes endpoints. These are genuine future integration targets. The proposal also retains external installer/restart/Catalog, snapshot-holder and migration obligations. It has not installed a guard. This reviewer provides one independent operational review; I do not assert another reviewer's result.

## Approved checks executed sequentially

Each command used the exact profile argv:

```text
env LC_ALL=C PYTHONUTF8=1 PYTHONMALLOC=malloc /home/sbarah/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 /home/sbarah/.codex/sbarbase-settlement/20261006/native-source-v2/bounded-checks.py --check python
env LC_ALL=C PYTHONUTF8=1 PYTHONMALLOC=malloc /home/sbarah/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 /home/sbarah/.codex/sbarbase-settlement/20261006/native-source-v2/bounded-checks.py --check hybrid
env LC_ALL=C PYTHONUTF8=1 PYTHONMALLOC=malloc /home/sbarah/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 /home/sbarah/.codex/sbarbase-settlement/20261006/native-source-v2/bounded-checks.py --check bun
```

| Phase | Exit | Executed | Failed | Skipped | Observed peak group RSS | Own raw log | Raw SHA256 |
|---|---|---|---|---|---|---|---|
| Python | 0 | 144 | 0 | 0 | 29134848 bytes | check-python-35.log | 171ae01b2264ac1f82f787bcdbcc27621c05b1c8ebb343698cdb67dad4cb6e36 |
| Hybrid | 0 | 3 | 0 | 0 | 94457856 bytes | check-hybrid-36.log | 00f308a1b492a77953e37253c33513ed3867b169f5b6bcb28bf880aa94c1521a |
| Bun | 0 | 78 | 0 | 0 | 63713280 bytes | check-bun-37.log | ca6a81e0e5e27b71b38cfe4d5dce43a574400d819a20f7250401097887bb080d |

All three results reported native_accepted false and reason null. Final shared cumulative counters reported 7.324388106004671 active seconds and 397293 output bytes. These counters include the shared role's previous consumption; they are not this reviewer's exclusive totals.

The checks discriminate corrupt/partial journals, lost acknowledgements, stale receipt/fence/inventory context, process/database/config/image mismatches, running or changed stopped observations, file replacement/link/identity and byte limits, orphan/version reconciliation, disabled prerequisites, strict cross-language wire data and negative mock protocol behavior. The hybrid phase builds fixture syntax with external dependencies and runs mock protocol units plus a Python-to-TypeScript receipt comparison. None proves original service behavior.

## Resource-control assessment and findings

The profile declares Python address space 268435456 bytes, Bun address space 8796093022208 bytes, aggregate group resident memory 805306368 bytes, each check 120 seconds, shared active time 180 seconds and combined output 16777216 bytes. The wrapper verifies its frozen bindings, serializes cumulative accounting with flock, creates an owned process session, applies address-space/CPU/file-size limits and samples process-group RSS while draining pipes. run-python.py selects exactly three hybrid cases and raises Bun's separate address-space ceiling immediately before exec; the pure Python phase retains its small hard ceiling.

Two static paths prevent a claim that these declared controls are complete:

1. OPS4-01, major: bounded-checks.py lines 33 and 55 through 61 supervise only while output pipes remain registered. Closing both pipes while keeping the process alive exits monitoring. The ensuing three-second wait can raise before group cleanup and shared accounting. There is no enclosing unconditional cleanup. A sleeping child is not bounded by CPU time. This can leave an owned process alive and its consumption unrecorded.
2. OPS4-02, major: bounded-checks.py lines 45 through 58 check the old output size, then retain whole chunks without clipping. After crossing the cumulative cap the wrapper kills the group but still retains drained bytes and writes the entire oversized log. The child file-size limit does not constrain pipe output or the supervisor log.

These triggers were established by static control flow inspection. No adversarial reproduction was executed because additional dynamic probes require separate freezing. Normal approved runs did not exhibit either failure. The findings concern promised exceptional-path controls, not invented failures in the actual passing tests. Their repairs and any runner-specific checks require a new frozen profile; this reviewer has not changed one.

RSS monitoring is sampled process-group observation, not a kernel cgroup memory boundary or protection against deliberately detached processes. The inspected approved cases do not create detached native resources. This review makes no broader sandbox or malicious-child guarantee.

## Final identity verification

Immediately before authoring the report, hosts.py review-state returned revision 46 and exactly the packet identities:

```text
plan_hash: 9af5054961109ad1f0cc09655b44d97929bb7c33093d071dcf9569aaec552d03
source_hash: 2534de8a2ca99f276e0d5bac035f9c312c010c4d22fcc033974987564e80d98c
```

Original-native settlement, forced/interrupted native case recovery and exact owned cleanup remain unrun. Installed launch/restart/publication authority, lifecycle consumers, all-tenant shared migration, enabled provider coverage, full merged/runtime/product acceptance and production remain open. A repaired NS3 source gate would not close those obligations.
