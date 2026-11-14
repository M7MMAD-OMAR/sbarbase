[العربية](status.ar.md)

# Status

Observed through 2026-11-14. A published source release, an installed revision and a completed production qualification are separate facts.

## Versions and addresses

| Item | Current observation |
|---|---|
| Source release | 0.13.2; check [Latest on GitHub](https://github.com/M7MMAD-OMAR/sbarbase/releases/latest) for publication and downloadable assets |
| Owner installation | Signed release v0.13.1 (`992be52`), installed through the ordinary release channel and confirmed by the guarded updater on 2026-11-14 ([evidence](../evidence/server-pgmq-bridge-2026-11-14.json)) |
| Documentation website | [base.sbarah.com](https://base.sbarah.com/), Arabic at `/docs/`, English at `/en/docs/` |
| Owner console | `https://sbarbase.sbarah.com`; other installations use their own configured origin |
| Development checkout | May contain unreleased changes. Read its Git revision and manifest; do not infer an installed version from it |

Version 0.13.1 drops the inherited PGMQ migration declaration, so the ordinary updater of 0.11.2 and later classifies the move as a normal update. The PGMQ repair is deferred: it runs only through `lab/backup.py repair-pgmq` after a verified backup that holds the legacy queues, or on a restored archive that passed the legacy guard. See [upgrades](../guides/upgrades.md). Service image locks do not change. The November 13 private console and SMTP observations below remain historical.

## What changed in the console

- Filled surfaces distinguish elevation without decorative borders. Keyboard focus remains visible.
- Workspace selectors and the sidebar footer remain accessible while navigation scrolls. Projects and environments can be selected directly.
- The connection workspace loads on demand and offers recovery after a failed load. Switching away from an undeployed Edge Function draft asks before discarding it.
- Browser tabs identify the project and environment. Native Studio adds a safe `workspace` display label to its URL while preserving its runtime host and signed authorization.
- Native select pickers follow the theme where the browser supports customizable selects; other browsers retain native controls.

[Console design](console-design.md) and [Studio](../guides/studio.md) explain the behavior.

## November 14 observation

Source `0044cb6` was deployed and its guarded activation confirmed on the owner server. Deletion through the Management API and project archive were exercised for project `i2s`, and native factor recovery was verified. These observations concern that revision only; the final 0.13.0 source identity and installed outcome require their own release verification.

Version 0.13.0 includes 20 token capabilities for 39 API/MCP operations, read defaults and explicit dangerous grants, legacy-token read migration, project archive with retained deleted environments and owner restore, sole-owner recovery through a local root command with factor verification through the management proxy and a separate 2-owner flow, the native lifecycle Auth path repair, Studio SMTP/template loading and private asset cache fixes, and portable gzip release verification. See the [release notes](../../CHANGELOG.md).

## Historical checks on November 13

The initial console-only candidate at `53b43da` passed 1610 Bun tests, 185 upgrade-related Python tests, typechecks, the UI and website builds, and 46 isolated console browser checks. A separate browser check used the actual pinned Studio on the owner's public server and confirmed the project title, readable URL, authenticated identity assets and reload behavior. Auth health and REST requests returned HTTP 200 for each of the 4 environments using their existing publishable keys.

Fresh backups were created for the 4 environments, shared Storage metadata and management. Their checksums and archive readability were verified. This was not a full restore rehearsal. The database container and running service image identities remained unchanged; some service containers were recreated on their existing images during restart. The [deployment observation](../evidence/server-console-2026-11-13.json) records this bounded result.

These observations do not certify a new installation, a PostgreSQL image migration, full-host disaster recovery, workload capacity or complete Supabase Cloud feature parity.

The subsequent SMTP repair candidate passed 1657 Bun tests, typechecking and 18 native browser checks including a stale-CDN simulation. The public server restored its native form from applied private settings. One Auth email request returned HTTP 200; the temporary test account was removed. Provider acceptance does not establish inbox placement. See the [SMTP observation](../evidence/server-smtp-2026-11-13.json).

## Remaining work

- Physical purge remains refused. Full Storage qualification and overall Studio latency remain open; the bounded repairs do not establish general performance.
- The SMTP and template compatibility repairs are limited to the pinned Studio build. Public v0.12.2 does not include these repairs.
- The PGMQ bridge is checked with real pgmq 1.5.1 on disposable databases (`lab/pgmq-check.py`, `lab/pgmq-access-check.py`, [evidence](../evidence/pgmq-access-2026-11-14.json)) and by unit tests: queue rows, grants, row level security and policies survive a backup and a replay of archive and supplement. `repair-pgmq` and the fenced cutover with a supplement have not run on a real installation with queues, and unlogged queues are covered by unit tests only. Partitioned queues, and queue tables with a trigger, rule, extra index or publication, are not supported. Backups that carry PGMQ data use manifest version 2, which versions before 0.13.2 refuse. Those versions restore a 0.13.1 backup (manifest version 1) without its queues and without a warning, and a 0.13.1 supplement restores its queues closed.
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
