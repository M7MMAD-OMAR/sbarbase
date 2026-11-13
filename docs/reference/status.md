[العربية](status.ar.md)

# Status

Observed on 2026-11-13. A published source release, an installed revision and a completed production qualification are separate facts.

## Versions and addresses

| Item | Current observation |
|---|---|
| Public source release | [v0.12.2](https://github.com/M7MMAD-OMAR/sbarbase/releases/tag/v0.12.2), commit `b73d57a`, with a source archive and SHA256SUMS |
| Owner installation | Base version `0.11.2` with private console revision `53b43dafa761b1071aa4b36a3cfc92f87965392d`; the guarded updater confirmed it |
| Documentation website | [base.sbarah.com](https://base.sbarah.com/), Arabic at `/docs/`, English at `/en/docs/` |
| Owner console | `https://sbarbase.sbarah.com`; other installations use their own configured origin |
| Development checkout | May contain unreleased changes. Read its Git revision and manifest; do not infer an installed version from it |

The private console revision keeps the installed dependency lock, service images and database runtime unchanged. It does not contain the PGMQ adapter, newer SDKs or capability-discovery additions from the public 0.12.2 source.

## What changed in the console

- Filled surfaces distinguish elevation without decorative borders. Keyboard focus remains visible.
- Workspace selectors and the sidebar footer remain accessible while navigation scrolls. Projects and environments can be selected directly.
- The connection workspace loads on demand and offers recovery after a failed load. Switching away from an undeployed Edge Function draft asks before discarding it.
- Browser tabs identify the project and environment. Native Studio adds a safe `workspace` display label to its URL while preserving its runtime host and signed authorization.
- Native select pickers follow the theme where the browser supports customizable selects; other browsers retain native controls.

[Console design](console-design.md) and [Studio](../guides/studio.md) explain the behavior.

## Observed checks

The console deployment candidate passed 1610 Bun tests, 185 upgrade-related Python tests, typechecks, the UI and website builds, and 46 isolated console browser checks. A separate browser check used the actual pinned Studio on the owner's public server and confirmed the project title, readable URL, authenticated identity assets and reload behavior. Auth health and REST requests returned HTTP 200 for each of the 4 environments using their existing publishable keys.

Fresh backups were created for the 4 environments, shared Storage metadata and management. Their checksums and archive readability were verified. This was not a full restore rehearsal. The database container and running service image identities remained unchanged; some service containers were recreated on their existing images during restart. The [deployment observation](../evidence/server-console-2026-11-13.json) records this bounded result.

These observations do not certify a new installation, a PostgreSQL image migration, full-host disaster recovery, workload capacity or complete Supabase Cloud feature parity.

## Remaining work

- Original Studio SMTP settings do not yet have a working save integration. Use the Sbarbase console's documented [Authentication settings](../guides/sign-in.md); do not treat the Studio form as proof that SMTP was applied.
- The public 0.12.2 source declares a manual PGMQ ownership and backup-metadata migration. A normal 0.11.2 update refuses it. Do not remove that declaration or force the update; see [upgrades](../guides/upgrades.md).
- Service image upgrades, including PostgreSQL 17.11, remain subject to independent compatibility, migration and recovery checks. No candidate image set was adopted by the private console update.
- Shared `pg_stat_statements` remains blocked for tenant isolation. Hosted billing, Git branching and other unsupported capabilities are not provided by a self-hosted Studio page.

See [deployment readiness](deployment-readiness.md) for the remaining acceptance boundaries, and [agents](../guides/agents.md) for version-specific API and MCP discovery.

<a id="source-version-and-evidence-scope-2026-11-13"></a>
<a id="private-storage-qualification-2026-11-08"></a>
<a id="current-state-2026-10-26"></a>
<a id="release-060-2026-10-23"></a>
<a id="releases-053-to-0512-2026-10-17-to-19"></a>
<a id="the-real-server-and-030-2026-10-16-and-17"></a>
<a id="management-and-first-project-update-2026-10-10"></a>
<a id="integration-status-2026-10-07"></a>
<a id="historical-evidence-before-the-current-integration"></a>
<a id="historical-test-suites-2026-09-24"></a>
<a id="historical-python-interpreter-probes-2026-09-25"></a>
<a id="historical-live-evidence"></a>
<a id="platform-and-management"></a>
<a id="environments-and-isolation"></a>
<a id="provisioning-and-crash-safety"></a>
<a id="recovery"></a>
<a id="import-from-supabase-inspection"></a>
<a id="install-with-docker-daily-backups-studio-and-upgrades-ci-clean-runner"></a>
<a id="empty-server-simulated-in-a-local-vm"></a>
<a id="roadmap-milestones-in-the-vm-2026-09-25"></a>
<a id="deployment-path-workstation-only"></a>
<a id="update-channel-2026-09-25"></a>
<a id="resources"></a>
<a id="historical-legacy-limitations-documentation-checkpoint-2026-10-06"></a>
<a id="current-priorities"></a>
<a id="september-next-step-record"></a>
<a id="preserved-october-verification-history"></a>

## Historical records

The previous [status history](status-history-2026-11-13.md), [readiness history](deployment-readiness-history-2026-11-13.md), [changelog](../../CHANGELOG.md) and dated [engineering checkpoints](../engineering/checkpoints.md) retain earlier source identities and limits. Historical pass counts are not current acceptance.
