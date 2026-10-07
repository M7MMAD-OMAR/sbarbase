[العربية](console-design.ar.md)

# Console design and local Studio preview

Updated 2026-10-06. This record covers console navigation, compact controls and the disposable local Studio preview. It does not approve production deployment. See [deployment readiness](deployment-readiness.md).

## Compact Supabase patterns

The console adapts the upstream [Supabase UI size constants](https://github.com/supabase/supabase/blob/master/packages/ui/src/lib/constants.ts), [Button](https://github.com/supabase/supabase/blob/master/packages/ui/src/components/Button/Button.tsx) and [Badge](https://github.com/supabase/supabase/blob/master/packages/ui/src/components/shadcn/ui/badge.tsx) patterns. The upstream [UI package](https://github.com/supabase/supabase/blob/master/packages/ui/package.json) declares MIT licensing. Numeric scales and visual patterns are expressed in the existing local CSS; the complete upstream UI package is not installed or vendored.

Desktop controls use the upstream 26px tiny button and 34px small input scales, 14px button icons, 6px corners and subdued borders. Status pills use compact uppercase text, a border and semantic colors. Mobile controls remain at least 34px tall. Existing focus indicators, field errors, native select behavior and light/dark theme choices remain available.

Projects are cards. Team and organization settings have separate sidebar entries. Environment pages separate Overview, Database Studio, Authentication, Services, Usage and API keys. Inactive environment sections unmount to stop their reads and polling. Switching sections discards unsaved drafts in those child forms; network retries within the same section preserve values. If permissions remove the active section, navigation falls back to Overview.

## Studio workspace navigation

A compact workspace bar shows organization, project and environment above the original same-origin Studio in an iframe. The vendor image, database and API paths stay unchanged. The proxy allows HTML framing from the same host only and preserves the remaining CSP restrictions. The console Studio ticket opens this workspace automatically. Organization and project changes request one scoped metadata snapshot; environment changes use the loaded list. There is no idle navigation polling and no automatic service startup.

Only organizations where the actor is an owner or administrator appear. The metadata transaction rechecks access to the current Studio. Switching requires an exact same-origin POST, bounded input, fresh source and target authorization, and a running upstream. It issues a short-lived target-runtime ticket and navigates to that runtime's host, retaining separate host cookies and the trusted console origin. Unavailable environments stay disabled. Pending selection hides the current editor until the actor returns or explicitly opens the target.

The local preview at http://127.0.0.1:8790/__sbarbase/workspace contains one real database, Sbar Shop / Production. Other organization and project names come from the management fixture and cannot open a second real database. Cross-host ticket switching is verified separately against a browser fixture, not a second provisioned stack.

## Server snapshot and full monitoring

Installation operators can request GET /management/v1/server. Every request authenticates and rechecks operator access before the lazy five-second cache is read. Other users cannot collect these counters. Responses use Cache-Control: no-store.

The cards show OS-visible CPU cores, load averages, uptime, RAM, swap and the application's filesystem capacity. These counters can include host-wide values even when the application runs in a container. They do not measure cgroup limits, Docker history or database performance. Missing or inconsistent counters stay unavailable. Refresh is manual.

[Beszel](https://beszel.dev/guide/what-is-beszel) is a separate MIT-licensed monitor for host and Docker resource history and alerts. The console links to its setup documentation. It is not installed or authenticated by this change. Use an independent private administrator account; do not put Sbarbase service tokens in monitoring URLs. The hosted [Supabase Metrics API](https://supabase.com/docs/guides/observability/metrics) is not available for self-hosted installations.

## Local demonstration boundaries

The management console at http://127.0.0.1:8787 uses an explicitly labelled in-memory fixture account. It is not a working production login. Only Sbar Shop / Production opens the separate original vendor Studio at http://127.0.0.1:8790/__sbarbase/workspace.

That Studio has a real disposable PostgreSQL database, sbarbase_preview, with public.preview_tasks and two example rows. Table loading and SQL count execution were verified through the actual Studio UI. It uses pinned, locally present vendor Studio and postgres-meta images with a standalone disposable PostgreSQL initialization. This does not prove the application's full upstream provisioning, native configuration or migration path.

Auth, REST, Storage, Realtime, Functions and Logs are not running in this local demonstration. Advisor checks are unavailable without the full upstream bootstrap. Query snippets use a private temporary directory. No retained application data or other project's containers are attached.

The three owned containers use an internal network, read-only root filesystems, private temporary storage, no host directory or Docker socket mounts, bounded logs, no container swap, a combined memory limit of 1792 MiB and a combined CPU limit of one core. The owner controller removes only its own resources after two hours, or sooner if available host memory drops below 2 GiB. Tables and snippets disappear when the preview stops. The local-only URL requires no preview login and must not be exposed publicly.

## Verification scope

The console was checked with the existing Bun suite, UI typecheck, build, bilingual/link/reference checks and browser journeys. Browser checks include field validation, network retries, separated navigation, organization switching, mobile overflow and actual OS-visible counters. Native Studio table and SQL journeys use a separate browser run. Full installation acceptance and production readiness remain open.

Workspace verification passed 362 Bun tests, UI types and a targeted typecheck of application sources and the new navigation tests. The broader control typecheck still reports 16 existing test typing errors. Browser verification covers scoped labels, signed cross-host switching, unavailable targets, literal untrusted names, failed-read retry and mobile overflow. It does not approve full installation or production.
