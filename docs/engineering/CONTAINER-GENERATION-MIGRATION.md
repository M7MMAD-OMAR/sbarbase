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
   verifiably gone (absence verification that treats inspection errors as fatal)
   or explicitly stopped with the operator asserting it will not return. The
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
2. `old-captured` (the retired container stopped with the operator's assertion, or
   verifiably gone, with its mounts, volume identity and last observed HBA digest);
3. `retired-archived` (the retired generation's record and that digest);
4. `new-captured` (the retired container removed by exact id and the replacement
   created with its tier label and its per-device block IO limits on the same
   volume);
5. `generation-minted` (one generation, durable before it is used);
6. `generation-initialized` (registry registration and the pin replaced by
   archive-then-publish);
7. `rules-published` (the owned single-attempt pipeline, parser and reload
   acknowledged);
8. `archived` (the re-derived rules compared with the retired digest, then the
   record removed).

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
