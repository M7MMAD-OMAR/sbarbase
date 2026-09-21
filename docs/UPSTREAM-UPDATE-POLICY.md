# Upstream update policy

sbarbase builds on the pinned Supabase distribution (PostgreSQL, Auth, REST,
Storage). Upstream releases must be adoptable without surprises. This policy is
binding for every future change, assistant and release.

## Rules

1. **Pin everything.** Every upstream component (Postgres image, Auth, REST
   (PostgREST), Storage, CLI, migration scripts) is pinned to an exact version
   in one visible place. No floating tags (`latest`, `main`). The pin file is
   the single source for "what are we on". Studio and postgres-meta are
   components of the same kind, adopted per environment: see the pending
   components note under Current pins.
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

Record the live pin set here and update it on every adoption. `lab/pin_update.py show`
prints this table from the lock files, and `lab/pin_update.py verify` refuses a
floating tag or an unpinned component.

| Component | Lock file | Pinned version | Digest (short) | Since | Notes |
|---|---|---|---|---|---|
| Distribution (`db` used by the installation) | `lab/distro-image.lock.json` | public.ecr.aws/supabase/postgres:17.6.1.166 | b3bfedb10741 | project start | probes use the same pin |
| Stock PostgreSQL (`db` in the component profile) | `lab/images.lock.json` | postgres:17-alpine | 18cfe3ef5e68 | project start | used by component-profile fixtures |
| Auth | `lab/images.lock.json` | public.ecr.aws/supabase/gotrue:v2.196.0 | c0c25187a6b8 | project start | GoTrue |
| REST | `lab/images.lock.json` | public.ecr.aws/supabase/postgrest:v14.15 | 2f8e7b656f09 | project start | PostgREST |
| Storage | `lab/storage-image.lock.json` | public.ecr.aws/supabase/storage-api:v1.73.1 | c24fb33cc2fa | project start | tenant-aware |

Keep this table honest. An out-of-date pin table is treated as a bug.

### Pending components

Studio and postgres-meta are adopted per environment ([decision](DECISIONS.md)) but
are not pinned yet, because nothing serves them. Each enters this table in its own
change with its own dated review entry, one component at a time. Two pairs exist and
one has to be chosen on purpose: the pair already measured on this host is
`studio:2026.07.27-sha-cbb076d` with `postgres-meta:v0.96.6`, and the pair named by
the current upstream self-hosting compose is `studio:2026.09.07-sha-7996410` with
`postgres-meta:v0.99.0`. The compose's database image must not be taken at all;
this installation pins `17.6.1.166`. Neither Studio nor postgres-meta is covered by
any existing evidence, so no recorded gate result may be reused for them, and the
adoption has to state which checkpoints it invalidates. [Integration
specification](STUDIO-INTEGRATION.md) section 9 lists them.

## Staging an update

```
/usr/bin/python3 lab/pin_update.py verify        # no floating tags, every component pinned
/usr/bin/python3 lab/pin_update.py show          # the whole pin set with digests
/usr/bin/python3 lab/pin_update.py stage --file lab/images.lock.json --component rest \
    --tag public.ecr.aws/supabase/postgrest:v14.16 --digest sha256:<64 hex> --note "why"
```

`stage` writes the dated review entry first, records the previous digest as the
rollback pin, changes exactly one component and refuses a floating tag, an
unknown component, an unchanged digest or a second entry for the same version.
Completing the entry and passing the adoption gate are still the operator's job.

## Where the log goes

`docs/upstream/YYYY-MM-DD-<component>-<version>.md` with the four sections from
rule 2. Link the entry from this file's log index below.

## Log index

- 2026-09-20: policy created; no upstream release reviewed yet.
