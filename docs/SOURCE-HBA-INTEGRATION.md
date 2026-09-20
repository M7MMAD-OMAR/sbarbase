# Owned source HBA integration

Updated 2026-09-20. The source runtime now uses the journal, authority, apply witness and live completion protocol. This is local experimental integration, not installation-wide revocation or production certification.

## Runtime behavior

`Runtime.hba` delegates to `SourceHBA`. Each writer prepares one complete revision, journals its exact identity, registers authority, applies once, checks parser errors and reload acknowledgment, retires authority, and durably archives completion before clearing the HBA journal. Failure never falls back to a raw write or retries the same writer. Reload acknowledgment remains distinct from activation; existing PostgreSQL sessions are not revoked by an HBA change.

Startup owns worker/effect/operation locks in that order. The supervisor explicitly passes its worker descriptor to the parent-bound installation stage; startup obtains fresh effect/operation descriptions and never unlocks the supervisor's shared description. Standalone source startup and both source cutover callers use the same startup acquisition. Missing or expired startup ownership refuses before source service mutations.

New durable worker receipts carry exact integer `hbaProtocol: 1`. Worker preflight checks its current catalog claim, receipt, preflight marker, inherited locks, target and existing HBA generation before credential writes or native database effects. The services stage separately validates its exact identity before HBA publication. Startup constructor checks occur before credential-file writes and disruptive cutover steps, but directory creation/chmod can happen first.

A pending HBA journal blocks worker startup before receipt recovery. HBA completion does not settle a provisioning job. Unknown later-stage worker outcomes continue to require separate reconciliation.

## Fresh initialization and existing installations

Fresh initialization requires both an absent database/retained volume/pin at preparation and positive evidence that this invocation created the exact subsequently inspected container. Docker inspect failure alone is not absence: a successful resource listing must confirm absence, including full or shortened container IDs. Unknown inspection failures refuse progress.

Existing containers require the exact private generation pin and configured container name, owner, image and ID. Restart reads the matching backend registry without reinitializing it and refuses active leftover authority. Missing or changed state is not repaired automatically. Container recreation requires an explicit generation migration, still unfinished.

The retained source predates this protocol and has not been adopted. Its `up` path intentionally refuses until explicit quiesced adoption is implemented. Stop remains available. Do not delete pins, journals or revocations to force startup. Recovery targets and their raw restore writers have not been migrated to this protocol.

The historical `durable-check.ts` recreation probe now refuses before catalog or Docker operations. Its old destructive container-removal step would invalidate the generation pin. Its recreation requirement remains open; it has not been relabeled as a restart test. Use `fresh-worker-check.py` for the currently supported isolated integration rehearsal.

## Verification

- Full Python suite: 217 tests. New integration checks cover legacy refusal, retained state without its container, changed identities, creation evidence, active backend authority, one-attempt refusal, inspection errors and expired/missing startup ownership.
- Bun suite: 73 tests, 408 assertions.
- [Fresh worker evidence](evidence/fresh-worker-checks.json): 76 live checks using pinned original PostgreSQL/Auth/REST/Storage, real guardian/receipt/SQL/HBA paths, exact startup and worker archives, retired tokens, missing-pin refusal, parent-bound restart under inherited supervisor ownership, unchanged generation, SDK RLS/private-object isolation and exact disposable cleanup.
- The separate 26-check HBA publication/host-death probe and 56-check registry probe remain narrower component evidence. The fresh worker integration does not yet inject death during an actual worker HBA operation.

Independent review found no remaining must-fix in the managed source HBA scope. It identified the uncertain-inspection/freshness issue and entry-point ownership/ordering constraints addressed here. No retained source, recovery target or unrelated Docker resource was changed for this rehearsal.

Next: actual worker/supervisor interruption at HBA publication, explicit legacy adoption and container-generation migration, then recovery-target writer integration. Startup management SQL, service-owned migrations, host power loss and broader provisioning recovery remain separate gates.
