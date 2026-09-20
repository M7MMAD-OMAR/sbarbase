# Upstream update policy

sbarbase builds on the pinned Supabase distribution (PostgreSQL, Auth, REST,
Storage). Upstream releases must be adoptable without surprises. This policy is
binding for every future change, assistant and release.

## Rules

1. **Pin everything.** Every upstream component (Postgres image, Auth, REST
   (PostgREST), Storage, CLI, migration scripts) is pinned to an exact version
   in one visible place. No floating tags (`latest`, `main`). The pin file is
   the single source for "what are we on".
2. **Read the changelog before touching anything.** For every candidate
   upstream release, before adopting it: read the upstream release notes and
   changelog, and record in a dated entry under `docs/upstream/`:
   - what changed,
   - which of our surfaces it affects (SQL bootstrap, HBA, Auth, REST, Storage,
     routing, tests),
   - breaking changes and required migrations,
   - the explicit adopt / defer decision and why.
3. **Impact analysis over hope.** A change that touches the database bootstrap,
   HBA publication, receipts/witnesses or the provisioning SQL path must state
   which checkpoints in `docs/RESUME-CHECKPOINT.md` it could invalidate. If a
   gate was proven against the old version, say so; do not silently reuse old
   evidence for a new version.
4. **Full test suite is the adoption gate.** A version is adopted only after:
   - the complete Python and Bun suites pass,
   - the live integration checks (fresh worker/SDK lifecycle, known-refusal,
     HBA probes) pass against the new version,
   - adversarial review finds no must-fix in the affected scope.
   No suite, no adoption.
5. **Adopt one component at a time.** Never bundle multiple upstream upgrades
   into one change; a failure must be attributable to one version delta.
6. **Rollback is part of the change.** Every adoption records how to return to
   the previous pin, and whether retained data needs a migration to do so.
7. **Ongoing tracking, not one-off.** Re-check upstream releases on a recurring
   cadence (cron is acceptable). The tracking log lives in `docs/upstream/`,
   newest entry first, one file per release reviewed.

## Current pins

Record the live pin set here and update it on every adoption:

| Component | Pinned version | Since | Notes |
|---|---|---|---|
| PostgreSQL | 17 (pinned image) | project start | probes use the same pin |
| Auth / REST / Storage | see `lab/` image pins | project start | pinned Supabase distribution |

Keep this table honest. An out-of-date pin table is treated as a bug.

## Where the log goes

`docs/upstream/YYYY-MM-DD-<component>-<version>.md` with the four sections from
rule 2. Link the entry from this file's log index below.

## Log index

- 2026-09-20: policy created; no upstream release reviewed yet.
