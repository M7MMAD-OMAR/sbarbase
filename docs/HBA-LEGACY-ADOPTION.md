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

## Explicit limits

Same-CID adoption does not fence raw legacy writers or queued Docker requests;
quiescence remains an explicit operational assumption. Revision rotation only
invalidates stale prepared CAS requests. No migration to a recreated container,
no recovery-target writers, no power-loss guarantee, and restarting PostgreSQL
terminates its sessions and reloads configuration (unchanged session behavior is
not claimed).

## Evidence

Live probe on a disposable pinned Supabase PostgreSQL 17 container, four phases
(healthy, kill after database-started, kill after hba-completed checkpoint, kill
after durable witness with pending journal):
`docs/evidence/hba-adoption-crash-checks.json`. Retained source, recovery
targets and all volumes untouched.
