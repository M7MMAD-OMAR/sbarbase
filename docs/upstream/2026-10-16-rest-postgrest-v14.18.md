# Upstream update: rest public.ecr.aws/supabase/postgrest:v14.18

- Date: 2026-10-16
- Component: rest (lab/images.lock.json)
- Previous pin (rollback): `public.ecr.aws/supabase/postgrest:v14.15` at `sha256:2f8e7b656f09db697a8875177694b417b35cb76c21370de07fc54e711e902326`
- New pin: `public.ecr.aws/supabase/postgrest:v14.18` at `sha256:c7cd7e265a85b05248abf53f4473fcec7e327c282d0bd0cf60f1551996d09fcf`

## What changed

Three patch releases in the maintained v14 line, all fixes (step A1 of the [upstream currency plan](../engineering/plans/2026-10-14-upstream-currency.md)):

- [v14.16](https://github.com/PostgREST/postgrest/releases/tag/v14.16): the admin server no longer crashes with no way to recover.
- [v14.17](https://github.com/PostgREST/postgrest/releases/tag/v14.17): JWT validation no longer uses a stale current time.
- [v14.18](https://github.com/PostgREST/postgrest/releases/tag/v14.18): no more sporadic "PGRST303 JWT issued at future" errors, and correct log timestamps after long idle periods.

The official self-hosting compose file pins v14.17.

## Affected surfaces

REST only: one PostgREST per environment, started from `lab/images.lock.json`. No configuration key, role, grant or HBA rule changes; the fixes are in JWT time handling, which every environment's REST uses. The update channel check (`lab/upgrade-check.py`) keeps its REST candidate, v14.16, which is now an earlier release than the pin and is reported as a test-only earlier candidate.

## Breaking changes and migrations

None. PostgREST keeps no schema of its own.

## Rollback

Revert the adoption change, or put this exact previous pin back in lab/images.lock.json and
run `/usr/bin/python3 lab/pin_update.py verify`. Do not restage it: `stage`
refuses a version that already has a review entry.

```json
{
  "tag": "public.ecr.aws/supabase/postgrest:v14.15",
  "id": "sha256:2f8e7b656f09db697a8875177694b417b35cb76c21370de07fc54e711e902326",
  "digests": [
    "public.ecr.aws/supabase/postgrest@sha256:2f8e7b656f09db697a8875177694b417b35cb76c21370de07fc54e711e902326"
  ]
}
```

No schema or data migrations, so the image rollback alone is the way back: `lab/upgrade.py rollback` and a restart.

## Decision

Adopt, staged on 2026-10-16 as its own version. Not accepted yet: the full suites pass on the staged pin, and the live gate (the baseline in section 3 of the plan, the supabase-js lifecycle, embedded resource filters and a schema cache reload through the gateway) has not run on the lab host. Version 16 is deferred: [its entry](2026-10-16-rest-postgrest-v16.4.md).
