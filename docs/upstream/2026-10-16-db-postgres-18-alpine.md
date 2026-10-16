# Upstream review: db postgres:18-alpine

- Date: 2026-10-16
- Component: db (lab/images.lock.json)
- Pin: unchanged, postgres:17-alpine (17.11) after step A8

## What changed

`postgres:18-alpine` is PostgreSQL 18, a major version (step A8 of the [upstream currency plan](../engineering/plans/2026-10-14-upstream-currency.md)). For this image the move is not a tag change:

- The official image moved its data directory: from 18 on, `PGDATA` sits under a version directory and the volume is mounted at `/var/lib/postgresql`, not `/var/lib/postgresql/data`. A volume made by 17 is not picked up as it is.
- A major version needs `pg_upgrade` or a dump and restore; the 18 server does not start on a 17 data directory.
- Upstream Supabase builds its own PostgreSQL images on 15 and 17, and its services are tested against those.

## Affected surfaces

Only the component-profile fixtures and the live probes that start stock PostgreSQL from `lab/images.lock.json`; the installation's database runs the Supabase image from `lab/distro-image.lock.json`, which stays on 17. Moving the fixtures to 18 would change their mounts and test against a version the upstream services do not target.

## Breaking changes and migrations

The data directory layout and the major version upgrade above.

## Decision

Defer. Stay on the 17 line (A8 refreshes `postgres:17-alpine`, 17.11). Revisit when upstream Supabase ships a PostgreSQL 18 image; the fixtures would then move with it, with new mounts and a dump and restore of any retained fixture volume.
