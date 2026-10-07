The SB-01 source-only integration contract is met at the inspected portable identity. No functional registry regression or proof transfer was observed. This verdict establishes registry and planning evidence boundaries only. It grants no runtime, full portable source runner, G0, G12, SB-13a, Cron, SDK, recovery, workload, or production acceptance.

Review completed on 2026-10-06. Root: `/home/sbarah/R/Projects/P/sbarbase`. Original registry comparison: `/home/sbarah/.codex/worktrees/d9bb/sbarbase/deploy/capabilities/registry.json`. The imported delta was identified as `bbc6e302b90cbac13fe2bbedf9603a0c33ca05a2`. Artifact identity was visible; this was an independent fresh review, not a blinded comparison. No parent source was edited. External scripts, synthetic fixtures, report, and logs are retained in this directory. No Docker, database, network, runtime test, or full suite was run.

Repository AGENTS.md and the available graft instructions were read. `~/AGENTS.md` is absent on this host. Source locations were obtained through graft before opening their exact spans. All test and verifier commands used `systemd-run --user --quiet --scope -p MemoryMax=256M -p CPUQuota=25%`. Python calls also used `PYTHONDONTWRITEBYTECODE=1`; fixture tests used TMPDIR in this external directory.

The actual initial, post-CLI, and final identities all equal:

```json
{
  "algorithm": "sbarbase-public-source-v1",
  "files": 523,
  "sha256": "602283dbbd8db422e610b19f19299ca75bcabb893291352d5339bb21ff10664f"
}
```

The independent audit also rebuilt the sorted path, bytes, length, and numeric mode inventory without calling the registry identity function, and obtained the same identity. Raw evidence is in `identity-before.json`, `identity-after-cli.stdout`, `identity-final.json`, their `.exit` files, and `audit-result.json`. Each CLI identity command exited 0. The artifact was not stale and did not change during this review.

The parsed original and merged registries differ at exactly one data path, `/bindings/9/sha256`. This is `catalog-schema`, binding `src/control/catalog.ts`:

| Input | SHA-256 |
| --- | --- |
| Original registry binding | `ef7716b47d47ae130aa60409471fa84fb2b3cedcfdff7c3f7f5d2072a028b713` |
| Merged binding and actual catalog bytes | `7c0f817445854ca85b46653507339daf77bd6c20e20bc5fcc47595694eb82c2f` |

The independent audit directly hashed the actual catalog bytes. No capability, placement, requirement, claim state, evidence list, limitation, implementation inventory, or history pointer changed in registry data. All 35 capabilities and 105 placement rows preserve acceptance and production as `unproven`, with empty evidence arrays for both gates. `deploy/capabilities/proof` is absent and is not a symlink. The actual derived status agrees, with all 105 rows unproven and no structural errors. These assertions and the full independent inventory are in `audit.py` and `audit-result.json`.

Actual root observations:

| Command after the scope prefix | Exit | Observation and log |
| --- | --- | --- |
| `python3 deploy/verify/capability_registry.py validate` | 0 | 105 unproven rows, empty evidence, no errors; `validate.stdout` |
| `python3 deploy/check_plan.py` | 0 | Planning integrity passes; output explicitly says runtime acceptance is not established; `check-plan.stdout` |
| `python3 deploy/verify/capability_registry.py accept --capability SB-01 --placement native-dedicated` | 1 | Required current proof missing; `accept-SB-01-native.stdout` |
| Same scoped accept for `SB-13a` | 1 | Required current proof missing; `accept-SB-13a-native.stdout` |
| Same scoped accept for `native-cron-effects` | 1 | Required current proof missing; `accept-native-cron-effects-native.stdout` |
| Same scoped accept for `foundation-reference-distribution` | 1 | Required current proof missing; `accept-foundation-reference-distribution-native.stdout` |
| Same scoped accept for `public-release` | 1 | Required current proof missing; `accept-public-release-native.stdout` |
| `python3 -m unittest -v lab.test_capability_registry lab.test_plan_acceptance` | 0 | 37 tests, zero failures, errors, or skips; `focused-tests.log` |
| `python3 <external>/audit.py` | 0 | Registry delta, current root identity, all rows, retained records, and artifact checks agree; `audit.stdout` |
| `python3 <external>/counterexamples.py` | 0 | 13 actual bounded CLI observations matched expected outcomes; `counterexamples.log` |

`commands.log` and `run-probes.sh` retain exact root CLI commands. `counterexamples-result.json` retains exact command arguments, exits, refusals, and synthetic accepted scopes. Every observation has raw `.stdout`, `.stderr`, and `.exit` files. Positive synthetic cases are external consistency fixtures created without running the declared runtime commands. They establish that refusal tests reach the intended proof boundary, and grant nothing to the parent root.

The 13 final scratch CLI observations establish these boundaries:

