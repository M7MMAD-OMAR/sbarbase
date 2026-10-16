# Container-generation migration

Design checkpoint, 2026-09-20. Implemented 2026-09-21 in `lab/hba_migration.py`,
`lab/migrate-generation.py` and `lab/hba_generation_migration_check.py`, with the
five crash tests named in the section below, and run once, attended, on the
retained database on 2026-09-25 (see the section below). The current runtime refuses to
silently re-pin a changed database container, which is correct: this document
records what an explicit migration must do, and it now says what was built.

## The gap

A generation pin binds one exact container ID, name, owner label and pinned
image. When a managed database container is recreated (host move, image
replacement, or an operator rebuild) the new container has a different ID and
its filesystem, including the authority registry and its marker, comes from the
image rather than from the old container:

- startup refuses, because the pin no longer matches the live container;
- the old registry and its tombstones are gone with the old container, so
  "already revoked" evidence cannot be re-read;
- previous HBA rules exist only in the old container's filesystem.

Today the only supported answers are: keep the original container, or adopt a
retained container that still exists (`lab/adopt-retained.py`). Recreation has
no path.

## Required migration operation

1. **Explicit intent before any effect.** Publish an exclusive, fsynced private
   migration record in that database's authority state, containing: the
   migration UUID, the old generation and old container ID (with its last
   observed identity), the new container ID once captured, name, owner, pinned
   image, the pgdata volume name and mount identity, and the desired rule
   inventory digest. An existing or torn record blocks both ordinary startup and
   a repeated migration.
2. **Preconditions.** No pending HBA journal, no pending worker-effect receipt,
   and no active authority anywhere in the old state. The old container must be
   explicitly stopped with the operator asserting it will not return. An absent
   retired container is unsupported without a verified archive route. A new
   absent-container request refuses before publishing intent; an existing such
   intent remains a startup barrier and refuses before effects. The
   volume identity must match the recorded one; a different volume is a
   different database and must not be migrated silently.
3. **Order of effects.** Initialize the new generation in the new container
   first (the registry lives in the container), then publish the desired rules
   through the owned single-attempt pipeline with parser and reload
   acknowledgment, then, and only then, record the new pin. Removing or
   rewriting the old pin before the new publication is acknowledged is wrong.
4. **Preserve evidence, not just bytes.** Archive the retired generation's
   record and the old container's last observed HBA digest into the outcomes
   directory before the pin is replaced, so the migration is auditable. Unlike
   same-container adoption, byte-for-byte preservation of the old rules cannot
   be claimed from the new container: the rules must be re-derived from the
   inventory and compared against the recorded digest, with any difference
   stated explicitly.
5. **Failure and uncertainty.** Any uncertain step leaves the migration record
   in place and the database refusing startup. No automatic retry, no new
   generation minted on retry, and no "best effort" pin write. A failed stop or
   an unavailable inspection is fatal, never treated as absence.
6. **Explicitly outside this design.** Automatic detection of a changed
   container, silent re-pinning, migration across hosts, concurrent migrations of
   several databases, power-loss guarantees, and any migration while the runtime
   is supervising the installation.

## Why it is deferred

Recreation is not needed for the current deployment plan: the installation keeps
its containers, and the retention rules already document that source and target
containers are preserved with their volumes. Implementing migration without an
explicit operational need would add a second way to change authority identity,
which is exactly the class of change the current gates refuse. The hooks that
exist today are `hba_runtime.SourceHBA.before_start` (refuses a legacy container
or a missing container with a retained volume) and `TargetHBA.before_create`
(refuses a preexisting volume); both must keep refusing until this design is
implemented and crash-tested on disposable fixtures.

## What was implemented on 2026-09-21

`lab/hba_migration.py` holds the operation, `lab/migrate-generation.py` is the
operator command, and `lab/hba_generation_migration_check.py` runs the five crash
tests inside the disposable fixture that `lab/fresh-worker-check.py` builds
(`--generation-crash all`). The record is a private directory beside the pin
(`.lab/upstream/hba-migration`), and every effect is checkpointed before the next
one begins:

1. `intent` (the record itself, exclusive and fsynced, blocks startup);
2. `old-captured` (the retired container stopped with the operator's assertion,
   with its mounts, volume identity and last observed HBA digest);
