# Sbarbase: current state and handoff

Updated 2026-09-20. Read this first in Codex or Hermes. Workspace: `/home/sbarah/R/Projects/P/sbarbase`.

## Purpose and firm requirements

Downloadable open source software for running multiple Supabase projects on an operator's own server, commonly one VPS, with possible expansion later. Keep Supabase and compatibility with existing applications. Provide UI-based administration, low incremental resource cost, project isolation, backup/restore and eventual transfers. Follow Supabase's design system for the eventual UI; current images are illustrations, not implemented UI.

The operator is trusted; application visitors are not. Public hosting for mutually hostile project owners is not the default product. Avoid a complete repeated stack per project; a small number of per-environment services is not ruled out. Do not replace Supabase or rewrite authentication merely to reduce process count.

## Latest direction, not a final architecture approval

Proposed hierarchy: installation -> organization -> project -> environment. Production and optional staging have separate data and credentials. Deployment placement is separate from ownership, so a stable project can move between hosts or organizations.

Primary experiment: shared PostgreSQL, database per environment, unique service logins, original Auth and PostgREST per environment, other services shared where verified. Reason: potential resource savings without replacing Supabase semantics. Savings and safety are unproven.

Comparison/fallback: independent PostgreSQL instances per environment with shared administration. Reason: canonical roles remain independent and SQL compatibility is simpler. Costs must be measured. Full repeated Supabase stacks are a reference baseline, not the preferred product. Kubernetes and multi-host HA are not initial requirements.

Critical open choice: shared canonical NOLOGIN API roles versus namespaced API roles. PostgreSQL roles are cluster-global; renaming `authenticated` can break policies and imports. Shared roles require strict login/database rules and membership review. Neither strategy is approved yet.

## Research completed

Three independent agent reviews covered component feasibility, adversarial security/operations, and existing implementations. Primary documentation and selected code were inspected; no runtime certification occurred.

- [Foundation review](docs/ARCHITECTURE-REVIEW.md): architecture options and acceptance gates.
- [Supabase feasibility](docs/reviews/supabase-feasibility.md): Auth/PostgREST limits, role compatibility, Storage/Realtime sharing and a pinned Auth migration scan.
- [Security and operations](docs/reviews/security-operations.md): isolation, privilege boundaries, recovery, upgrades and HA limitations.
- [Alternatives](docs/reviews/alternatives-product.md): supabase-multitenant is an adaptation candidate, not endorsed; Supafleet CLI shares database service credentials; Pigsty helps operations; Coolify/Dokploy alone do not establish resource sharing.
- [Capacity method](docs/reviews/capacity-method.md): reproducible load comparison, not benchmark results.

Corrections: no evidence Supabase lacks a sound hierarchy; its self-hosted distribution lacks cloud project management. Separate databases do not isolate CPU, memory or host failure. Container count and daily visits do not determine capacity. A replica is not a backup; native physical PITR restores the cluster, not one database directly.

## Saved diagrams and lifecycle intent

- [10-project diagram](docs/diagrams/ten-projects.png)
- [Transfer and restore diagram](docs/diagrams/move-and-restore.png)
- [Diagram assumptions and generation prompts](docs/diagrams/README.md)

The six/four server distribution is illustrative. The depicted 10-project cap is a proposed pilot policy, not a user-approved permanent limit or measured capacity. Count environments and active workloads too. No promise of 100 projects on one host.

Organization transfer changes whole-project ownership and permissions. Server transfer moves an environment after copy, brief write pause, final synchronization, validation and routing switch. Rollback after destination writes requires reconciliation. Cross-installation export/import is a separate future workflow. Backups include database, objects, secrets/configuration and function artifacts; restore into an isolated target with outbound jobs disabled before switching.

## Local experiment in progress

Last read-only snapshot: about 32 GB total RAM, 9.6 GiB available, 24 logical CPUs and 496 GB free disk. Docker 29.7.2 was available with an unrelated Supabase stack running. This is a historical snapshot; recheck before allocating anything.

Proposed initial aggregate lab budget: 4 GB RAM, 4 logical CPUs, 20-30 GB disk. Start with A production, A staging and B production, then 10 environments if the workstation remains comfortable. This budget is a candidate, not proof that every comparison fits. Isolate networks, ports, volumes and names; preserve existing services. Monitor host memory and disk pressure and stop load generation if it affects desktop work. Do not run simultaneous large comparisons or assume 100 environments fit.

Next implementation gate: pin versions; test real Supabase SDK/SQL behavior; deny cross-environment tokens AND database credentials; test lifecycle retries, restore and upgrade failure; measure idle/active footprint and noisy-neighbor effects. Local tests need no rented infrastructure or paid APIs, but cannot establish Contabo performance.

## Execution state and continuation

The user authorized continuing implementation in Codex. Git was initialized on main. `lab/run.py` now creates an owned network, volume, three environment databases and per-environment Auth/REST service definitions using local pinned image IDs. This is a component experiment on stock PostgreSQL 17, not yet the Supabase PostgreSQL distribution or the full product.

The component lab now passed 61 live HTTP/database checks and 21 Supabase SDK checks. Evidence: `docs/evidence/component-checks.json`, `sdk-checks.json`, and `idle-snapshot.json`. Auth migration startup required a per-login, per-database auth search_path, consistent with upstream bootstrap. Internal bridge IPs work from the Linux host without publishing container ports; the SDK test uses a transient loopback router.

Verified scope: three environments, independent signup/login/refresh identities, owner-only RLS reads/writes, swapped Auth/REST token rejection, and service database credential rejection across environment and administrative databases. SDK CRUD and logout passed. Limitations: stock PostgreSQL plus a minimal auth.uid fixture, test-only router without API-key enforcement, no Storage/Realtime/functions or full upstream database bootstrap. This is not production compatibility certification.

Provisioning now recovers from injected interruptions after roles, database creation and permissions. Repeating creation preserved the same database OID and canary rows. A logical dump restored into a fresh temporary database reproduced Auth user identities and application rows while source and neighbor stayed unchanged. This does not test off-host backups, PITR, complete Auth state comparison, Storage, or route switching. Evidence: `docs/evidence/retry-checks.json` and `restore-check.json`. A process lock prevents overlapping CLI lifecycle commands.

An initial gateway module now binds API keys to enabled environments before proxying Auth/REST and removes client-injected routing headers. Eleven unit tests and 27 live SDK/gateway checks passed, including six crossed-key rejections. User JWT validation remains upstream. Evidence: `docs/evidence/gateway-sdk-checks.json`. Keys are generated ephemerally by the lab probe; durable key management, service keys, CORS, OAuth callbacks, rate limits, Storage and WebSockets remain pending. The earlier test-only router is replaced in the SDK probe.

All owned lab containers are stopped after the checks, with volumes preserved. Snapshot container memory totaled about 134 MiB after a tiny workload; this excludes full platform services and is not a capacity forecast. Aggregate configured container ceilings remain 2560 MiB and 2.5 logical CPUs. Recheck available memory before each startup. Next: strengthen repeatable provisioning and recovery, implement a real keyed gateway, then extend the matrix to upstream database bootstrap and remaining services.

The original broader requirements remain active: UI administration, compatibility, lifecycle, transfer, backup/recovery, upgrades and measured capacity. No server purchased or remotely modified. No final topology, capacity, license or upstream adoption selected. No transfer to Hermes was dispatched.
