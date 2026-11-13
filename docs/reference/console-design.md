[العربية](console-design.ar.md)

# Console design

Updated 2026-11-13. This is the current console contract. The previous disposable preview URLs and expired demonstration instructions have been removed. See [status](status.md) for the deployed revision and [readiness](deployment-readiness.md) for remaining qualification.

## Surfaces and controls

The Sbarbase console follows Supabase's visual language using semantic light/dark tokens in `ui/style.css` and the shared controls in `ui/components.tsx`. This is plain React and CSS, not an installed copy of the Supabase React component library. The original Supabase Studio runs separately as a full page. Upstream attribution remains in `NOTICE`.

Fields, buttons, cards, tables, alerts and tabs use filled elevation surfaces without decorative borders. Selected items have a distinct fill. Keyboard focus retains a visible outline, including inside open menus. Destructive actions retain their labels and confirmation behavior.

All native console selects share theme-aware picker styles. Supporting browsers use `appearance: base-select`, a top-layer popup, a selected checkmark, bounded scrolling and wrapping for long labels. Unsupported browsers retain their native controls and themed option colors. Forced-colors mode uses contrasting system-color surfaces. The standalone Studio workspace picker follows the same approach; native vendor Studio components retain their own styling.

## Navigation

The desktop sidebar has viewport height; only its navigation links scroll. Workspace selectors and footer actions remain visible. The mobile layout keeps selectors separate and footer actions available.

Organization, project and environment have direct selectors. Requests are aborted when scope changes, and late responses are matched to their original scope. Selecting a project clears the previous environment. The connection workspace loads inside the main content boundary so the sidebar stays mounted; failed loads offer recovery.

An undeployed Edge Function draft requires confirmation before project, environment or shell navigation discards it. Other forms keep their own save behavior; this is not a global promise that every unsaved form persists between sections.

## Browser and Studio identity

Console tabs show the current project and selected environment. Native Studio tabs prefix its page title with the project and environment, and add a readable `workspace` query value. Names are text, control/bidirectional formatting characters are removed, and the URL label is normalized and safely encoded. Labels cannot grant access or select another database.

The runtime hostname and signed session remain authoritative. The display query is stripped before proxying native project requests. Studio switching still requires current source and target authorization, same-origin POST, bounded input and a running destination. The title enhancement follows native navigation without replacing the Studio application. Restrictive vendor CSP can suppress the enhancement; its policy is not weakened.

## Server snapshot

`GET /management/v1/server` is restricted to installation operators and has a lazy five-second cache with `Cache-Control: no-store`. It reports OS-visible CPU, memory, swap, uptime and filesystem counters, not workload capacity or a hosted Supabase metrics service. Missing counters remain unavailable. External monitoring has its own authentication and operational requirements.

## Verification

Run `bun run typecheck:ui`, `bun run typecheck:control` and `bun run build:ui`. The isolated project browser harness is `lab/project-workflow-browser.ts`; it needs its local Playwright setup and a usable Chromium executable (`SBAR_BROWSER_EXECUTABLE`). It covers direct switching, drafts, recovery, themes, sidebar scrolling and mobile layout. The [deployment observation](../evidence/server-console-2026-11-13.json) separately records the actual owner Studio title and URL checks. Neither test counts nor a screenshot alone certify all production workflows.

<a id="console-design-and-local-studio-preview"></a>
<a id="supabase-design-tokens-and-components"></a>
<a id="studio-workspace-navigation"></a>
<a id="server-snapshot-and-full-monitoring"></a>
<a id="local-demonstration-boundaries"></a>
<a id="verification-scope"></a>
<a id="november-2026-verification"></a>
