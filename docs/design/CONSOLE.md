[العربية](CONSOLE.ar.md)

# Console visual specification

Reference: `console-concept.png`, generated with the built-in image tool on 2026-09-20.
Scope: the platform-layer surface only. Design-system parity with Studio is withdrawn: each
environment is administered by the original upstream Studio, which is not ours to specify
([integration specification](../engineering/STUDIO-INTEGRATION.md)).

Primary surface: sidebar, organization selector, project table and creation form.
Required extensions in the same system: password login, empty/error/loading states,
project environment list, provisioning status, connection details, key issuance
and revocation. No invented metrics, resource forecasts or unavailable navigation.

Tokens: these are the platform layer's own tokens, not Studio's. Each has a light and a dark
value: light is the default, the dark set applies through the operating system or browser
preference, and an explicit choice overrides both. Background #171717, sidebar #1c1c1c, surface #202020, border #303030,
text #ededed, secondary #a0a0a0, accent #3ecf8e with dark text. Neutral flat fills;
any generated raster lighting/grain is not a production asset. No raster UI assets.
System sans typography, 14px controls/body, 34px heading, 12px metadata, monospace IDs.
Sidebar 268px at desktop, content padding 40px, header 64px, controls 40px,
rows 92px. Reference native dimensions: 1586 x 992. Use Lucide outline icons,
18px, 1.75px strokes; no pictorial brand mark, text wordmark only.

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
