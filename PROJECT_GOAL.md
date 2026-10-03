# Project goal and durable constraints

Owner direction, recorded 2026-10-03. This is the durable brief for implementation, research, testing and review. Read it together with [the product plan](docs/engineering/plans/2026-10-03-product-and-portability-plan.md) and [the execution method](docs/engineering/plans/2026-10-03-gauntlet-execution-method.md).

## Intended outcome

An open-source platform that operates original Supabase services for multiple projects efficiently, supports their available capabilities and configuration, approaches the managed developer workflow, and helps people who do not understand server administration. Recovery, secure management, upgrades, domains, resource allocation and growth must be understandable and testable.

## Non-negotiable constraints

1. Original Supabase remains the foundation. Do not replace its database, Auth, Storage, Realtime or Functions contracts with a different backend.
2. The primary runtime is Linux containers through Docker and Compose. Use one container implementation across Linux hosts. Windows use, if validated, runs these same Linux containers through Docker; a native Windows implementation is not part of the plan.
3. No runtime, release, build, test or documented user workflow may require the owner's workstation, other private projects, personal home paths, untracked scripts, private local package registries or machine-specific services. Public dependencies and user-supplied deployment configuration are allowed and must be documented.
4. Host paths, Docker endpoints, ports, filesystem/resource capabilities and public URLs are installation inputs. Detect supported capabilities and refuse unsupported combinations before mutation. Do not infer support from the owner's successful setup.
5. Publish the source, configuration schema, dependency/license notices, reproducible verification commands and limitations. No optional hosted controller may become necessary for the self-host core to work.
6. Preserve complete application recovery and environment isolation while improving efficiency. Shared resources must have explicit failure boundaries and workload-bound capacity evidence.
7. Compatibility and cloud equivalence are separate claims. Record unavailable features and test implemented alternatives individually.
8. Performance and reliability need measured workloads, failure drills and independent review. No absolute promise of zero defects, universal hosts or unlimited projects.

## Execution and completion

Use a named, public, versioned reference before building. The initial application baseline is official Supabase `self-hosted/v0.8.2`; freeze its resolved commit and image set in every actual comparison. Multi-project operations use a written specification where upstream has no equivalent.

Give each slice a builder and a separate critic with fresh context. Compare actual artifacts or execution evidence without identifying which candidate is Sbarbase. Require a binary choice and address the largest remaining gap. Passing a document or unit slice does not establish application, recovery, performance or production readiness.

The requested methodology must include executable checks and a durable work/evidence ledger. The broader product roadmap remains open until each required capability is implemented and accepted at its own scope. Current failures, required skipped cases, missing evidence and stale evidence stay visible. Never redefine the full objective around the parts already passing.
