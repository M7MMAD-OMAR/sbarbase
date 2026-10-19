# Upstream review: distro public.ecr.aws/supabase/postgres:17.11.0.005

- Date: 2026-10-19
- Component: distro (lab/distro-image.lock.json)
- Pin: unchanged, 17.6.1.166

## What changed

17.11.0.004 (2026-10-05) and 17.11.0.005 (2026-10-19) are builds of the PostgreSQL 17.11 line of the Supabase distribution ([supabase/postgres](https://github.com/supabase/postgres)). Moving from 17.6.1.166 crosses five PostgreSQL minor releases, 17.7 to 17.11 ([#2155](https://github.com/supabase/postgres/pull/2155)), and upstream's [17.11 breaking-changes notice](https://supabase.com/changelog/postgres-15-19-17-11-breaking-changes). Distribution changes since September include fixes to security issues in extension custom scripts ([#2491](https://github.com/supabase/postgres/pull/2491)), `pgrst_ddl_watch` no longer notifying PostgREST needlessly ([#2464](https://github.com/supabase/postgres/pull/2464)), a supautils bump to 3.4.4, tzdata from the PostgreSQL sources, and `pg_upgrade` fixes for `pg_graphql`, `pg_cron` and `plpgsql_check`. The tag on the registry is a build of the image; the GitHub releases named `17.11.0.005-*` cover its variants.

## Affected surfaces

Every surface: the installation's one database cluster, its bootstrap SQL, extensions, HBA publication, the provisioning SQL path, backup, restore and the generation pin of every environment. Under rule 3, a later adoption cannot reuse the evidence of the checkpoints in `docs/engineering/checkpoints.md` proven on 17.6.1.166, among them the source database fence, the export fence, managed target publication, independent database restore and the HBA replacement, revision and activation checkpoints; each must be proven again on the new image.

## Breaking changes and migrations

The four actions of the 17.11 breaking-changes notice, run in every environment database, the management database and `storage_metadata`. A database container may only be replaced by `lab/migrate-generation.py`; `lab/upgrade.py` refuses a target whose distro lock differs.

## Decision

Defer. This is step A7 of the [upstream currency plan](../engineering/plans/2026-10-14-upstream-currency.md), the riskiest step, and it runs last, only through the P1 flow, after P3 and its rehearsal in the VM. None of those has run, so this bump is not adopted here. The official self-hosting compose file still pins 17.6.1.136, so staying on 17.6.1.166 keeps us ahead of compose. When A7 runs it takes the newest 17.11 build of that day, with its own review entry.
