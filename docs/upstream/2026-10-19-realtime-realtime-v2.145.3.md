# Upstream review: realtime public.ecr.aws/supabase/realtime:v2.145.3

- Date: 2026-10-19
- Component: realtime (lab/realtime-image.lock.json)
- Pin: unchanged, v2.144.0 after step A3

## What changed

Eight releases after the staged pin, from 2026-10-16 to 2026-10-19 ([v2.144.0...v2.145.3](https://github.com/supabase/realtime/compare/v2.144.0...v2.145.3)):

- [v2.144.1](https://github.com/supabase/realtime/releases/tag/v2.144.1): the Postgres Changes rate counter restarts with the tenant's current limit.
- [v2.144.2](https://github.com/supabase/realtime/releases/tag/v2.144.2): polls again at once after a full batch with no deliveries.
- [v2.144.3](https://github.com/supabase/realtime/releases/tag/v2.144.3) and [v2.144.4](https://github.com/supabase/realtime/releases/tag/v2.144.4): the region rebalancer only moves to a region that has nodes, and no longer picks a node when it only needs the region.
- [v2.145.0](https://github.com/supabase/realtime/releases/tag/v2.145.0): a presence track latency metric.
- [v2.145.1](https://github.com/supabase/realtime/releases/tag/v2.145.1): `realtime.messages` partitions are created only when missing, and `ALTER TABLE ... OWNER TO supabase_realtime_admin` runs on new partitions only, which avoids needless locks.
- [v2.145.2](https://github.com/supabase/realtime/releases/tag/v2.145.2): a single broadcast payload type is picked from `Content-Type`.
- [v2.145.3](https://github.com/supabase/realtime/releases/tag/v2.145.3): a static `secret_key_base` in the dev and test configuration only.

No release in the range adds a tenant migration; the code changes sit in `lib/` and the dev, test and base configuration.

## Affected surfaces

Realtime only, in each environment that turns it on. v2.145.1 touches how Realtime manages its daily `realtime.messages` partitions while it runs. Here its login is a superuser only during the migrating start (`realtime_start` in `lab/durable_runtime.py`), and later partitions are created as the ordinary `<e>_realtime` login. Running the ownership change on fewer partitions should only reduce what that login attempts, but it is unproven here; it belongs in the same live gate. The single-node `region: local` tenant does not use the rebalancer.

## Breaking changes and migrations

None declared upstream, and no migrations.

## Decision

Defer. A3 staged v2.144.0, which ships in v0.5.10 to v0.5.12, and its live gate (its migrations, a replication slot per environment, Postgres Changes and Broadcast through the gateway, and the image rollback on a migrated schema) has not run. The [upstream currency plan](../engineering/plans/2026-10-14-upstream-currency.md) holds the next Realtime pin until the staged one is accepted or reverted. Restage A3 to the newest stable release when its live gate is about to run, and check partition creation by the non-superuser login in that gate.
