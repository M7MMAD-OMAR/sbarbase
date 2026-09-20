# Durable legacy source adoption

Implemented 2026-09-20 per HBA-LEGACY-ADOPTION-DESIGN.md. Modules:
`lab/hba_adoption.py`, crash probe `lab/hba_adoption_crash_check.py`,
unit tests `lab/test_hba_adoption.py`.

## What the operation does

1. `publish_intent` exclusively captures the stopped source (exact CID, trusted
   name/owner/image, pgdata mount identity, full mount list, stopped inventory)
   plus fresh adoption and generation UUIDs into `hba-adoption.json` (fsynced,
   0600, checksummed). Any existing or torn intent blocks adoption; never unlink
   it to retry.
2. `execute` runs under fresh worker/effect/operation ownership via the startup
   gate (pending worker-effect receipts and pending HBA journals are refused).
   A preexisting generation pin must match the intent's exact generation or
   adoption refuses before the database is started.
3. Durable, immutable checkpoints under `hba-adoption-checkpoints/` separate:
   `database-started` (only the captured container is started), 
   `generation-initialized` (the intent's exact generation; INIT never repeated),
   `hba-completed` (actual rules preserved through the owned apply/reload
   pipeline; only a fresh revision marker is added, so stale prepared CAS
   requests lose), `source-stopped` (exact stopped identity reverified),
   `completed` (intent consumed).
4. A failed or uncertain stop raises before its checkpoint: adoption stays
   incomplete and the record is kept. A later `execute` resumes from durable
   checkpoints only.

## Recovery paths

- Killed after the database-started checkpoint: resume starts nothing new if the
  container already runs; the observed-running mode is checkpointed.
- Killed after a durable completion witness with a pending journal: settle the
  journal with the existing `hba_settlement.complete_applied` (no apply, no SQL),
  then resume adoption.
- Killed before the HBA journal exists: the intent and checkpoints carry the
  recovery; the HBA stage simply runs once.
- Initialization uncertainty inspects the same operation/generation; INIT is
  never dispatched twice and no replacement identity is ever minted.

## Adversarial review and fixes, 2026-09-20

Independent adversarial review: docs/reviews/legacy-adoption-review.md. Five
must-fix findings were established and all five are now fixed, each with a
regression test that fails against the previous code:

- MF-1 resume past the source-stopped checkpoint: the database-dependent steps
  (readiness, generation, HBA byte recheck) ran unconditionally, so a crash
  after source-stopped could never reach completion. They now run only while
  the HBA stage is outstanding, and a resume from source-stopped performs no
  database work at all.
- MF-2 generation pin written before initialization could refuse: a preexisting
  backend authority marker now refuses adoption without writing any pin, and a
  durable pin with no committed backend marker resolves by exactly one INIT on
  the recorded generation (never a new one).
- MF-3 unreadable checkpoints were treated as absent: existence now uses lstat
  and only FileNotFoundError is absence; denied state stays fatal, so an
  unreadable completion checkpoint can no longer replay the HBA stage.
- MF-4 existing checkpoints were read without the invariants every other reader
  applies: one strict reader now enforces O_NOFOLLOW, regular file, owner,
  0600, size limit and the envelope checksum.
- MF-5 captured mount identity was never revalidated: the live mount list and
  pgdata volume are now compared against the intent before the source is
  started and again after the final stop.

Lesser observations were also addressed: the checkpoint directory entry is now
fsynced, the two branches of checkpoint comparison use one rule, and the
running-versus-stopped discrimination no longer matches on error text.

## Explicit limits

Same-CID adoption does not fence raw legacy writers or queued Docker requests;
quiescence remains an explicit operational assumption. Revision rotation only
invalidates stale prepared CAS requests. No migration to a recreated container,
no recovery-target writers, no power-loss guarantee, and restarting PostgreSQL
terminates its sessions and reloads configuration (unchanged session behavior is
not claimed).

## Live verification

Live probe on a disposable pinned Supabase PostgreSQL 17 container, five phases
(healthy, kill after database-started, kill after hba-completed checkpoint, kill
after source-stopped checkpoint, kill after durable witness with pending
journal), 44 checks:
`docs/evidence/hba-adoption-crash-checks.json`. Retained source, recovery
targets and all volumes untouched.

## Retained adoption executed, 2026-09-20

The retained `sbarbase-durable-db` source was adopted with this operation:
lab/adopt-retained.py ran it and lab/verify-retained.py verified
it from durable evidence only (12 checks,
docs/evidence/retained-source-adoption.json). Only the captured database
container was started and stopped again; no application service and no recovery
target was touched. Reported evidence: the live pg_hba.conf digest equals the
recorded applied content, the pre-adoption bytes are preserved byte for byte
against the journal expected digest, exactly one fresh revision marker exists,
inventory rules exist for every catalog environment, no pending journal or
worker effect remains, and the source is stopped by exact identity. Quiescence
was checked before running (all source containers stopped, locks free, no
receipt, no journal) and remains an explicit assumption for raw legacy writers.
