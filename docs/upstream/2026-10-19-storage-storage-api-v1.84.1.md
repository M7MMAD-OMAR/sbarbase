# Upstream review: storage public.ecr.aws/supabase/storage-api:v1.84.1

- Date: 2026-10-19
- Component: storage (lab/storage-image.lock.json)
- Pin: unchanged, v1.83.3 after step A5

## What changed

Five releases after the staged pin, all published on 2026-10-16 and 2026-10-19 ([v1.83.3...v1.84.1](https://github.com/supabase/storage/compare/v1.83.3...v1.84.1)):

- [v1.83.4](https://github.com/supabase/storage/releases/tag/v1.83.4): the list-v2 success response gets a declared schema.
- [v1.83.5](https://github.com/supabase/storage/releases/tag/v1.83.5): TUS closes its temporary part readers after S3 uploads.
- [v1.83.6](https://github.com/supabase/storage/releases/tag/v1.83.6): TUS keeps its database lease until the upload finishes.
- [v1.84.0](https://github.com/supabase/storage/releases/tag/v1.84.0): query pipelining in the PostgreSQL driver, behind a new `DATABASE_ENABLE_PIPELINING` setting that is off unless set to `true`, and a fix for a cancellation that could stall queued requests on a pipelined client. No existing default in `src/config.ts` moved.
- [v1.84.1](https://github.com/supabase/storage/releases/tag/v1.84.1): single-object file writes are staged before they are published.

No release in the range adds a migration.

## Affected surfaces

Storage only: the one shared tenant-aware process. TUS uploads and the file backend's write path change; we do not set `DATABASE_ENABLE_PIPELINING`, so pipelining stays off. No schema, HBA, Auth, REST or routing change.

## Breaking changes and migrations

None declared upstream, and no migrations.

## Decision

Defer. A5 staged v1.83.3, which ships in v0.5.10 to v0.5.12, and its live gate (tenant migrations, uploads, signed URLs, the restore pause and the P4 way back) has not run. The [upstream currency plan](../engineering/plans/2026-10-14-upstream-currency.md) holds the next Storage pin until the staged one is accepted or reverted, and each release crosses one upstream component at a time. These are fixes and an opt-in feature with no migrations, so nothing here is urgent. Restage A5 to the newest stable release when its live gate is about to run, so the gate proves the version that ships.
