# Upstream update: auth public.ecr.aws/supabase/gotrue:v2.197.0

- Date: 2026-10-16
- Component: auth (lab/images.lock.json)
- Previous pin (rollback): `public.ecr.aws/supabase/gotrue:v2.196.0` at `sha256:c0c25187a6b835e65a6f6e6c6b39d090e832d40e6de5186f2c038e0411944232`
- New pin: `public.ecr.aws/supabase/gotrue:v2.197.0` at `sha256:1736a63078f5922b198c4cbe50f80ab9a2d3b54fe8b7b6cfb2e9dc5dbbc12c6b`

## What changed

One stable release, [v2.197.0](https://github.com/supabase/auth/releases/tag/v2.197.0) ([compare](https://github.com/supabase/auth/compare/v2.196.0...v2.197.0)), step A4 of the [upstream currency plan](../engineering/plans/2026-10-14-upstream-currency.md):

- MFA recovery codes: a new factor type with endpoints to generate, regenerate, verify and delete them, and audit events.
- Login events for OAuth server token exchanges.
- `expires_at` on one-time tokens.
- SCIM tables (`scim_users`, `scim_tokens`, digests only).

## Affected surfaces

Auth in every environment and the management realm's Auth, which share the image in `lab/images.lock.json`. Five additive migrations run in each Auth database (the `auth` schema), among them `ALTER TYPE auth.factor_type ADD VALUE 'recovery_code'`, which only the type's owner can run; each Auth migrates with its own login, which owns its schema. No JWT or JWKS change. The update channel check (`lab/upgrade-check.py`) now uses v2.196.0, the previous pin, as its Auth candidate: an earlier release, reported as test-only. That candidate runs on a schema v2.197.0 already migrated, in CI and the rehearsal VM only.

## Breaking changes and migrations

None declared upstream. Every migration is additive and written with IF NOT EXISTS, but there are no down migrations and an added enum value cannot be removed.

## Rollback

Revert the adoption change, or put this exact previous pin back in lab/images.lock.json and
run `/usr/bin/python3 lab/pin_update.py verify`. Do not restage it: `stage`
refuses a version that already has a review entry.

```json
{
  "tag": "public.ecr.aws/supabase/gotrue:v2.196.0",
  "id": "sha256:c0c25187a6b835e65a6f6e6c6b39d090e832d40e6de5186f2c038e0411944232",
  "digests": [
    "public.ecr.aws/supabase/gotrue@sha256:c0c25187a6b835e65a6f6e6c6b39d090e832d40e6de5186f2c038e0411944232"
  ]
}
```

Forward only for the data: an image rollback leaves the five migrations in every Auth database. The way back for the data is the backups `lab/upgrade.py` takes before the move: each environment's `restore`, and `restore-management` for the operator accounts ([backup guide](../guides/backup-and-restore.md)). Whether v2.196.0 runs on the migrated schema is untested.

## Decision

Adopt, staged on 2026-10-16 as its own version. It needs P2 and P5, whose code is reviewed. Not accepted yet: the full suites pass on the staged pin, and the live gate (the MFA fixture, sign-in providers, SMTP, the SDK revocation retry, and the management realm's sign-in) has not run on the lab host.
