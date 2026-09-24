[العربية](upgrades.ar.md)

# Upgrades

Sbarbase runs pinned upstream Supabase images. Upgrading means changing one pin, deliberately, with a written review and the full test gate. **No upstream release has been adopted through this process yet, and there are no automatic upgrades.** The binding rules are in the [upstream update policy](../engineering/UPSTREAM-UPDATE-POLICY.md); this page is the operator's view.

## What is pinned

Every upstream image is pinned by tag and digest in three lock files: `lab/distro-image.lock.json` (the Supabase PostgreSQL image), `lab/images.lock.json` (Auth, PostgREST and the stock PostgreSQL used by fixtures) and `lab/storage-image.lock.json` (Storage). Print the whole set and check that nothing floats:

```
/usr/bin/python3 lab/pin_update.py show
/usr/bin/python3 lab/pin_update.py verify
```

## Changing one component

1. Read the upstream release notes for the candidate version.
2. Stage the change. This writes a dated review entry under `docs/upstream/`, records the previous digest as the rollback pin and changes exactly one component:

   ```
   /usr/bin/python3 lab/pin_update.py stage --file lab/images.lock.json --component rest \
       --tag public.ecr.aws/supabase/postgrest:vX.Y --digest sha256:<64 hex> --note "why"
   ```

3. Complete the review entry: what changed, which Sbarbase surfaces it touches, breaking changes and migrations, and the adopt or defer decision.
4. Pass the adoption gate: the complete Python and Bun suites, and the live integration checks against the new version. No suite, no adoption.
5. Adopt one component at a time, so a failure points to one version change.

## Rolling back

Restore the previous pin recorded in the review entry, then restart the supervisor. If the newer version migrated data, rolling back may need a data migration of its own; record it in the same entry.

## Database containers are special

Startup never recreates a database container as an implicit upgrade. A managed database container is pinned by its exact identity, and replacing it (for a new image, for example) goes through `lab/migrate-generation.py`, which is journaled and crash-tested on disposable fixtures. Its first attended run on retained data has not happened yet; see [status](../reference/status.md).

## Limits

- Studio and postgres-meta are not pinned yet, because nothing serves them.
- There is no upgrade rehearsal on a server with real client data.
