[العربية](console-design.ar.md)

# Console design and local Studio preview

Updated 2026-10-18. This record covers the console's design system, navigation and the disposable local Studio preview. It does not approve production deployment. See [deployment readiness](deployment-readiness.md).

## Supabase design tokens and components

The console uses Supabase's own design language. Its colours are the semantic tokens of the [Supabase UI package](https://github.com/supabase/supabase/tree/master/packages/ui) (`build/css/source/semantic.css` with `themes/light.css` and `themes/dark.css`, commit `7425d493`), resolved to literal values in `ui/style.css`. Buttons, inputs, text areas, cards, alerts, badges, tables and the focus ring follow the recipes of the upstream [Button](https://github.com/supabase/supabase/blob/master/packages/ui/src/components/Button/Button.tsx), [Input](https://github.com/supabase/supabase/blob/master/packages/ui/src/components/shadcn/ui/input.tsx), [Card](https://github.com/supabase/supabase/blob/master/packages/ui/src/components/shadcn/ui/card.tsx), [Alert](https://github.com/supabase/supabase/blob/master/packages/ui/src/components/shadcn/ui/alert.tsx), [Badge](https://github.com/supabase/supabase/blob/master/packages/ui/src/components/shadcn/ui/badge.tsx) and [Table](https://github.com/supabase/supabase/blob/master/packages/ui/src/components/shadcn/ui/table.tsx) components, rewritten as plain CSS on the console's existing class names. No Supabase React component, Tailwind or Radix package is installed. The repository is Apache 2.0 and the UI package declares MIT; attribution is in `NOTICE`.

Text and surfaces keep Supabase's default contrast setting (0.5). Strokes use Supabase's own contrast setting raised to 0.75, and editable fields take the upstream hover stroke at rest, because at the default setting cards, fields and buttons blend into one grey. Two values differ from upstream for WCAG AA: the light theme's lightest text step is slightly darker, and the dark theme's destructive hover darkens instead of lightening so white text keeps 4.5:1.

What each element means:

- **Primary action**: the solid green button, one per task. Other buttons use the raised default surface; destructive confirmations are solid red.
- **Editable fields** are sunk, with a stroke of at least 3:1 against the card. **Read-only values** (tokens, keys, URLs, commands) are filled, monospace and have a quiet stroke, so they read as something to copy.
- **Cards** have a header strip; the first heading of a section becomes it. A one-time secret panel has a green edge and header.
- **Alerts** are warning (amber), destructive (red) and success (green), each with a coloured edge. Status badges use the same three colours.
- **Focus** is a 2px ring with a 2px offset in the brand green, at least 3:1 against every surface.

`tests/ui-contrast.test.ts` checks every text and boundary pair above in both themes. Desktop controls are 32px buttons and 34px fields; phone controls stay at least 36px tall. Existing focus order, field errors, native select behaviour and the light, dark and system theme choice are unchanged.

Projects are cards. Team and organization settings have separate sidebar entries. Environment pages separate Overview, Database connection, Sign-in settings, Services, Usage and API keys. Inactive environment sections unmount to stop their reads and polling. Switching sections discards unsaved drafts in those child forms; network retries within the same section preserve values. If permissions remove the active section, navigation falls back to Overview.

## Studio workspace navigation

Updated 2026-10-10: tickets now enter `/project/default` directly. Native Studio is a full page; its HTML, navigation and security headers are preserved. The optional `/__sbarbase/workspace` is an environment selector only, with no embedded editor. The console handles organization/project selection and automatically opens Studio after a newly created production becomes ready. Existing project access remains one click. See [the journey audit](../design/USER-JOURNEY.md).

Environment switching still requires exact same-origin POST, bounded input, current source and target ownership and a running target. It issues a fresh target-only ticket. These authorization checks also cover the optional selector. The preview described below is historical evidence from 2026-10-06, not a capture of the revised default entry.

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
