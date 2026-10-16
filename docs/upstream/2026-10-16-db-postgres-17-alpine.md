# Upstream update: db postgres:17-alpine

- Date: 2026-10-16
- Component: db (lab/images.lock.json)
- Previous pin (rollback): `postgres:17-alpine` at `sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73`
- New pin: `postgres:17-alpine` at `sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24`

## What changed

The same tag, `postgres:17-alpine`, rebuilt upstream on 2026-09-21 (step A8 of the [upstream currency plan](../engineering/plans/2026-10-14-upstream-currency.md)). Both the pinned image and this one report `PG_VERSION=17.11`, so PostgreSQL itself does not change; the rebuild carries the Alpine base and its packages. The new digest is the one Docker Hub lists for `17-alpine` and `17.11-alpine`.

## Affected surfaces

Only the component-profile fixtures and the live probes that start stock PostgreSQL from `lab/images.lock.json` (`db`), for example `lab/run.py`, `lab/management-mfa-fixture.py` and `lab/pressure-response-check.py`. The installation's own database runs the Supabase PostgreSQL image from `lab/distro-image.lock.json`, which this step does not touch. No schema, HBA, Auth, REST or Storage change.

## Breaking changes and migrations

None: same PostgreSQL version, same data directory layout.

## Rollback

Revert the adoption change, or put this exact previous pin back in lab/images.lock.json and
run `/usr/bin/python3 lab/pin_update.py verify`. Do not restage it: `stage`
refuses a version that already has a review entry.

```json
{
  "tag": "postgres:17-alpine",
  "id": "sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73",
  "digests": [
    "postgres@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73"
  ]
}
```

The image rollback is the way back: put the previous pin back. Fixture data volumes stay readable by either build.

## Decision

Adopt, staged on 2026-10-16. PostgreSQL 18 (`postgres:18-alpine`) is deferred in its own entry. The full unit suites pass on the staged pin. Not accepted yet: the fixtures that start this image have not run against it on the lab host.
