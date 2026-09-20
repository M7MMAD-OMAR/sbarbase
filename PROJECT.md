# Sbarbase: start here

Updated 2026-09-20. Workspace: `/home/sbarah/R/Projects/P/sbarbase`.

## Product and latest decision

Open source, downloadable Supabase-based administration for multiple projects, usually on one VPS. Preserve Supabase application compatibility and use its design system for the future UI. Installation operators are trusted; application visitors are not. No production platform or UI exists yet.

**Decision: continue a local feasibility experiment, not approve a production architecture.** Candidate hierarchy: installation > organization > project > environment. Ownership and server placement are independent. Each environment has its own database, identities and credentials. Candidate runtime: one PostgreSQL cluster with original Auth and PostgREST per environment. Share other services only after verification. This avoids rewriting authentication and may reduce repeated infrastructure.

Shared PostgreSQL roles and resources remain important boundaries. The lab uses shared canonical API roles and separate service logins. Independent PostgreSQL per environment remains the fallback if compatibility, isolation or recovery gates fail. No permanent project cap, 100-project promise, upstream adoption or distribution license is selected.

## What actually works locally

| Evidence | Verified scope | Important limit |
|---|---|---|
| [97 component checks](docs/evidence/four-environment-component-checks.json) | Auth, RLS, crossed tokens and database credentials across 4 environments | Stock PostgreSQL with a minimal auth.uid fixture |
| [44 current SDK/gateway checks](docs/evidence/four-environment-sdk-checks.json) | SDK CRUD/Auth, scoped publishable keys and revocation | Auth/REST only; management integration tested separately |
| [Retry checks](docs/evidence/retry-checks.json) | Recovery after 3 provisioning interruptions, existing data preserved | Selected creation phases only |
| [Restore check](docs/evidence/restore-check.json) | Logical restore into fresh database; source and neighbor preserved | Same cluster, selected data, not full recovery or PITR |

Twenty-one unit tests now pass, including organization authorization and persistent metadata transfer. Older SDK evidence files are earlier iterations, not additional independent coverage. API key metadata uses a local SQLite adapter storing hashes; application data stays in PostgreSQL. The control-store choice for multiple hosts remains open.

Last recorded lab state: owned containers stopped, volumes retained. Configured container ceilings: 3072 MiB and 3 logical CPUs for this component lab. The approximately 134 MiB idle snapshot is not a full-platform requirement or a capacity estimate. Recheck available RAM before startup. The proposed overall lab budget is 4 GB RAM, 4 logical CPUs and 20-30 GB disk; never disturb existing services.

Internal catalog now models organizations, owner/admin/viewer membership, projects and environments. Mutations check current membership inside SQLite transactions; the last owner cannot be removed. Metadata ownership transfer requires ownership of both organizations and preserves project/environment IDs. An initial HTTP handler now derives actor identity through Supabase SDK getUser against a fixed dedicated management endpoint. Ten live Auth/HTTP checks now pass using a_stage as a temporary management realm and a_prod as an application realm. A dedicated management deployment remains pending. Runtime secrets/access still need revocation for complete transfer. See [control-plane boundary](docs/CONTROL-PLANE.md).

Live management evidence: [10 checks](docs/evidence/management-checks.json) cover crossed application tokens, tampering, nonmembers, viewer writes, body/header identity spoofing and immediate membership revocation. The lab was stopped after verification.

Environment creation now atomically queues a persistent provisioning operation. The single-host lab worker provisions database/Auth/REST, checks health and records success. Seven live checks cover recovery after services started but completion was not recorded, preserving database identity and Auth data. [Evidence](docs/evidence/provision-checks.json), [worker scope](docs/PROVISIONING.md). This is not full Supabase provisioning or automatic installation startup.

The combined management handler now provides scoped connection discovery and publishable-key issuance/list/revocation. The managed gateway accepts only successfully provisioned runtimes and currently valid stored keys. Nine live checks connected the SDK to a provisioned environment with a management-issued key, denied application-token management access and proved immediate key revocation. [Evidence](docs/evidence/connection-checks.json). No secret/service key proxying or full platform routing is enabled.

## Research, reasons and saved pictures

- [Decision register](docs/DECISIONS.md): why this candidate, alternatives, remaining gates.
- [Architecture review](docs/ARCHITECTURE-REVIEW.md): overall reasoning and acceptance criteria.
- Independent reviews: [feasibility](docs/reviews/supabase-feasibility.md), [security/operations](docs/reviews/security-operations.md), [alternatives](docs/reviews/alternatives-product.md), [capacity methodology](docs/reviews/capacity-method.md). Each contains source links.
- Saved images: [10 projects](docs/diagrams/ten-projects.png), [transfer and restore](docs/diagrams/move-and-restore.png). [Assumptions/prompts](docs/diagrams/README.md).
- [Implementation record](docs/SESSION-RECORD.md) retains earlier findings and progression. This page takes precedence for current state.

Images illustrate proposed behavior. Their 10-project pilot cap and two-server layout are not measured limits or implemented features.

## Remaining work, in order

1. Verify full Supabase PostgreSQL bootstrap, canonical role privileges and upgrades using pinned versions; compare independent PostgreSQL if the candidate fails.
2. Implement authenticated management, organization/project/environment authorization, durable lifecycle operations and gateway hardening, including service keys, CORS, OAuth, rate limits and timeouts.
3. Integrate and test Storage, Realtime, pooler, functions and scheduled jobs; verify cross-environment boundaries for every service.
4. Prove complete encrypted off-host backup/restore, safe server transfer and organization transfer, failed-upgrade recovery and audit trails.
5. Benchmark representative peak workloads and noisy neighbors, first 3 then 10 environments. Determine admission limits from RAM, CPU, I/O, connections, disk and recovery headroom. Daily visitor totals alone cannot establish 10 or 100 project capacity.
6. Build the Supabase-inspired administration UI and installer against verified lifecycle APIs.

Organization transfer changes whole-project ownership and permissions. Server transfer moves one environment with a write pause, validation and routing cutover. After destination writes, rollback requires reconciliation. Recovery covers databases, objects, secrets/configuration and function artifacts. These are design requirements, not implemented workflows.

## Continue in Codex or Hermes

Use this same repository as the source of truth. Read this file, DECISIONS.md and lab/README.md; inspect git status and current host resources. Use bun and /usr/bin/python3. Keep secrets in ignored .secrets and local runtime state in .lab. Never publish either. Preserve unrelated containers and services. Local lab checks do not establish Contabo capacity.

The handoff archive contains source, research, diagrams and sanitized evidence only. It excludes secrets, runtime data, dependencies and Git history. Reinstall dependencies with bun install. Local image IDs in lab/images.lock.json are machine-specific, so a different host needs image availability and provenance verification before running the lab.

No Hermes execution was dispatched. Changing assistant does not change these technical decisions. Avoid two agents mutating the same checkout simultaneously.