- A complete synthetic SB-01 proof is accepted for its selected external fixture scope, exit 0. Changing source bytes then refuses with `source identity mismatch`, exit 1. Updating only the envelope source refuses with `raw observation disagrees`, exit 1.
- A complete synthetic binding baseline is accepted, exit 0. Changing actual catalog bytes refuses `catalog-schema: binding checksum mismatch`, exit 1. Deliberately updating the registry binding still refuses the old proof with `source identity mismatch`, exit 1. Updating the outer source, registry, and bindings hashes still refuses the old raw observation, exit 1.
- A synthetic native Cron fragment is accepted for that exact external scope, exit 0. Scoped SB-13a acceptance still refuses missing current proof, exit 1. Its five mandatory requirements are original startup, native identities, Cron/HTTP effects, SDK flow, and fenced recovery. The fragment supplies no complete admission transfer.
- Two synthetic local SB-01 acceptances validate, exit 0. Marking G0 passed with only that governance proof makes the relocated actual `check_plan.py` CLI refuse both local `foundation-reference-distribution` placements, exit 1.
- Two synthetic local SB-03 acceptances validate, exit 0. Marking G12 passed with only security proof makes the relocated actual planning CLI refuse 64 required coverage scopes, including both `public-release` placements, exit 1.

The focused 37-test regression run additionally confirms complete slice inventory cannot be narrowed, incomplete reference metadata cannot be overridden by a complete-looking foundation proof, source additions and mode changes alter identity, source mutation/deletion revokes accepted rows, stale and renewed observations refuse, and production cannot inherit ordinary acceptance or unit proof.

Source spans reviewed include `deploy/verify/capability_registry.py:167-204` for portable identity and bindings, `:207-279` for required capability, slice, native, and test inventories, `:292-362` for exact proof and raw observation binding, `:365-417` for derived status and source-change invalidation, `:420-432` for local coverage and G12's additional declared slice coverage, and `:435-477` for CLI reporting. Planning passed-slice coverage is enforced in `deploy/check_plan.py:23-104`. Relevant focused tests are `lab/test_capability_registry.py:146-158`, `:262-293`, `:312-325`, `:356-364`, `:409-416`, and `lab/test_plan_acceptance.py:95-117`. Graft furnished these exact spans.

The retained root records were checked independently. `sb01-merged-validate-before.json` is a pre-rebinding refusal with zero rows and `catalog-schema: binding checksum mismatch`; it has no source field and cannot be treated as current acceptance. `sb01-merged-final-status.json`, `sb01-merged-validate-after.json`, and `sb01-merged-native-refusal.json` carry the actual current identity and all 105 unproven, empty-evidence rows. The native record's sole error is the expected SB-13a missing-proof refusal. The binding delta record agrees with the actual parsed one-field comparison.

Current `docs/evidence/sb01-merged-2026-10-06.json` is consistent with this limited component scope: `passed-component-verification-review-pending`, 37 focused tests, structural validation, planning integrity, and all acceptance and production rows unproven. It explicitly says previous task proof cannot accept merged source and full-source verification is pending. The actual copied `tests.log`, `types.log`, `result.json`, and `cleanup.json` SHA-256 values match its declarations. The copied focused test log is byte-identical to `/home/sbarah/.codex/sbarbase-integration/20261006/studio-check-a757258fdbe9/tests.log`. This review validates record consistency, not the historical runtime cleanup action or a new typecheck run.

The imported `docs/engineering/reviews/2026-10-06-sb01-capability-registry.md` and `docs/evidence/sb01-20261006/focused-tests.log` remain historical. The old report names 506 files and source SHA-256 `1b447fe3ad525087ba8ac79836757d8e1fd46401493bf0bff0e68b80c03337e3`, which differs from the actual merged identity. Its retained log hashes to the declared `8bde2654b9d3fe0dcd51a4a2f4d04989c822a065fc0c083a90f5f8f32c1f8cd0`. Neither file is a detached proof reference. The current registry supplies no evidence links to them. Source, canonical registry, bindings, execution identity, timestamps, scope, and raw observation checks prevent transferring or merely rebinding their earlier verdict to the current merged artifact. Checksums remain self-attested integrity checks; they do not prove execution provenance or reviewer independence.

Findings by priority:

- No P0, P1, or P2 functional registry integration defect was observed in this scope.
- P3, nonblocking documentation clarity: the retained old report opens with an unqualified current-sounding title and observations at `docs/engineering/reviews/2026-10-06-sb01-capability-registry.md:1-27`. Its explicit different source identity and integration warning make it historical on inspection, and the current merged summary correctly prevents transfer. A future authorized documentation change could add an explicit historical imported-artifact label and distinguish the delta commit from current merged portable identity in the merged summary. No source edit was made for this observation.
- The largest remaining acceptance gap is the already disclosed standard portable source runner Python FIFO repair plus fresh full-source verification. Focused registry/plan tests and historical imported proof cannot fill that gap. Runtime execution contracts, unresolved reference image metadata, native admission, independent-host recovery, full release/soak coverage, and production remain unproven as declared.

Two reviewer harness mistakes were retained rather than hidden. `audit-attempt-1.stderr` records an initial assumption that the pre-rebinding refusal record would contain a source field; it intentionally does not. The final audit handles and checks the pre-rebinding refusal explicitly. `counterexamples-attempt-1.log` records an initial two-placement Cron synthetic helper fixture whose second command freeze invalidated the first proof. The verifier correctly refused the stale first proof. The external harness was corrected to a single Cron scope, and all 13 final observations passed. Neither event involved parent source changes or a registry failure.

Graft saved approximately 38,812 tokens across five retrieval calls. All report and observation artifacts are outside parent source.
