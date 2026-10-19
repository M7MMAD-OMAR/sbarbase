[العربية](CONSOLE.ar.md)

# Console visual specification

## Current project and dashboard flow

Creating a project selects it immediately and queues its production environment in the same server transaction. The Create action reserves a new tab; after production is ready, Studio starts and opens automatically there. The console remains open and Stay in console cancels the handoff. Existing projects retain the named dashboard action. Project settings are collapsed until requested. Native Studio opens as a full page with its own navigation and vendor security headers. Connection details also expose the same action; Database connection and Sign-in settings distinguish local configuration from native Studio pages. See [the complete journey audit](USER-JOURNEY.md). Viewer roles keep connection discovery without Studio controls. Account security follows the installation MFA policy and offers the owner a fresh-code policy change. See [the implementation plan](../engineering/plans/2026-10-10-management-project-workflow.md) and [Studio guide](../guides/studio.md).


Reference: `console-concept.png`, generated with the built-in image tool on 2026-09-20.
Scope: the platform-layer surface only. Each environment is administered by the original
upstream Studio, which is not ours to specify
([integration specification](../engineering/STUDIO-INTEGRATION.md)). Since 2026-10-18 the
console itself uses Supabase's design tokens and component recipes so it reads like Studio.

Primary surface: sidebar, organization selector, project table and creation form.
Required extensions in the same system: password login, empty/error/loading states,
project environment list, provisioning status, connection details, key issuance
and revocation. No invented metrics, resource forecasts or unavailable navigation.

Tokens and sizes: Supabase's semantic colour tokens and component recipes, with a light and a
dark value for each: light is the default, the dark set applies through the operating system or
browser preference, and an explicit choice overrides both. The values, sources and checked
contrast pairs are in [console design](../reference/console-design.md); `ui/style.css` is the
source of truth. No raster UI assets. Use Lucide outline icons, 16px, 1.75px strokes.

React component ownership: App session/layout, Login, Projects, Environments,
Connection, reusable creation form/status/error. Vite builds assets, Bun serves
those assets and the same-origin API. Native forms/selects/buttons; inline
creation forms avoid dialog focus traps. No secret or Auth session is persisted
in browser storage. A raw project key is shown once and cleared on navigation.

The generated mockup supplies sample names/short IDs. Real API data determines
rows, names, IDs, counts, permissions and statuses. Intentional differences:
responsive stacking under 720px; operational notices only for actual pending or
failed jobs; full IDs available in connection details; inline creation/key forms.
The reference text inventory is wordmark, Organization, Projects, Local installation,
Sign out, Your projects, organized in one place., New project, Search projects,
Project, Project ID and the actual project count. Additional view copy is functional.

Prompt summary: complete 1440 x 900 Supabase-style dark Sbarbase console, sidebar,
organization select, Projects heading and search, two-row project table, green
New project button, no metrics/charts/hero/illustration. Tool returned 1586 x 992.

## Studio handoff

The platform layer does not administer an environment. It links to that
environment's Studio, the original upstream application, served on its own
authenticated route ([integration specification](../engineering/STUDIO-INTEGRATION.md),
sections 4 and 5). Anything inside an environment, from tables and SQL to Auth
users and settings, belongs to Studio and to upstream's own design, which this
document does not specify.

The connection surface already lists the services an environment exposes; the
Studio entry joins that list, so the person reaches it the same way they reach the
API, rather than through a second navigation of our own.
