# Upstream update: meta public.ecr.aws/supabase/postgres-meta:v0.100.0

- Date: 2026-10-16
- Component: meta (lab/studio-image.lock.json)
- Previous pin (rollback): `public.ecr.aws/supabase/postgres-meta:v0.99.0` at `sha256:9a079ac1c94d89629262822a4bd1902d5b1be4adb464e5a0a8fd078aaed72158`
- New pin: `public.ecr.aws/supabase/postgres-meta:v0.100.0` at `sha256:145325b7e39f8ef4931d6e8cfd3f6f4d655de09d72a2ccc96d9513542c2202bd`

## What changed

One release, [v0.100.0](https://github.com/supabase/postgres-meta/releases/tag/v0.100.0) (step A6 of the [upstream currency plan](../engineering/plans/2026-10-14-upstream-currency.md)). The `/generators/*` routes (type generation) now dispatch through `@supabase/typegen`, generator options are validated before introspection, and the typegen and formatter dependencies are pinned exactly. No change to the introspection or query routes Studio uses for tables and SQL is listed.

## Affected surfaces

postgres-meta only: one container per environment while a Studio session runs (`lab/studio.py`, tier `operator.meta`), started for that session's Studio. It connects as the same `<e>_studio` login, so it can do no more than that login. It sits on the shared internal runtime network and has no authentication of its own, so while a session runs any container on that network can reach it, other environments' Edge Functions included. That exposure predates this update, which does not change it, and is tracked as its own fix. Closed later the same day: each session now runs on its own network, with meta on loopback inside Studio's namespace ([Studio integration](../engineering/STUDIO-INTEGRATION.md), section 1.1). No schema, HBA, Auth, REST or Storage change.

## Breaking changes and migrations

None declared upstream. postgres-meta runs no migrations in the environment database.

## Rollback

Revert the adoption change, or put this exact previous pin back in lab/studio-image.lock.json and
run `/usr/bin/python3 lab/pin_update.py verify`. Do not restage it: `stage`
refuses a version that already has a review entry.

```json
{
  "tag": "public.ecr.aws/supabase/postgres-meta:v0.99.0",
  "id": "sha256:9a079ac1c94d89629262822a4bd1902d5b1be4adb464e5a0a8fd078aaed72158",
  "digests": [
    "public.ecr.aws/supabase/postgres-meta@sha256:9a079ac1c94d89629262822a4bd1902d5b1be4adb464e5a0a8fd078aaed72158"
  ]
}
```

postgres-meta keeps no state and runs no migrations, so the image rollback is the way back: put the previous pin back. Studio 2026.10.12 (the Studio part of this step) has not been checked against v0.99.0; take the two back together.

## Decision

Adopt, staged on 2026-10-16 as its own version after the Studio part. The full unit suites pass on the staged pin. Not accepted yet: the live check (a Studio session per environment, table and SQL editors, and the TypeScript type generation in Studio) has not run on the lab host.
