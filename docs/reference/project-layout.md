[العربية](project-layout.ar.md)

# Project layout

This map describes the maintained source tree. [Status](status.md) identifies public and installed versions; [deployment readiness](deployment-readiness.md) defines the remaining operational gates.

| Path | Responsibility |
|---|---|
| `src/control/` | Organizations, projects, environments, permissions, keys, management APIs, Supabase-compatible API and MCP |
| `src/gateway/`, `src/http/` | Application routing, admission, request accounting, traffic holds and transport |
| `ui/` | Sbarbase React console, shared controls, theme tokens and environment workspaces |
| `src/control/studio.ts` | Authenticated native Studio proxy, sessions and cross-environment switching |
| `src/control/studio-workspace.ts`, `src/control/studio-identity.ts` | Optional workspace selector and safe browser title/URL identity |
| `lab/` | Operator tools, runtime coordination, backup/restore, guarded upgrades and verification fixtures |
| `tests/` | TypeScript tests; Python tests live beside the corresponding lab tools |
| `deploy/` | Container packaging, host admission, supervisor/TLS tooling and isolated verification entry points |
| `website/` | The bilingual public site, generated docs, Markdown copies, changelog feeds and agent discovery files |
| `docs/explain/`, `docs/guides/`, `docs/reference/` | Reader-facing explanations, procedures and current reference |
| `docs/decisions/` | Design choices and reconsideration criteria |
| `docs/engineering/`, `docs/evidence/` | Dated engineering contracts and source-bound observations, including incomplete work |
| `fragments/`, `.planning/` | Development fragments and planning history, not operator install prerequisites |

## Current versus experimental paths

The existing shared topology and the experimental native/dedicated placement work have different acceptance requirements. A helper or test file in the tree does not make an experimental workflow production-ready. Read the relevant contract before operating on retained resources. Historical counts and temporary preview addresses have been removed from this source map; the dated records remain in the engineering notebook and evidence.

The [project goal](../../PROJECT_GOAL.md), [product plan](../engineering/plans/2026-10-03-product-and-portability-plan.md) and [evidence ledger](../engineering/gauntlet-ledger.json) describe broader work. They are not substitutes for current source and deployment checks.

## Local and generated material

`.lab/` and `.secrets/` are private installation state. Keep them, credentials and backup payloads out of public source. Dependency directories, built UI assets and `website/dist/` are generated.

`graft/` is an ignored local source index. Run `graft build` in a fresh checkout when needed; the index is not a product dependency. Follow repository agent instructions when locating or changing code.

## Verification entry points

Use the frozen Bun lock, UI/control typechecks, `bun run build:ui` and the relevant Bun/Python tests for a change. Website documentation changes are checked with `bun run build` and `bun test` inside `website/`. Browser workflows and native container checks have additional isolated-environment requirements; their results retain their own source and scope.

<a id="historical-verification-records"></a>
<a id="additional-integration-sources"></a>
<a id="native-startup-preparation-sources"></a>
<a id="private-sql-completion-sources"></a>
<a id="current-authority-and-verification"></a>
