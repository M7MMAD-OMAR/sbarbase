# Sbarbase handoff

Snapshot: 2026-09-20. Read [PROJECT.md](../PROJECT.md) for detailed status and evidence. This repository, not a chat transcript, is the continuation source.

## Decision in one minute

- Build an open source, self-hosted Supabase-based platform, initially on one server.
- Hierarchy: installation > organization > project > environment. Organization owns the project; placement selects where an environment runs.
- Candidate runtime: shared PostgreSQL, separate database and service credentials per environment, original Auth/REST per environment, shared Storage with tenant isolation.
- Keep Supabase compatibility. Shared Auth/REST database switching would require unsupported behavior or substantial custom security-sensitive code.
- Separate PostgreSQL instances remain the fallback if shared isolation, upgrades, recovery or measured savings fail. This is a tested local direction, not an approved production architecture.
- Follow Supabase's UI direction; current console is functional locally, not a complete implementation of its design system.

## Why this, and why not alternatives?

[Decision register](DECISIONS.md) records alternatives and rejection criteria. Schema-only separation weakens the independent lifecycle we want. A full stack per environment is the resource-cost baseline, not a discarded option. Replacing Supabase conflicts with the required compatibility. Deployment managers alone do not implement the intended tenant hierarchy.

Research and adversarial reviews: [feasibility](reviews/supabase-feasibility.md), [security](reviews/security-operations.md), [alternatives](reviews/alternatives-product.md), [capacity](reviews/capacity-method.md), [upstream bootstrap](reviews/distribution-bootstrap.md), [shared Storage](reviews/shared-storage.md), [recovery](reviews/storage-recovery.md), [durable runtime](reviews/durable-runtime.md), [management](reviews/upstream-management.md). These files contain source links and limitations; reviews are not security certification.

## What is actually present?

Local console, management login, organization/project discovery, project/environment creation, queued provisioning, scoped API keys, real Supabase Auth/REST/Storage integration and retained volumes. Versioned checks are in [evidence](evidence/); browser results are in [console QA](design/CONSOLE-QA.md). Recovery evidence covers a quiescent same-cluster fixture, not production off-host disaster recovery.

**Local supervisor:** `lab/dev.py` runs the console and watching worker. Five lifecycle tests and [eight live smoke checks](evidence/supervisor-smoke-checks.json) pass, including lock exclusion, idle worker restart and graceful shutdown. Review fixes retain the worker lock across restarts and bound startup cancellation. An additional [eight-check recovery probe](evidence/supervisor-recovery-checks.json) interrupts a running provisioner after private state persistence and verifies completion with stable identity. Other crash points and production service management remain unverified.

## Saved visuals

- [Ten-project hierarchy](diagrams/ten-projects.png).
- [Ownership, migration and recovery](diagrams/move-and-restore.png).
- [Diagram assumptions and original prompts](diagrams/README.md).
- [Console concept](design/console-concept.png), [desktop](design/console-desktop.jpg), [mobile](design/console-mobile.jpg).

The diagrams illustrate future operations. Ten projects is not a measured capacity limit; the second server is optional future placement, not a current requirement.

## Still unresolved

Production admission and noisy-neighbor controls; capacity at 10 or 100 projects; upgrades; complete off-host backup and restore; server cutover and full ownership transfer; Realtime, pooler, functions and cron; production onboarding, audit and installation. Daily visitor counts alone cannot size these workloads.

Local budget proposal: 4 GB RAM, 4 CPUs and 20-30 GB disk. The four retained upstream environments have container limits totaling 3840 MiB and 3.75 CPUs, reaching the experimental admission guard. These are configured ceilings, not measured workload capacity or total host consumption. Recheck available host resources before starting anything.

## Latest gateway checkpoint

Committed baseline: `548d898`, the SDK workload checkpoint. [Gateway concurrency and streaming](GATEWAY-OVERLOAD.md) now have 22 HTTP checks, 8 actual Supabase overload checks and a 1,000-operation SDK regression. Read [the exact resume checkpoint](RESUME-CHECKPOINT.md) for remaining limits and next steps.

## Continue in Codex or Hermes

Use the same repository, `/home/sbarah/R/Projects/P/sbarbase`; no migration of code is necessary. Read `~/AGENTS.md`, this file, `RESUME-CHECKPOINT.md`, `PROJECT.md`, then `lab/README.md`. Inspect Git changes and live processes first. Use one assistant as active writer. No Hermes execution has been dispatched.

Keep `.secrets/` and `.lab/` private. The handoff ZIP contains source, research, saved pictures and sanitized evidence, including explicitly unfinished source. It excludes credentials, runtime data, dependencies and Git history. It is a development handoff, not a runnable backup of the installation.
