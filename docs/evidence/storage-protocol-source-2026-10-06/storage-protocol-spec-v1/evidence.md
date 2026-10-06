# Independent specification evidence

Reviewer: storage-protocol-spec-v1. Axis: specification. Verdict: pass for the NS1/NS2/NS3 source checkpoint only.

Source hash: 1aac0f7909f02fc21a3ddebdfd619a1fc8d0c760347d79e2991e61299137c772.
Plan hash: f9831194ca3341e5098eea524704988ac088e208359bf49887a00c24414a9dc4.
The actual hosts.py review-state output in tool-review-state.json returned revision 14 and both matching hashes immediately before this report.

## Rules and inspection

/home/sbarah/AGENTS.md is absent. I read /home/sbarah/.codex/AGENTS.md, the review input.json, /home/sbarah/.codex/skills/checkpoint-workflow/SKILL.md, its references/roles.md, references/host-codex.md and the report schema in references/cli.md. I also read the repository .agents/skills/graft/SKILL.md. No additional AGENTS.md was found at the checked project and scope ancestors. Authored evidence uses no long dashes.

I inspected actual bodies of all 16 scoped files:

- lab/storage_native_authority.py
- lab/test_storage_native_authority.py
- lab/disposable-storage-settlement-drill.py
- lab/test_disposable_storage_settlement_drill.py
- lab/storage_write_settlement.py
- lab/test_storage_write_settlement.py
- src/control/storage-settlement-contract.ts
- tests/storage-settlement-contract.test.ts
- docs/engineering/plans/2026-10-06-storage-write-settlement.md
- docs/engineering/reviews/2026-10-06-storage-writer-source-map.md
- lab/storage-image.lock.json
- lab/durable_runtime.py
- package.json
- bun.lock
- PROJECT_GOAL.md
- docs/engineering/plans/2026-10-03-product-and-portability-plan.md

I read the frozen profile.json, bounded-checks.py, run-python.py and control-checks.py before executing their fixed commands. I did not edit source, read another reviewer's output, import this report, use workflow status/export, or read the workflow journal. Historical summaries embedded in mandatory scoped documents were not used as independent evidence.

## Executed checks

Each argv in input.json was executed exactly once, without budget or helper changes. tool-python.json, tool-hybrid.json, tool-bun.json and tool-control.json retain the actual tool responses and supervisor stdout. The frozen supervisor verified its bindings and charged these executions to the shared locked 180 active-second, 16 MiB output role budget. Python had the declared 256 MiB address-space ceiling. Bun had its declared 8 TiB virtual ceiling and the supervisor's 768 MiB aggregate resident-memory ceiling.

| Check | Executed units | Failed | Skipped | Evidence |
| --- | --- | --- | --- | --- |
| python | 159 | 0 | 0 | python.log |
| hybrid | 3 | 0 | 0 | hybrid.log |
| bun | 78 | 0 | 0 | bun.log |
| control | 2 | 0 | 0 | control.json |

All four top-level commands returned exit 0. Python, hybrid and Bun supervisor reasons were null. Their observed peak group RSS values were 46776320, 129572864 and 63414272 bytes. These are source units and private supervisor controls, with native acceptance explicitly false.

The control log's reduced-policy children intentionally returned failures for output overflow and closed-pipe timeout. The actual assertions passed: the first retained 3072 bytes plus 1024 metadata bytes within a 4096-byte private limit; neither child survived its runner. These controls are no original Storage writer observations.

Raw hashes, independently checked after copying:

- python.log: c7941f3a2947021e2d8a92e3a404482a791009b89b0e0484cb326b29d874c32b
- hybrid.log: 3836fcd5a415711e2749c9b6bf3d758c21b974873e13f11489a9e7406514a098
- bun.log: d9b3d6e33236c757c661029cde91bb6af4729c44ec61c32a6bef9d875321c81e
- control.json: 85d7dd9841f723fea4dc485b977b1154cd0cb85f36b7c96b671e24357d23735b

## Specification assessment

NS1: The original CASES tuple retains all 29 IDs. case_matrix separately represents 14 helpers, 15 pending complete implementations, feature availability, missing prerequisites and concrete source stages. Disabled features do not remove required IDs. The new disconnect sends a partial declared body and applies socket reset; its result explicitly leaves server consumption and residual effects pending. The multipart source signs, creates, uploads, lists, completes and compares downloaded bytes. Exact part listings, response bounds and ambiguous failure handling are covered by units. It does not claim original database/version/cleanup settlement.

NS2: _installed_launch_guard at lab/storage_native_authority.py:350 always refuses. DockerLocalCommands._run, LocalNativeAuthority public operations and OriginalHttpTransport operations reach it before native effects. stop records fsynced intent before the original stop, distinguishes after-intent and after-stop-before-ack, and run_stop_fault identifies and reaps its owned controller. recover_stop observes pending outcomes without stop replay. Reconciliation requires exact owned observed-stop identity, detached stopped observations and unchanged original writer/database state across capture. Fixture receipt consumers retain pending outcomes; neither Python's lifecycle verifier nor the TypeScript native seam accepts supplied callbacks or JSON as installed authority. The exported SDK text remains preparation for later assigned execution, never a native admission mechanism.

NS3: The delta against frozen baseline 727850058d7fedfcd3991d10ebf4398c21c5d6b9 is retained in source-delta.patch, SHA256 330653073ecfbf9db370568b69d5cd82eb8f5098abc7357562a407f8d4df966e. git diff reported exactly five declared changed files, 689 insertions and one deletion. All other scoped files are unchanged against that baseline. This report supplies one fresh specification review; it does not certify completion of the separate second-reviewer gate.

The Runtime proposal matches actual Runtime.launch at line 378, Runtime.start at line 448 and Runtime.activate_services at line 589. The current bodies still contain native launch and tenant/publication effects. The proposal correctly requires persistent installed ownership before those effects, reobservation after work and a final same-authority publication check. It also retains direct caller, installer, systemd, queue/provider, consumer and shared all-tenant requirements. These paths were inspected, not executed or edited.

## Limits

No original native effect, network request, Docker command, service, database server or VM was executed. The SigV4 vector and protocol/mock units do not establish installed source provenance or original-service compatibility. Installed endpoint/bucket/credential binding and durable per-request intent are still pending. All 29 complete native cases, SS3, SS4 and SS5 remain unrun or unaccepted. This source-only pass grants no runtime, recovery, migration, product or production acceptance.
