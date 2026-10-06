# Independent operational source review

Reviewer: `native-source-ops-v5`. Axis: `operational-correctness`.
Verdict: pass for the frozen source checkpoint covering NS1, NS2 and the operational review portion of NS3. No unresolved findings were identified in that scope. This report supplies one independent critic, not verification of the other critic's existence or result.

SS3, SS4 and SS5 remain open. No original Storage, PostgreSQL, Docker, VM, network, installed authority, remote provider, shared migration or consumer runtime proof was obtained. Private files and private supervisor processes are source fixtures only. Native admission remained false in every approved check.

## Identity

Input: `ops-packet-v5.json`, dispatched revision 64.

The required read-only `hosts.py review-state --run /home/sbarah/.codex/sbarbase-settlement/20261006/native-source-v2/run --checkpoint source` was executed immediately before preparing this report. It returned revision 64 and exactly these packet identities:

- Plan: `da6738391268ea0dcba1edf5f66b526ab2ab186d0444a5dec57df842dd6a9bc8`.
- Source: `ec66680820c951169cdc9ca9447bda7e34e222d8e5f3285cd260f3e3e9f16bc2`.

A second review-state check after saving the artifacts returned revision 65 with the same exact plan and source hashes. No workflow status, verdict or journal was read.

Static file hashing also confirmed the reviewed helper bytes:

| Artifact | SHA256 |
|---|---|
| profile.json | 0772c8ba0a9f0d19da10c1dde6ef1cd27489e3c55d0d39b1a572d9f874b481c0 |
| bounded-checks.py | 8506d7fd92d2c50f27e597b7f59d4f1172cbb21dcffc06e003ce935e0404d6c0 |
| run-python.py | 16f5db8adf0d854e0a17aac284607da0140cf5e65296423519f24ecb0e0db9a3 |
| control-checks.py | e497aa7c1e06a6aba0523da5fd3203bf83310e4d8c62a732cbd59e748b7bb5e1 |

## Rules and independence

Read `/home/sbarah/.codex/AGENTS.md`. It imports `/home/sbarah/AGENTS.md`, which was attempted and is absent. The current checkout has no root AGENTS.md, and the bounded initial rules search found no descendant AGENTS.md in this checkout. The user-supplied no long dashes instruction was followed in authored commentary and artifacts.

Read `/home/sbarah/.codex/skills/checkpoint-workflow/SKILL.md` and its `references/roles.md`, `references/host-codex.md`, `references/hosts.md` and `references/cli.md`. Read `/home/sbarah/.codex/worktrees/09c0/sbarbase/.agents/skills/graft/SKILL.md` and the existing graft cards before scoped source navigation. Existing drill card spans are older than the actual drill; actual scoped source was inspected directly after locating those cards. The graph was not refreshed.

No implementer conversation was inherited. No previous candidate review report, evidence note, verdict artifact, workflow status/export, journal or task inventory was read. No agent inventory was requested. The full scoped product/source plans contain historical acceptance statements and source-repair descriptions; these authorized architectural inputs were read, and their historical results were not used as current acceptance evidence. Rules discovery printed filenames from other directories without reading their contents. The supplied profile contains descriptive freeze status and cumulative supervisor counters appear in new command summaries; neither supplied a prior review verdict.

No implementation or profile file was modified. The only executed test commands were the four exact profile commands, sequentially, with no additional dynamic reproduction.

## Actual checks

Each command used the exact profile prefix `env LC_ALL=C PYTHONUTF8=1 PYTHONMALLOC=malloc /home/sbarah/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`.

| Sequential phase | Fixed script arguments | Actual result | Evidence | SHA256 |
|---|---|---|---|---|
| Python | bounded-checks.py --check python | Exit 0, 144 tests, 0 failed, 0 skipped | check-python-48.log | 54b557cfd991d7844891fa0a545d8c46c9d7aac73ddf273ad7ef421eb43766b4 |
| Hybrid | bounded-checks.py --check hybrid | Exit 0, 3 tests, 0 failed, 0 skipped | check-hybrid-49.log | bbaa862b57a235565410c1ae0fac82fbbd63bffd5a05aaf29d9cac435badb433 |
| Bun | bounded-checks.py --check bun | Exit 0, 78 tests, 0 failed, 0 skipped | check-bun-51.log | ab31adc9dc80de933548a7d39f6f310ab43e0e6d53d332b0120450964310ef31 |
| Control | control-checks.py | Exit 0, 2 checks, 0 failed, 0 skipped | control-check-52.json | b1dd7b864b56f28e0a7888619267cf59b68935efc0025e0e72fc9112a67a3ee7 |

Raw summary inspection independently found Python `Ran 144 tests` and `OK`, hybrid `Ran 3 tests` and `OK`, and Bun `78 pass`, `0 fail`. The helpers reported `native_accepted: false`. These are the newly executed command logs, not reused earlier results. Global log sequence numbers reflect the serialized shared budget and are not reviewer-specific identifiers.

