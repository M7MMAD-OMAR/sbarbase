# Scoped Agent Administration Implementation Plan

> **For agentic workers:** Implement this plan task-by-task using available execution tools. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give agents explicit scoped administration through API and MCP, with an owner-controlled permission UI, while diagnosing Studio latency and removing the requested i2s project.

**Architecture:** Personal access tokens carry independently checked capabilities and one resource scope. A fixed operation registry reuses existing management handlers and their current membership checks. MCP uses the same API rather than a separate authority path. Dangerous execution rights remain explicit opt-ins.

**Tech Stack:** Bun, TypeScript, SQLite, React, official Supabase MCP server and MCP SDK.

**Spec:** The owner's request in this conversation, recorded as requirements R1-R6 in the checkpoint plan at `/home/sbarah/.local/share/sbarbase-checkpoints/2026-11-14-agent-admin/plan.json`.

## Global Constraints

- Preserve existing work; use Bun for installation, execution and tests.
- Arabic interface copy; English repository documentation and comments.
- Tokens never mint other tokens, impersonate management sessions or bypass current organization membership.
- The scope is all currently accessible resources, one organization or one project. Capabilities further narrow that scope.
- New tokens default to read access. Existing tokens migrate to read access and need explicit reissue for writes.
- Safe administration excludes SQL writes, arbitrary function deployment and destructive grants. Granting code execution or SQL permits destructive effects and must be described honestly.
- Project/environment lifecycle deletion and purge remain unavailable to agents.
- No production deployment or shared service restart is implied by local feature implementation.
- The explicit owner request authorizes removing the exact live project i2s through normal lifecycle safeguards, independently from source delivery.
- No secrets, credential bodies or SQL contents in plans or operation audit records.

## Task 1: Granular token authority and administrative API

**Files:** `src/control/access-tokens.ts`, `src/control/access-token-http.ts`, `src/control/agent-permissions.ts`, `src/control/agent-admin.ts`, `src/control/supabase-api.ts`, `src/control/application.ts`, `tests/agent-permissions.test.ts`, existing token/API/schema tests.

**Interfaces:** `AGENT_PERMISSIONS`, `READ_PERMISSIONS`, `SAFE_ADMIN_PERMISSIONS`, `DANGEROUS_PERMISSIONS`, `AgentPermission`, `hasAgentPermission`; token `permissions` field. `GET /v1/agent/operations`; `POST /v1/agent/execute` with `{operation, project_ref?, organization_id?, input?, dry_run?}`.

- [x] Add failing regression cases for omitted and unknown grants, cross-project/org access, read-only SQL and implicit GET mutation.
- [x] Migrate persisted tokens safely without storing plaintext bearer secrets.
- [x] Enforce capabilities on every supported compatibility endpoint, including SQL, migrations, functions, keys and settings.
- [x] Add fixed operation discovery and execution, filtering by grants and validating exact targets. Dry run must not call mutation handlers.
- [x] Wire native management dependencies for agent invocation, preserving revocation and publication checks.
- [x] Verify `bun test tests/agent-permissions.test.ts tests/agent-api.test.ts tests/store-version.test.ts tests/store-schema-fingerprint.test.ts`.

## Task 2: MCP administration and owner permission UI

**Files:** `src/control/mcp.ts`, new `src/control/agent-mcp.ts`, `ui/AccessTokens.tsx`, `ui/style.css`, `tests/agent-mcp.test.ts`.

**Interfaces:** MCP `get_sbarbase_operations` discovers permitted operations; `execute_sbarbase_operation` executes the API contract. React consumes the same capability arrays as the backend.

- [x] Preserve official Supabase tools and both supported protocol generations while adding the two administration tools.
- [x] Offer read, safe administration, custom and all-permissions presets with independent organization/project scope.
- [x] Require explicit acknowledgement for dangerous grants and explain arbitrary SQL/code risk. Avoid claiming that all permissions prevent data deletion.
- [x] Provide one-time token display, MCP address, copyable configuration, expiry and immediate revocation.
- [x] Verify actual browser interactions with an isolated owner fixture and inspect matched screenshots at 1280 by 1000 plus a narrow viewport.
- [x] Run `bun test tests/agent-mcp.test.ts`, `bun run typecheck:ui`, `bun run typecheck:control`, `bun run build:ui`.

## Task 3: Measured Studio repair

**Files:** Confirmed Studio proxy/adapter implementation and corresponding regression tests, chosen from runtime evidence.

- [x] Compare authenticated document, asset and API timings through the public proxy and direct upstream. Record resource pressure and running revision.
- [x] Reproduce the slow boundary locally before changing it. Add a meaningful regression.
- [x] Fix the measured cause without weakening session, origin or per-request authorization checks.
- [x] Re-run relevant Studio suites and report fixture results separately from live production behavior.

## Task 4: Exact i2s removal

- [x] Resolve the exact live project and all its environments from the live catalog.
- [x] Inspect deletion/lifecycle tools and preserve their backup, retention and native authorization safeguards.
- [ ] Remove only the owner-requested project. Never treat test schemas named i2s as the live project.
- [ ] Read back lifecycle state and record any retained backup or pending cleanup accurately.

## Task 5: Documentation and independent verification

**Files:** `docs/guides/agents.md`, `docs/guides/agents.ar.md`, relevant Studio diagnostics documentation and this progress checklist.

- [x] Document capability names, scope, token migration, connection steps, operation discovery, preview limitations and destructive boundaries.
- [x] Run focused tests plus both typechecks and UI build through the checkpoint engine against final source.
- [x] Obtain two independent reviews for requirements and security, and an independent Arabic/UI review. Fix concrete findings and refresh evidence.
- [x] Accept only with fresh source-bound evidence. Distinguish local acceptance, live deletion and production rollout.


## Verified delivery status

The local source checkpoint is accepted at source digest `010463f6a545b6326805323846d4bcd14947e88a751fb15a1736b734795c63b4`. Its final behavior evidence covers 60 agent/API/MCP/store tests and 97 Studio tests, both TypeScript checks and the UI build. Two independent language-model code reviewers passed the final snapshot. Another independent language-model reviewer inspected Arabic copy, matched desktop screenshots, the mobile capture and the coordinator's isolated browser interaction evidence.

The coordinator's final browser workflow verifies safe defaults, dangerous-grant acknowledgement and resets, saved grants/scope, one issuance request, client configuration, post-commit focus, keyboard navigation, mobile layout and table scrolling, revocation and one-time token hiding. This is an isolated UI fixture, not production integration evidence.

An intermediate broad run executed 1680 tests: 1679 passed and one overloaded-host proxy timing assertion failed; that assertion passed independently. It is not claimed as a green full-suite run against the final source.

Studio repair is limited to private caching of six verified versioned SMTP assets. Persistent severe slowdown was not reproduced; its overall cause remains unresolved.

The exact live i2s project was resolved. Its functions and direct database access are on. The owner's forgotten management password was recovered through the documented host recovery path; the native MFA factor remains intact. The separate live deletion action is in progress and requires a current MFA-verified management session. Native lifecycle inventory, service-stop, retention and shared-storage safeguards remain enforced. No live project deletion or production source deployment is claimed by this checkpoint.
