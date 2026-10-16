# Upstream update: realtime public.ecr.aws/supabase/realtime:v2.144.0

- Date: 2026-10-16
- Component: realtime (lab/realtime-image.lock.json)
- Previous pin (rollback): `public.ecr.aws/supabase/realtime:v2.138.1` at `sha256:7a6d995635f747b566079e51b1a1388dded8b2d0dfef1eda5afe98f6c9e5567e`
- New pin: `public.ecr.aws/supabase/realtime:v2.144.0` at `sha256:d97bf891884ef035a5068f01e2ea1ce0e9649c21c19ab14a91d70fcfea3b03d4`

## What changed

About 26 releases from v2.138.2 to v2.144.0, mostly fixes, presence metrics and serializer hardening (step A3 of the [upstream currency plan](../engineering/plans/2026-10-14-upstream-currency.md)). Notable:

- [v2.138.6](https://github.com/supabase/realtime/releases/tag/v2.138.6): base grants on `realtime.messages` restored for `anon`, `authenticated` and `service_role`, each only when that role exists.
- [v2.140.1](https://github.com/supabase/realtime/releases/tag/v2.140.1): changes committed behind a synchronous standby are handled.
- [v2.140.4](https://github.com/supabase/realtime/releases/tag/v2.140.4): a tenant with a JWKS and no shared secret no longer crashes; HS tokens are refused without a secret and asymmetric ones are checked against the JWKS. F1 (asymmetric keys) needs this.
- v2.140.10 and v2.140.11: a bad presence key or message can no longer crash a shared shard or the dispatch.
- [v2.141.0](https://github.com/supabase/realtime/releases/tag/v2.141.0): private channels may be authorized through a Postgres function, behind a feature flag that is off by default.
- [v2.143.3](https://github.com/supabase/realtime/releases/tag/v2.143.3): messages sent in one transaction replay in send order.

## Affected surfaces

Realtime only: one container per environment that turns it on, from `lab/realtime-image.lock.json`. Five new tenant migrations run in each such environment database, in the `realtime` schema: the `realtime.messages` grants, `realtime.settled_changes()`, `realtime.authorize(...)` (owner set to `supabase_realtime_admin`) and its replacement, and `clock_timestamp()` defaults on `realtime.messages`. `lab/durable_runtime.py` starts Realtime with migrations when its pinned image changed, and its login is a superuser only during that start (`realtime_start`, then `realtime_grants`), so the grants and the ownership change can run. The base grants help only because the API roles are named `anon`, `authenticated` and `service_role` here.

## Breaking changes and migrations

None declared upstream. The migrations are additive; v2.140.6 edited three 2021 and 2022 migrations in place, which only matters for a fresh schema.

## Rollback

Revert the adoption change, or put this exact previous pin back in lab/realtime-image.lock.json and
run `/usr/bin/python3 lab/pin_update.py verify`. Do not restage it: `stage`
refuses a version that already has a review entry.

```json
{
  "tag": "public.ecr.aws/supabase/realtime:v2.138.1",
  "id": "sha256:7a6d995635f747b566079e51b1a1388dded8b2d0dfef1eda5afe98f6c9e5567e",
  "digests": [
    "public.ecr.aws/supabase/realtime@sha256:7a6d995635f747b566079e51b1a1388dded8b2d0dfef1eda5afe98f6c9e5567e"
  ]
}
```

The migrations change the `realtime` schema in each environment that runs Realtime and are not undone by an image rollback. Whether v2.138.1 runs on the migrated schema is untested; until it is, the backups `lab/upgrade.py` takes before the move, restored per environment, are the way back.

## Decision

Adopt, staged on 2026-10-16 as its own version. It needs P5, whose code is reviewed. Not accepted yet: the full suites pass on the staged pin, and the live gate (its migrations, a replication slot per environment, Postgres Changes and Broadcast through the gateway, and the image rollback on a migrated schema) has not run on the lab host.
