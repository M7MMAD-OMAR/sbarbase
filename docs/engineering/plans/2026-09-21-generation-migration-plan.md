# Plan: the container generation migration

Executable plan for the one deferred piece that everything else waits on
(`docs/engineering/CONTAINER-GENERATION-MIGRATION.md`, status "Not implemented"). Written
2026-09-21 as the next piece of work, not as a design. The design is the other
document; this one says what to build, in what order, and what must be true before
any part of it touches the retained installation.

## Why this, and why now

Four open items converge here:

1. The retained database container cannot be recreated, so the retained placement
   cannot carry its resource tiers or its block IO limits
   (`docs/engineering/RESOURCE-POLICY.md` section 3.6).
2. `lab/durable-check.ts` is disabled by its own line 8 pending this migration, and
   it is the only writer of `.lab/upstream/probe.json`, the fixture both load
   vehicles read, so the arrival driven and mixed SDK load measurements cannot run
   (`docs/engineering/RESOURCE-POLICY.md` section 5.0).
3. No tier measurement can run on a placement with history, only on a fresh
   disposable one.
4. The pinned database image can never be changed on a retained installation.

## What must not change

- The authority registry, its tombstones and the rule inventory stay owned by the
  single-attempt pipeline. This plan adds one caller, not a second way to publish
  rules.
- Every refusal that exists today keeps refusing: a legacy container, a missing
  container with a retained volume, a preexisting volume on a target create, an
  unmatched generation pin.
- No secret, no private path and no container environment reaches evidence.

## Phases

**Phase 0, the fixture.** A disposable placement with a real database container
holding a real registry, so every phase can be exercised and crashed without the
retained installation. The fresh worker path already builds one
(`lab/fresh-worker-check.py`); reuse its namespace and cleanup rather than writing
a second one. Acceptance: the fixture builds and removes its own resources, and
`docker ps -a --filter label=io.sbarbase.owner` shows nothing left.

**Phase 1, intent before effect.** A private, fsynced migration record in that
database's authority state: migration UUID, old generation and old container id
with its last observed identity, the new container id once captured, name, owner,
pinned image, pgdata volume name and mount identity, and the desired rule
inventory digest. An existing or torn record blocks both ordinary startup and a
repeated migration. Acceptance: a test that starts a migration twice and shows the
second refuses, and a test that truncates the record and shows startup refuses.

**Phase 2, preconditions.** Refuse unless: no pending HBA journal, no pending
worker-effect receipt, no active authority anywhere in the old state, and the old
container is either verifiably gone (inspection errors fatal, never read as
absence) or explicitly stopped with the operator asserting it will not return. The
volume identity must equal the recorded one. Acceptance: one test per precondition,
each showing the refusal, plus a test that an inspection error is not treated as
absence.

**Phase 3, order of effects.** Initialize the new generation in the new container,
publish the desired rules through the owned single-attempt pipeline with parser
and reload acknowledgment, and only then record the new pin. Acceptance: a test
that removes the acknowledgment and shows the pin is not written, and that the
database keeps refusing while the record is present.

**Phase 4, evidence.** Archive the retired generation's record and the old
container's last observed HBA digest into the outcomes directory before the pin is
replaced, re-derive the rules from the inventory, and compare against the recorded
digest, stating any difference rather than implying byte equality. Acceptance: an
evidence file whose digest comparison is a real comparison, and a test that a
deliberate difference is reported and not swallowed.

**Phase 5, failure semantics.** Any uncertain step leaves the record in place and
the database refusing startup. No automatic retry, no new generation minted on
retry, no best-effort pin write. Acceptance: the crash tests below.

## Crash tests

Five, each on the disposable fixture, each asserting the state refuses startup and
is reconcilable exactly once, and each with its evidence file:

1. Crash after the intent record is written, before the old container is captured.
2. Crash after the old container is captured, before the new generation is
   initialized.
3. Crash after the new generation is initialized, before the rules are published.
4. Crash after the rules are published, before the pin is written.
5. Crash after the pin is written, before the retired generation is archived.

## Acceptance for the whole piece

1. All five crash tests pass on the fixture, with their evidence committed.
2. The retained database container is recreated once, deliberately, carrying
   `io.sbarbase.tier` and the per-device block IO limits, with its authority rules
   preserved or the difference stated and its data intact (verify the environment
   databases and a row count before and after).
3. `lab/durable-check.ts` is re-enabled: its recreation requirement is what this
   migration exists to satisfy. Its own acceptance run then writes
   `.lab/upstream/probe.json` again.
4. The two load vehicles run against that regenerated fixture and their evidence
   is committed: the arrival driven pressure experiment and the mixed SDK load.
5. `docs/engineering/CONTAINER-GENERATION-MIGRATION.md` changes status from "Not implemented"
   to the revision that implemented it, with the crash tests named.

## Explicitly out of scope

Automatic detection of a changed container, silent re-pinning, migration across
hosts, concurrent migrations of several databases, power-loss guarantees, and any
migration while the runtime is supervising the installation.

## First command to run

```
cd /home/sbarah/R/Projects/P/sbarbase && git log --oneline -1 && \
  /usr/bin/python3 lab/fresh-worker-check.py
```

It must pass before anything else is built, because it is the fixture every later
phase is tested on. If it does not pass, fix that first and nothing else.