3. `retired-archived` (the retired generation's record and that digest);
4. `new-captured` (the retired container removed by exact id and the replacement
   created with its tier label and its per-device block IO limits on the same
   volume);
5. `generation-minted` (one generation, durable before it is used);
6. `generation-initialized` (registry registration and the pin replaced by
   archive-then-publish);
7. `rules-published` (the owned single-attempt pipeline, parser and reload
   acknowledged);
8. `archived` (the observed rules must match the desired inventory before
   archival and completion; comparison with the retired digest remains evidence).
   Every retry rechecks the current rules, including a retry with an existing
   archive. A mismatch, or an older archive that did not verify the inventory,
   preserves the record and startup barrier.

The five crash tests are named `after-intent`, `after-old-captured`,
`after-recreated`, `after-generation` and `after-rules`. Each one kills the
operator command with SIGKILL at that durable checkpoint, then asserts that the
database refuses ordinary startup, refuses a repeated migration, reconciles
exactly once, and only afterwards admits startup again.

Three deviations from this document, each deliberate and each visible in the code:

- The pin is replaced immediately after the new generation's registry
  registration and immediately before the rules publication, not after it.
  `hba_apply.execute` calls `hba_generation.require` and `SourceHBA.publish` calls
  `hba_generation.read_existing`, so a pin written after the rules are
  acknowledged cannot gate that publication. This is the same order the
  same-container adoption path already uses.
- The retired generation's archive is a private directory beside the pin
  (`hba-migration-archive/<migration>`) rather than the HBA outcomes directory.
  Every file in the outcomes directory is validated as a journal-bound HBA
  outcome, so a migration record placed there would be rejected as invalid or
  would break readers that glob that directory.
- The pin replacement moves the retired pin's exact bytes into that archive
  instead of unlinking them, so the retirement stays byte-auditable.

## Attended run on the retained database, 2026-09-25

`lab/migrate-generation.py` still refuses the retained placement by default. An
attended run passes `--attended-retained` together with `--confirm-retained NAME`,
where NAME is the container name the pin (or, for `--reconcile`, the migration
record) already names; either flag alone, or any other name, refuses before the
intent is written. `lab/test_migrate_generation.py` covers the refusal, the
override, a mismatched or partial override and the same gate on `--reconcile`.

A new migration also refuses while the upgrade record (`.lab/upgrades/state.json`)
says an upgrade or rollback is not confirmed yet: until then `lab/upgrade.py` may move
the installation back on its own, and its way back describes the database as it was.
An unreadable record refuses too. `--reconcile` still finishes an interrupted
migration, since leaving it half done is worse.

The owner authorized the run, and it was made once on `sbarbase-durable-db`
(owner `durable-upstream`, volume `sbarbase-durable-pgdata`, network
`sbarbase-durable-net`, tier `system.db`, inventory from the runtime's own
`runtime.json`, retired container stopped with the assertion). Before it: no
runtime process, no HBA journal, no worker receipt, no migration record, and the
retired registry held 54 revoked operations and none active. Results, in
[docs/evidence/generation-migration-retained.json](../evidence/generation-migration-retained.json):

1. **Before the run.** The pin was archived, and a cold copy of the pgdata volume
   plus a `pg_dumpall` were taken under the gitignored
   `.lab/generation-migration-backup-20260925/`. Row counts were read from that
   cold copy in a network-less throwaway container: 7 databases, 166 tables, 851
   rows. The environment database exported by the earlier cutover is fenced on the
   source, so it was opened for counting and dumping on the copy only.
2. **The run.** Migration `0b01f97e-1f31-4f5b-bfa7-c312180ab854` completed and its
   record is gone. The replacement container carries `io.sbarbase.tier=system` and
   all four per-device block IO limits (read 256 MB/s, write 128 MB/s, 6000 read
   IOPS, 3000 write IOPS), which the retired container never had; `io.max` in its
   cgroup agrees. A second cold copy after the run gives the same count for every
   one of the 166 tables. The rules are **not** byte identical: all 17 retired
   rules are kept in their order and 12 are added (a studio, realtime and developer
   login rule for each of four environments), because the inventory builder has
   emitted those since the retired container last started. The pin now names the
   new container and generation; the retired pin's bytes are in the archive.
   Observed and not reconciled: `cpu.weight` reads 174 and `io.weight` reads
   `default 100`, which do not match the mapping RESOURCE-POLICY section 3.1 infers.

## What remains

3. **Done 2026-09-25: `lab/durable-check.ts` reworked, re-enabled and passing.** The owner
   decided (2026-09-25) that it becomes a non-destructive lifecycle probe on the
   migrated generation instead of a recreation probe, because its old step that
   removed every owned container would leave published environments unable to
   resume (startup resumes their Auth and REST from existing containers only).
   It now:
   - uses exactly the two published, unfenced environments `e_f61bf85...` and
     `e_1f0624...`, refusing clearly if either is unpublished, paused in routing,
     fenced or unprovisioned; it never touches the exported `e_60332245...` and
     provisions nothing (selection in `lab/durable-fixture.ts`, eight Bun tests);
   - starts the runtime with `lab/durable_runtime.py up`, runs the same SDK data
     path (signup, RLS insert, private upload, signed URL, neighbour denial), then
     stops and starts it through the same command and asserts the same database
     container id, an unchanged generation pin, every owned container resumed with
     its id, and every data, identity, object and signed URL check again;
   - removes the rows, objects and Auth users it created, stops the runtime, and
     only when all of that passes writes a fresh `probe.json` (a different earlier
     fixture is archived beside it as `probe.pre-<day>.json`, never overwritten)
     and its evidence file `docs/evidence/durable-lifecycle-restart.json`;
     `verification.json` is written on every run.
   Container recreation is covered by `lab/migrate-generation.py` (database) and
   `lab/upgrade.py` (services), not by this probe. Startup needs 6400 MiB available
   (3840 MiB placement plus the 2560 MiB start reserve); one attempt was refused for
   headroom before anything started. The run passed 31 checks: nine owned
   containers resumed with their ids, the database still `1f43fb01...` on generation
   `7c0432a2...`, the pin unchanged, and a fresh `probe.json` written with the stale
   one archived as `.lab/upstream/probe.pre-20260925.json`
   ([evidence](../evidence/durable-lifecycle-restart.json)).
4. **Load vehicles, run 2026-09-25 on that fixture, one result each way.** The
   mixed SDK load (`lab/sdk-load-check.ts --policy-regression`) passed with no
   failed operation. The sustained arrival run (`lab/gateway-overload-check.ts
   --sustained`) failed reproducibly: 14 of 600 target arrivals ended without an
   HTTP status, one every 2050 ms, while every neighbour arrival was correct and
   fast; the cause is not established. The pressure-sampling mode and the
   experimental-class phase of RESOURCE-POLICY sections 5.2 and 5.3 are not built.
   Numbers and limits: RESOURCE-POLICY.md section 5.0.

Out of scope: automatic detection of a changed container, silent re-pinning,
migration across hosts, concurrent migrations of several databases, power-loss
guarantees, and any migration while the runtime is supervising the installation.

## A generation change onto a new image

Upstream currency P1 lets a generation migration move the database onto the image of a newer distribution lock, for the PostgreSQL step (A7) and its return. `lab/migrate-generation.py --image-from-lock` takes the image from this checkout's `lab/distro-image.lock.json` and proves it on the local daemon first, the same way the runtime does at start: the digest in the lock must be one the daemon reports for that image, and the image id recorded is the daemon's own (`image_identity.resolved_id`), the identity a running container reports. Nothing is pulled.

The name, owner and pgdata volume stay; only the image changes. The intent records it (intent version 2, with an `image` field present only when it differs from the retired image; version 1 records keep loading unchanged). `validate_replacement` then requires exactly that image, the new generation pin names it, the retired pin archive keeps the old one, and the evidence records both (`target.image`, `retired_image`). `--reconcile` finishes onto the image the intent recorded and refuses `--image-from-lock`, so an interrupted change never moves to another image if the lock changes in between. `lab/test_hba_migration.py` interrupts after each of the seven phases and checks the resume. A run stopped inside the new-captured phase, after the retired container was removed and before the replacement was ready, is resumed too: when the retired container is proven gone (its inspection fails and a listing that succeeded does not show it), `create_replacement` adopts a container under the shared name only if its image, owner and tier labels and pgdata volume are exactly the replacement's, starts it if it stopped, and never creates a second one; any rerun after that checkpoint, the archived one included, starts a stopped replacement before the later steps, since the rule comparison runs in it. The three files written just before a checkpoint (`retired-generation.json`, `evidence.json`, `completed.json`) are kept on a rerun when the rerun derives the same record and refused when it differs, The finished record leaves in one rename (to `hba-migration.removing`, deleted next and cleared by the next removal if a kill interrupts that), so no part of it stays under the record's name; a run killed after `completed.json` and before the rename finishes on the rerun, only when the archived evidence is there. The intent is written whole beside the record and renamed into it, so a kill leaves no record, the whole intent, or an empty record directory. `lab/migrate-generation.py` removes such an empty directory where every run comes in (a `--reconcile` then says to start again; `upgrade.py database --continue` starts it again by itself), since nothing had started. The checkpoints, `retired-generation.json`, `evidence.json`, `completed.json` and the new generation pin are written under a temporary name, synced and then linked to their own name, so a kill never leaves them torn (the copies `docker cp` takes out of the retired container are taken again by a rerun before the container is removed, and the retired pin is archived with one rename); a torn intent or checkpoint found anyway (a disk fault) is still refused and needs review (`ResumeAfterRemovalTests`, `test_migrate_generation.py`).

This is only the container step, and `--image-from-lock` reads the lock of the checkout it runs in, so the checkout must already carry the new lock. The attended move does both in one maintenance window: `lab/upgrade.py database --to REF` prepares, and `database --continue` moves the checkout and then runs this command (`lab/database_move.py`, [upgrades guide](../guides/upgrades.md#moving-to-another-postgresql-image)). Until the checkout and the database match, startup refuses, because the pinned image and the running one differ. The move goes only forward once `--continue` has recorded it as started (it first refuses while an operation record waits to settle, so the generation change does not meet one later); a change that then keeps refusing, or a new image that never becomes ready on the volume once the retired container is removed, leaves the record waiting. For the second, a new image that never becomes ready after the retired container was removed, the exit is a rebuilt cluster taken into use (below); a change that keeps refusing while the retired container still exists has no exit yet. Nothing here has run against a real database yet.

## A generation change onto another volume

Upstream currency P3 part 1 ([whole-cluster restore](WHOLE-CLUSTER-RESTORE.md)). The runtime no longer names the database volume as a constant: `lab/database_volume.py` reads a private record (`database-volume.json` in the state directory) and falls back to `sbarbase-durable-pgdata` when it is absent, so an installation that never moved changes nothing; a record it cannot read, or one that is not private and well formed, refuses the start. `lab/durable_runtime.py` mounts and checks that volume, and `lab/upgrade.py database` passes it to the migration.

`lab/migrate-generation.py --to-volume NAME` moves the database container onto another existing pgdata volume, with or without `--image-from-lock`. The intent records it (version 3, `new_volume`, a name `sbarbase-durable-pgdata-<12 hex>` that differs from the retired one). Before the intent is written, and again inside the migration before the retired container is stopped (and on every rerun until the replacement runs on it), `require_cluster_volume` proves the volume exists, carries the runtime's owner label (the runtime refuses to mount any other) and holds a PostgreSQL data directory whose `PG_VERSION` names the major version the target image's server reports (`postgres -V`), through a read-only helper on the target image without network: an empty volume would make PostgreSQL initialize a new, empty cluster. That helper command has not run against the real image yet. The constant `sbarbase-durable-pgdata` is never a target, and neither is a volume an earlier move left behind (read from the archived evidence): after a move they hold kept, outdated clusters. `PG_VERSION` must be digits only and is compared as a whole major version. The replacement is created on it, adoption after a kill requires it, and once the change reaches its archive, the record names it, before the migration record is removed, so the runtime mounts it from then on. The evidence records `volume` (retired) and `target_volume`. `--reconcile` refuses `--to-volume`. The old volume is kept. Unit tested only; `lab/cluster_rebuild.py` (part 3) creates the rebuilt volume.

## Superseding a stuck migration

Upstream currency P3 part 4. A move whose new image never becomes ready leaves one exact state: the retired container captured (`old-captured`), its generation archived (`retired-archived`) and the container removed, a replacement created under the durable name on the old volume and never captured (`new-captured` absent), and the generation pin still naming the retired container. `lab/hba_migration.py supersede`, run as `lab/migrate-generation.py --to-volume NAME --supersede-stuck` (by `lab/cluster_takeover.py`), handles that state only; any other refuses unchanged. A retired container that still exists, a later checkpoint, a pin that names another container or generation, or a container under the name that is not exactly the stuck replacement (image, owner, tier, volume) is refused before anything is written.

Each step is written before the next and can be run again after a kill:

Before each of the first three, on every rerun until the record is gone, the stuck state is checked again (no later checkpoint, the pin unchanged, the retired container gone), so a migration that moved on meanwhile is never superseded.

1. The stuck intent and its two checkpoints are archived whole as `superseded.json` in that migration's archive, beside its retired generation, with the id of the migration that supersedes it, what it moves onto (volume, image, rule inventory) and the id of the replacement to remove. A rerun keeps that record when it is the same and refuses another volume, image or inventory.
2. The replacement it names is inspected again by id and removed only when its identity still matches; a removal the daemon acknowledges but does not perform refuses.
3. The record is removed in one rename, as a finished one is.
4. A version 4 intent is published under the archived id, naming the superseded migration (`supersedes`), the rebuilt volume and, when it differs, the image. A run stopped before this finds the waiting archive (one where neither the stuck migration nor its superseding one has evidence; more than one refuses) and publishes the same intent. While it waits, no other intent is published, since only the superseding migration can start without the retired container.

Once the archive exists, the stuck migration is no longer resumed on its own (`execute` refuses it and points to `lab/cluster_takeover.py`). The version 4 migration then runs as a version 3 one, except that its retired container is gone: its `old-captured` and `retired-archived` checkpoints are copied from the superseded archive, which must name it, and never taken from a container. No generation is minted twice, and nothing is deleted before it is archived. Its evidence records `supersedes`; `superseded.json` is not evidence, so `retired_volumes` reads only completed migrations and marks the old volume once the version 4 migration finishes. The old volume is kept, but it is not untouched: the replacement that never became ready ran the newer image on it. The rebuilt cluster holds what the backup run held, so writes made after that run are lost. Unit tested only (`SupersedeTests` in `lab/test_hba_migration.py`, `lab/test_cluster_takeover.py`).
