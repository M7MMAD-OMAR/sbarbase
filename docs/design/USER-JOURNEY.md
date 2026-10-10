[العربية](USER-JOURNEY.ar.md)

# User journey research and implementation

Updated 2026-10-10. This reviews the current management console and its handoff to original Supabase Studio, including the previous optional MFA and production delivery. It is a source and interaction audit, not a study with recruited users or live provider acceptance.

The goal is a short path from a project name to a usable native dashboard. Supabase's original interface stays responsible for tables, SQL, Auth users and Storage. The console remains responsible for installation and environment configuration.

## Research and decisions

| Source | Finding | Decision in this repository |
| --- | --- | --- |
| [Supabase self-hosting](https://supabase.com/docs/guides/self-hosting) and [Docker setup](https://supabase.com/docs/guides/self-hosting/docker) | Self-hosted Studio administers a single project; hosted multi-project features are not equivalent. | Keep a native Studio per environment. Select organization/project in the console, then enter `/project/default` directly. |
| [Supabase database overview](https://supabase.com/docs/guides/database/overview) | Native Table Editor and SQL Editor already provide database administration. | Preserve original Studio pages, assets, navigation and response body. No duplicate editor or added iframe. |
| [Progressive disclosure](https://www.nngroup.com/articles/progressive-disclosure/) | Secondary options can be exposed when needed. | Collapse project rename, move and delete under Project settings. Keep existing configuration features reachable. |
| [User control and freedom](https://www.nngroup.com/articles/user-control-and-freedom/) | Users need a way to stop or leave an action. | Explain automatic opening before Create; provide Stay in console; treat closing the waiting tab as cancellation. Preserve the console tab and session. |
| [W3C status messages](https://www.w3.org/WAI/WCAG21/Understanding/status-messages) | Progress and result messages should be available to assistive technology without requiring focus. | Keep provisioning and opening messages under `role="status"`, named controls and native keyboard-accessible disclosure. |

These are design inferences from the sources, checked against local implementation. They do not establish user satisfaction or feature parity with hosted Supabase. `ui-skills-root` selected `pbakaus/distill`: simplify the path and disclose advanced controls, while retaining necessary functionality.

## Complete journey inventory

| Journey | Default path | Deliberate user action or permission boundary |
| --- | --- | --- |
| Sign in and MFA | Server policy decides password access or verification/setup. Optional unenrolled accounts use durable password sessions. | Installation owner changes policy with fresh native MFA. Existing verified factors still require verification. |
| Organization and team | Assigned organization is selected; existing projects load. | Organization creation is installation-operator-only. Invitations and roles remain explicit. Unassigned accounts contact their owner. |
| New project | Enter its name and Create. Server atomically creates production and its provisioning job. UI selects the project and waits automatically. | No extra environment creation or Studio start click. Creation refusal closes the waiting tab and leaves no partial project. |
| Provisioning | Worker prepares production; page polls status. Its original environment id drives handoff, even if its name changes. | Stay in console cancels only the handoff. Failed provisioning exposes Retry for the existing environment. |
| Dashboard | Create reserves a new tab during the user gesture. After readiness, Studio starts and a signed session opens native `/project/default`. | Closing that tab cancels opening. Popup blocking prepares Studio and exposes the normal named Open action. Existing projects open only on request. |
| Tables, SQL, Auth users and Storage | Use original full-page Supabase Studio. | Environment-scoped native capabilities remain those of the integration contract. Vendor security headers are preserved. |
| Application connection and keys | Connection details show the project URL; Database connection contains direct and pool settings. | Key issuance/revocation remains explicit. One-time key disclosure and connection purpose selection remain available. |
| Auth and services configuration | Sign-in settings contains site URL, redirects, providers and signing controls. Services retains Realtime and Functions configuration. | App URLs, OAuth credentials, resource choices and desired services require real values or intent; they are not guessed. These configure self-hosted services, not replacement native user editors. |
| Email and domains | Current email state is shown. Installation operators can open Domain and email setup. | External DNS, credentials and domain ownership remain explicit; status and validation help the next action. |
| Usage, operations and updates | Usage stays scoped to the environment. Installation views are visible only to operators. | Updates, restore, destructive actions and role changes retain current authorization and confirmation. Automatic opening does not grant operator privileges. |
| Sign out and revocation | Both management grants and Studio epoch sessions are invalidated. | New factor enrollment and policy changes retain prior revocation guarantees. Automatic UI actions use the same authenticated endpoints. |

Code boundaries: `ui/Projects.tsx`, `ui/App.tsx`, `ui/Environments.tsx`, `ui/Studio.tsx`, `ui/Connection.tsx`, `src/control/studio.ts`, `src/control/studio-workspace.ts`. MFA, creation transactions, CLI and installed workers are described in the [complete delivery plan](../engineering/plans/2026-10-10-management-project-workflow.md).

## Before and after

Previously, project creation selected a provisioning page and required a later dashboard click; production entry could lead to a workspace wrapper around an embedded Studio. Now the same Create gesture starts the entire project-to-dashboard journey. Native Studio is the destination, and the original console remains available in its tab. Ordinary project visits do not start a dashboard automatically.

The optional legacy workspace URL remains a selector only. It does not embed or alter Studio. Existing scoped metadata and target-only ticket checks are retained.

## Failure and verification contract

Creation, provisioning, Studio startup and status request failures remain separate. Retrying status or opening an already-starting dashboard does not create another project or restart its pending request. Cancellation during an asynchronous handoff prevents navigation but does not undo completed provisioning. Automatic attempts do not repeatedly relaunch after a failure.

The real UI build is exercised by `lab/project-workflow-browser.ts` using the real application and Studio proxy with isolated native Auth/Studio fixture responses. It covers keyboard creation, automatic handoff, console retention, cancellation, closed/blocked tabs, creation refusal, startup and status recovery, mobile overflow, advanced settings disclosure, MFA enrollment and policy revocation. `tests/studio-navigation.test.ts` verifies native entry, unchanged body/framing policies and safe optional selection. Existing permissions, lifecycle, CLI and transfer tests remain required.

See the checkpoint export for actual command counts and independent reviews. No deployment, external DNS operation, live policy change or native container startup is established by this local fixture evidence.
