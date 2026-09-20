# Container-generation migration

Design checkpoint, 2026-09-20. Not implemented. The current runtime refuses to
silently re-pin a changed database container, which is correct: this document
records what an explicit migration must do before anyone implements it.

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