Observed peak process-group RSS was 29,048,832 bytes for Python, 114,020,352 for hybrid, and 63,778,816 for Bun. The Bun summary reported cumulative role accounting of 12.870392438006158 active seconds and 609,981 output bytes, before this review's control command. The control command charged its own elapsed time and capture bytes through the same locked budget. These observed figures stayed far below the frozen 180 seconds and 16 MiB ceilings.

## Operational assessment

NS1: `lab/disposable-storage-settlement-drill.py` retains 29 case IDs. Its matrix explicitly distinguishes 14 helpers, nine SDK/HTTP helpers and five local observation/controller helpers, from 15 pending implementations. Every row separately lists required and missing prerequisites, enabled-feature availability and `native_accepted: false`. Lost-stop-acknowledgement delivery and installed-generation mutation remain explicit pending case stages. A generic missing-guard refusal is not accepted as changed-generation success. The tests exercise retained IDs, disabled signed/TUS/queue/Redis/provider prerequisites, and negative protocol observations. Syntax, protocol mocks and cross-language fixtures are never counted as original-native success.

NS2: `lab/storage_native_authority.py` and its tests bind exact original registry-pin/config-ID associations independently for Storage and database, effective configuration, full daemon container set, protected neighbor fingerprints, CID/start identity, process PID/start ticks and namespaces, exclusive mount/root/device/inode/mount identity, operation and generation. Database OID, configuration, image and init process are checked through capture. Sorted Docker CID observations are returned in enrolled writer order only after complete validation. Mixed or ambiguous writer state refuses. The private owner ledger requires a durable fsynced intent before stop, distinguishes acknowledgement from observed exit, rejects identity drift/corruption/incomplete records and refuses automatic stop replay. Reconciliation requires a completed exact owned stopped observation before file or SQL reads, detaches observer material and checks immutable observations again afterward.

The file parser observes original bytes, SHA256, xattrs, numeric ownership/mode and nanosecond mtime through descriptor-relative reads without rewriting row/file metadata. Missing versions, orphan/staging files, sessions/prepared transactions and enabled unimplemented barriers stay unresolved. A same-version size match or parsed observation never grants native proof. Shared and fixture enrollments are excluded from the local native controller. Every live command and top-level authority method reaches the missing installed guard before external execution; the approved tests verify no subprocess calls or ledger allocation on that refusal.

The companion `lab/storage_write_settlement.py` fixtures persist pending fence/stop/effect states, reobserve exact identity, retain uncertain consumer outcomes and reject stale receipt consumption. `src/control/storage-settlement-contract.ts` detaches and freezes closed source objects, preserves Catalog inventory order, and always reports `nativeAdmitted: false`. Its native entry point unconditionally refuses, including caller-supplied authority callbacks and mutually matching forged native labels. Actual Python and Bun tests cover these limits.

NS3: The mandatory profile and three scripts were inspected. The supervisor keeps monitoring until process exit and pipe closure, clips retained output to the remaining allowance, reserves metadata bytes, kills its owned group in unconditional cleanup, waits for its direct child and fsyncs cumulative accounting under a lock. It binds declared source/helper bytes before execution. The fixed pure Python child has soft and hard AS limits of 256 MiB; Python supervisors retain a 256 MiB soft AS limit with the documented 8 TiB hard control-plane reservation. Hybrid setup permits only fixed Bun source subprocesses and raises Bun's separate limit immediately before exec. Bun's own AS ceiling is 8 TiB and the supervisor monitors an aggregate owned group RSS ceiling of 768 MiB. This is controlled supervision of trusted fixed tests, not an OS sandbox for arbitrary escaping descendants.

The control script uses two real private processes and a reduced copy of the supervisor policy. Output-cap observation retained 3,072 log bytes plus 1,024 reserved bytes, exactly the 4,096-byte local budget, and reported a bound failure with runner return code 1. Closed-pipes observation retained zero bytes and terminated its sleeping child at 0.17519238300155848 seconds with runner return code 1. Both private identity markers existed and neither child survived the runner. The enclosing two-check result correctly treats these expected negative results as passed control regressions. They do not exercise native Storage writers. Their temporary policy copies and private children are declared source controls, not changes to the frozen source/profile.

The read-only Runtime proposal in `docs/engineering/plans/2026-10-06-storage-write-settlement.md` corresponds to actual `Runtime.launch` at lines 378 through 426, `Runtime.start` at lines 448 through 490 and `Runtime.activate_services` at lines 589 through 650. Actual launch calls also occur from mail/Auth reconciliation, realtime/functions start and management. The proposal covers before-effect guard acquisition, replacement removal/start/volume/run, shared startup and database publication, tenant POST/PATCH and final endpoint publication, with same-authority revalidation and current-generation checks. It explicitly retains installer/systemd/worker, Catalog consumer, coherent snapshot and all-tenant migration obligations. None of those integration paths was modified or executed here.

## Completion boundary

The pass covers reviewed source preparation and its frozen bounded checks only. Original native case execution and interruption/cleanup, installed persistent launch/restart/publication authority, coherent backup/restore and transfer/purge integration, all-tenant shared migration, provider/queue/Redis coverage and full merged product/runtime acceptance remain unproven. This review does not authorize their execution or publication.
