[العربية](project-layout.ar.md)

# Project layout

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

## Current authority and verification

The [project goal](../../PROJECT_GOAL.md), [product plan](../engineering/plans/2026-10-03-product-and-portability-plan.md) and [execution method](../engineering/plans/2026-10-03-gauntlet-execution-method.md) define current development. The [native placement contract](../engineering/plans/2026-10-03-native-placement-identity.md) separates experimental dedicated placement from the existing shared runtime. The [ledger](../engineering/gauntlet-ledger.json) records source-bound fragments, passes and losses; local packet references are not distributed public evidence. Dated September plans and checkpoints remain historical records.

The Docker-only source verification command is:

```sh
sh deploy/verify/run.sh
```

Run it from the repository root. It creates a disposable bounded verifier, executes without network access and writes evidence under the ignored `.lab/` directory. See [verification scope](../../deploy/verify/README.md) for its limits. CI also exercises the disposable Docker installation and empty-host acceptance; source gates do not establish complete recovery, independent-host support or production readiness.

Local `.lab/` evidence and `.secrets/` are private state, not publication artifacts. Dependency directories and build outputs are generated. They are not part of the public source tree or a substitute for reproducible release artifacts.
