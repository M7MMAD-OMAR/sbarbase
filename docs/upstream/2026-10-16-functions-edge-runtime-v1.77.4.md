# Upstream update: functions public.ecr.aws/supabase/edge-runtime:v1.77.4

- Date: 2026-10-16
- Component: functions (lab/functions-image.lock.json)
- Previous pin (rollback): `public.ecr.aws/supabase/edge-runtime:v1.77.0` at `sha256:5f555406dc0705d775323c58e27e856cca7b5b8d0ab726cc6a30b11fcfc7c6b3`
- New pin: `public.ecr.aws/supabase/edge-runtime:v1.77.4` at `sha256:ad45164b7548bf5820ed7be714789c17ac2865edce79a09cfb4c3756c3d365a8`

## What changed

Four patch releases, fixes only (step A2 of the [upstream currency plan](../engineering/plans/2026-10-14-upstream-currency.md)):

- [v1.77.1](https://github.com/supabase/edge-runtime/releases/tag/v1.77.1): emitter caches are memoized, and Deno cache databases are deleted only when actually corrupt.
- [v1.77.2](https://github.com/supabase/edge-runtime/releases/tag/v1.77.2): OpenTelemetry APIs in event workers.
- [v1.77.3](https://github.com/supabase/edge-runtime/releases/tag/v1.77.3): a build check of the runtime snapshot.
- [v1.77.4](https://github.com/supabase/edge-runtime/releases/tag/v1.77.4): the destination path of a sandboxed filesystem rename is normalized.

## Affected surfaces

Edge Functions only: one edge-runtime container per environment that turns them on, started from `lab/functions-image.lock.json`, and recreated on `write_pause.py resume` and on turning them on. No change to the JWT check in `lab/functions/main/index.ts`, the keys functions receive, or their network.

## Breaking changes and migrations

None. No database is touched.

## Rollback

Revert the adoption change, or put this exact previous pin back in lab/functions-image.lock.json and
run `/usr/bin/python3 lab/pin_update.py verify`. Do not restage it: `stage`
refuses a version that already has a review entry.

```json
{
  "tag": "public.ecr.aws/supabase/edge-runtime:v1.77.0",
  "id": "sha256:5f555406dc0705d775323c58e27e856cca7b5b8d0ab726cc6a30b11fcfc7c6b3",
  "digests": [
    "public.ecr.aws/supabase/edge-runtime@sha256:5f555406dc0705d775323c58e27e856cca7b5b8d0ab726cc6a30b11fcfc7c6b3"
  ]
}
```

No schema or data migrations, so the image rollback alone is the way back: `lab/upgrade.py rollback` and a restart.

## Decision

Adopt, staged on 2026-10-16 as its own version. Not accepted yet: the full suites pass on the staged pin, and the live gate (deploy, invoke, the 150 s wall clock and the JWT check through the gateway) has not run on the lab host.
