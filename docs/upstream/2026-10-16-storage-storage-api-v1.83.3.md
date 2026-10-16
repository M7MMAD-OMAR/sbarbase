# Upstream update: storage public.ecr.aws/supabase/storage-api:v1.83.3

- Date: 2026-10-16
- Component: storage (lab/storage-image.lock.json)
- Previous pin (rollback): `public.ecr.aws/supabase/storage-api:v1.73.1` at `sha256:c24fb33cc2fa38d0f9582a30312907fc56da333fb9ba0646833186197e4982f3`
- New pin: `public.ecr.aws/supabase/storage-api:v1.83.3` at `sha256:2cea04f1ec8f174521f79382334ea3a97959b1a6770be30c6d6bc4a9e2779428`

## What changed

About 75 releases from v1.74.0 to v1.83.3 (step A5 of the [upstream currency plan](../engineering/plans/2026-10-14-upstream-currency.md)). Notable:

- Object versioning in stages: version-aware listing ([v1.76.0](https://github.com/supabase/storage/releases/tag/v1.76.0)), the `bucketid_objname` index dropped ([v1.77.0](https://github.com/supabase/storage/releases/tag/v1.77.0)), bucket versioning status and version-aware writes ([v1.81.0](https://github.com/supabase/storage/releases/tag/v1.81.0)).
- Lifecycle configuration behind a feature flag ([v1.74.0](https://github.com/supabase/storage/releases/tag/v1.74.0)); content encoding ([v1.80.0](https://github.com/supabase/storage/releases/tag/v1.80.0)).
- Hardening: S3 multipart bound to bucket and key (v1.79.6), signed TUS (v1.80.2), repeated presigned header values refused (v1.82.2); grants on `storage.objects`, `buckets` and `buckets_analytics` narrowed for `anon` and `authenticated` ([v1.77.7](https://github.com/supabase/storage/releases/tag/v1.77.7)).
- An "ahead migration" guard ([v1.80.0](https://github.com/supabase/storage/pull/1483)): from that version an older process no longer rewinds a tenant migrated by a newer one.
- S3 session-token signing also accepts the tenant id as the signing secret ([v1.78.0](https://github.com/supabase/storage/pull/1340)); the JWT check against the tenant secret or JWKS stays.

## Affected surfaces

The one shared, tenant-aware Storage process, from `lab/storage-image.lock.json`: the widest blast radius of any service, since every environment's Storage pauses while it restarts. Tenant migrations 0068 to 0074 run in every environment database (the `storage` schema), including index changes made concurrently and the grant narrowing, which reads the role names from `storage.anon_role` and `storage.authenticated_role`. Multitenant migration 0030 adds `tenants.feature_object_versioning` in `storage_metadata`. The restore barrier (`lab/restore_barrier.py`) admits a Storage container by its object writer settings; whether v1.83.3 keeps them is part of the live gate.

## Breaking changes and migrations

None declared upstream, but the image move is one-way. v1.73.1 lacks the ahead guard, so it would take a migrated tenant for one that is behind and could rewind its recorded version; and after 0072 dropped `bucketid_objname`, the `ON CONFLICT (name, bucket_id)` upsert of images before v1.77.0 likely finds no matching index (inferred from source, untested).

## Rollback

Revert the adoption change, or put this exact previous pin back in lab/storage-image.lock.json and
run `/usr/bin/python3 lab/pin_update.py verify`. Do not restage it: `stage`
refuses a version that already has a review entry.

```json
{
  "tag": "public.ecr.aws/supabase/storage-api:v1.73.1",
  "id": "sha256:c24fb33cc2fa38d0f9582a30312907fc56da333fb9ba0646833186197e4982f3",
  "digests": [
    "public.ecr.aws/supabase/storage-api@sha256:c24fb33cc2fa38d0f9582a30312907fc56da333fb9ba0646833186197e4982f3"
  ]
}
```

An image rollback alone is not a way back. The way back is the backups `lab/upgrade.py` takes before the move: `restore-storage` for `storage_metadata` and `restore` for every environment, with Storage stopped. When the previous version cannot start, `lab/backup.py start-database` starts the database alone for those restores (P4, [backup guide](../guides/backup-and-restore.md#when-sbarbase-cannot-start-after-a-way-back)).

## Decision

Adopt, staged on 2026-10-16 as its own version, as a one-way move whose way back is the restore. It needs P4, whose code is reviewed (commit `2a7e914`). The P4 procedure covers an upgrade that changed Storage alone, so when releases are cut this version needs a release of its own whose `minimum_from` is the release just before it; otherwise one upgrade can cross the Auth change (A4) together with this one. Not accepted yet: the full suites pass on the staged pin, and the live gate (tenant migrations on every environment, the ahead guard, uploads and signed URLs through the gateway, the restore barrier's admission of the new container, and the P4 drill that migrates forward, fails back and restores) has not run on the lab host.
