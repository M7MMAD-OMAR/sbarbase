[العربية](project-layout.ar.md)

# Project layout

Layout update, 2026-10-07: database workflow, administrative transfer, Storage settlement, lifecycle and recovery dependency sources have entered the integration tree. Final release source assembly and production acceptance remain open. This page locates source; [status](status.md) records test evidence and its scope. Product files must not depend on a developer's private machine paths.

## Historical verification records

Historical checkpoint, 2026-10-06: complete snapshot verification passed 1449 tests with no failures, errors, warnings or skips and independent actual review. Repairs cover SQLite and HTTPError response closure, tempfile ownership and expected test diagnostics. All 52 preparation cases also passed; native PostgreSQL startup, Cron/Vault restart continuity and physical restore remain unaccepted. See [current status](status.md) for CI results and evidence scope.

The separate public55 V3 role has actual MET: 25 component and 30 pump tests passed, original 290 and added 4 actual mode/byte captures matched, and all three known helpers were removed with no unresolved resources. Public BusyBox V8 help metadata also has actual MET: fixed-path Bash 5.3.3 and BusyBox 1.37.0 help returned zero, with its one helper removed. Neither result accepts private request operation, timeout timing/signal behavior, installed binary source provenance, candidate startup, cold/warm Vault or Cron continuity, physical restore or production.

The preserved warning-window V4 negative run reported 1447 ordinary tests OK but six ResourceWarnings, native 1 and observed window 839 for every warning. Three records have exact sqlite3.Connection type; three report tempfile.py line 484 with NoneType source. Observation windows are not allocation causes, and the three tempfile origins remain unknown. Fresh complete source/identity/mode/full bindings, final-revision CI and manually dispatched empty-host acceptance remain required; published 0e745394 CI and Website results retain only their earlier revision scope.

Sbarbase is a development project built on original Supabase services. The repository separates the control plane, operator tooling, container packaging, verification and public documentation. This map describes the tree, not production acceptance.

| Path | Purpose |
|---|---|
| `src/` | TypeScript control plane, catalog, gateway and management API |
| `ui/` | Operator console; upstream Studio handles environment administration |
| `lab/` | Python operator/runtime tooling, fixtures and integration checks; read its README before running container operations |
| `tests/` | TypeScript verification cases |
| `deploy/` | Docker packaging, server scripts and isolated verification entry points |
| `fragments/` | Focused implementation fragments and development notes |
| `docs/explain/` | Design explanations and their limits |
| `docs/guides/` | Operator procedures |
| `docs/reference/` | Status, configuration, API and lookup material |
| `docs/decisions/` | Recorded design choices and reconsideration conditions |
| `docs/engineering/` | Dated mechanism notes, current plans, reviews, handoff and evidence ledger |
| `docs/evidence/` | Historical published live-probe results; each retains its original scope |
| `website/` | Public documentation/product website and its separate build workflow |
| `film/` | Explainer production sources |
| `graft/` | Source context graph with file and symbol references |

The root package declares version 0.2.0 as a development snapshot. [Changelog](../../CHANGELOG.md) preserves the historical 0.1.0 source release. [Status](status.md) is the current capability summary, while [deployment readiness](deployment-readiness.md) distinguishes rehearsed workflows from remaining gates.

## Additional integration sources

These paths exist in the inspected integration snapshot. Their presence does not establish native operation or completion of the full capability.

| Area | Source locations |
|---|---|
| Database workflow | `src/control/database-workflow.ts`, `lab/pooler_migration_contract.py` |
| Administrative transfer | `src/control/transfer-history.ts`, `lab/transfer-history-check.py` |
| Storage settlement | `src/control/storage-settlement-contract.ts`, `lab/disposable-storage-settlement-drill.py` |
| Lifecycle and authority | `src/control/lifecycle.ts`, `src/control/lifecycle-authority.ts`, `lab/lifecycle_native_authority.py` |
| Recovery dependencies | `src/control/lifecycle-recovery.ts`, `lab/lifecycle_recovery.py`, `lab/recovery_bundle.py` |

The [integration programme](../engineering/plans/2026-10-06-integration-programme.md) retains the full completion requirements. Ignored local development packets are not dependencies of the published product.

## Native startup preparation sources

These eight public files are integrated preparation tooling and its source regressions. They do not constitute an accepted candidate server or private transport. Read [the preparation contract](../../deploy/verify/native-startup-preparation.md) before any native operation.

| Source | Responsibility |
|---|---|
| `lab/native_startup_materials.py` | Typed material and retained-volume handoff |
| `deploy/verify/native_factory_startup_inspect.py` | Bounded prepare-only executor and exact native admission |
| `deploy/verify/native_startup_contract.py` | Shared public identity, projection and refusal contracts |
| `deploy/verify/native_startup_guardian.py` | Independent ownership and deadline guardian |
| `deploy/verify/native-startup-preparation.md` | Preparation scope, declared inputs and operator limits |
| `lab/test_native_startup_materials.py` | Material regressions, minimum 14 |
| `lab/test_native_factory_startup.py` | Executor regressions, minimum 9 |
| `lab/test_native_startup_guardian.py` | Guardian/process isolation regressions, minimum 29 |

`lab/test_notification_producers.py` separately asserts the exact expected startup-failure diagnostic. The guardian test fixture restores the caller's original subreaper state, including nested test failure cleanup. Full source results and native preparation results remain separate evidence gates.

## Private SQL completion sources

These public source components define the finite private SQL completion protocol. The complete frozen 881-file public source run passed 1504 tests with no failures, errors, warnings or skips; independent actual review and root closure passed. See the [public source regression evidence](../evidence/private-pump-regression-2026-10-06.json). The historical CI result, `6c86`, covers the baseline before these four additions. For native execution, installed native client provenance, private startup/session authority, the owned restart lease, Cron/Vault cold/warm continuity and full restoration remain UNRUN and unaccepted.

| Source | Responsibility |
|---|---|
| `deploy/verify/private_psql_producer.py` | Fixed selector encoding, exclusive namespace guards, control framing and transfer budgets |
| `deploy/verify/private_request_pump.py` | Bounded request/completion lifecycle, closed outcomes and refusal on uncertainty |
| `lab/test_private_psql_producer.py` | 31 public component regressions |
| `lab/test_private_request_pump.py` | 30 public pump regressions |

## Current authority and verification

The [project goal](../../PROJECT_GOAL.md), [product plan](../engineering/plans/2026-10-03-product-and-portability-plan.md) and [execution method](../engineering/plans/2026-10-03-gauntlet-execution-method.md) define current development. The [native placement contract](../engineering/plans/2026-10-03-native-placement-identity.md) separates experimental dedicated placement from the existing shared runtime. The [ledger](../engineering/gauntlet-ledger.json) records source-bound fragments, passes and losses; local packet references are not distributed public evidence. Dated September plans and checkpoints remain historical records.

The Docker-only source verification command is:

```sh
sh deploy/verify/run.sh
```

Run it from the repository root. It creates a disposable bounded verifier, executes without network access and writes evidence under the ignored `.lab/` directory. See [verification scope](../../deploy/verify/README.md) for its limits. The exact published `0e745394` CI run passed disposable Docker installation; its optional manually dispatched empty-host gate was SKIPPED and still needs a fresh dispatch. Source and documentation edits need fresh gates; source gates do not establish complete recovery, independent-host support or production readiness.

Local `.lab/` evidence and `.secrets/` are private state, not publication artifacts. Dependency directories and build outputs are generated. They are not part of the public source tree or a substitute for reproducible release artifacts.
