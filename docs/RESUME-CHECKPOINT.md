# Resume checkpoint

Snapshot: 2026-09-20. Read this before older chronological checkpoints in PROJECT.md. Repository files are the continuation source for Codex or Hermes.

## Current decision

Keep Supabase. Installation > organization > project > environment. Ownership is separate from server placement. Candidate: shared PostgreSQL, separate database and scoped credentials per environment, original Auth/REST per environment, shared Storage. Independent PostgreSQL remains the fallback if isolation, lifecycle or savings fail. This is a local prototype, not production approval or capacity certification for 10 or 100 projects.

[Reasons and alternatives](DECISIONS.md), [research and review index](HANDOFF.md), [saved diagrams](diagrams/README.md).

## Verified

- Local management console, queued provisioning, scoped keys and original Supabase services with retained volumes.
- Gateway admission: 8 requests/environment, 32/process, REST 3 matching its pool. These are experimental limits.
- REST client disconnect can leave SQL running. Admission is retained through bounded upstream settlement/draining. REST defaults are 8-second statement and 12-second transaction deadlines; trusted SQL is outside the hard-isolation claim.
- Combined-policy SDK probe: 1,001 correct operations. Latest recorded unit suites: 52 Bun tests, 269 assertions, 34 Python tests. These are prior results, not newly rerun during documentation.
- Encrypted selected-environment export: 29 live checks. Includes database, scoped configuration, object bytes/xattrs and tenant signing material. Latest export adds ICU locale metadata and table counts/hashes.

## Exact unfinished work

Baseline commit: `bb0bba1`. Uncommitted work: `lab/recovery-export.py`, `docs/evidence/recovery-export-checks.json`, and new `lab/recovery-restore-db.py`. Preserve it; the handoff archive includes it as unfinished source.

The source-dependent preflight is fixed. The first independent database restore passed 45 live checks, including the contents of 32 tables, locale, scoped connections and deadlines. See [database-stage evidence and limits](INDEPENDENT-RESTORE.md). The target is retained stopped. Do not create another target automatically; inspect `.lab/upstream/recovery-target.json` and reuse it for subsequent verification.

Complete roles, memberships, ACLs and settings now match in five retained-target checks. Adversarial cleanup fixes are implemented but still need failure-path verification. Original target Auth/REST now pass 11 live checks, including original-password login, stable identity, session validation and RLS row access. Their containers remain stopped. Next restore Storage, rebind its connection, reencrypt tenant signing keys under a fresh platform key, restore objects/xattrs and verify identity, old signed URLs and source/neighbor isolation. Database-only success does not complete recovery. Full server migration, organization transfer, upgrades, sustained capacity and production installation remain open.

## Runtime and secrets

Source durable containers are stopped, volumes retained. The independent recovery target also exists stopped with its own volume. Reinspect before execution. Preserve unrelated containers and all source volumes. The private artifact pointer is `.lab/upstream/recovery-latest.json`; load it programmatically without printing secrets. `.secrets/` and `.lab/` are excluded from the handoff ZIP, so the ZIP is not a usable data backup.

Local source container ceilings total 3840 MiB and 3.75 CPUs. They are not total host usage or production sizing. Recheck resources, stage source/target startup and retain existing guards.

## Continue

Work in `/home/sbarah/R/Projects/P/sbarbase`. Read `~/AGENTS.md`, `docs/HANDOFF.md`, this file and `lab/README.md`, then inspect Git and live resources. Use one active writer. No execution has been dispatched to Hermes. Changing assistant does not require moving the repository.

## Latest Storage checkpoint

Target Storage now exists stopped with separate metadata and object volume. Eight retained-target checks pass: exact files/xattrs, signing material, object-specific service-key downloads and foreign-secret denial. Initial failure was an incorrect common-content test expectation; explicit resume verified the existing restore without recreating it. Next: end-user Storage RLS and valid pre-export signed URL continuity. The bundle currently does not record a pre-export URL fixture; preserving private signing material alone is not proof of URL continuity. Do not overwrite the retained target or regenerate source artifacts silently.

## Latest identity and URL verification

Twelve end-user Storage checks pass: original login, own-object access, cross-owner download/sign denial and anonymous denial. Seven source-issued URL checks pass sequentially: source signing rows match export, the unchanged signature works on the independent target, tampering is rejected and both stacks stop. The URL was issued after export; pre-export issuance and public routing cutover remain unproven. Preserve the distinction. Next: explicit resumable recovery orchestration, failure-path tests and chronological pre-export fixture before management cutover.
