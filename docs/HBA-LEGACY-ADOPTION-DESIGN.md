# Legacy source HBA adoption requirements

Design checkpoint, 2026-09-20. Not implemented or executed. The retained legacy source remains stopped and lacks a generation pin; ordinary startup intentionally refuses.

## Operational boundary

Adopting the same container ID requires an explicit operational assumption that old host clients and queued Docker requests have been quiesced. Fresh host locks and stopped containers alone do not prove this. Republishing unchanged HBA rules with a fresh revision invalidates old prepared CAS requests whose expected hash is stale, but does not fence raw writers or a delayed client preparing a new write afterward. Do not describe revision rotation as complete legacy revocation.

A stronger container/daemon boundary needs separate design and impact assessment. Do not restart the shared Docker daemon or modify unrelated workloads as an implicit migration step.

## Required durable operation

Before first starting PostgreSQL, exclusively publish and fsync a private adoption intent containing the adoption UUID, intended generation UUID, exact source CID/name/owner/image, pgdata volume and mount identity, and initially stopped source inventory. An existing or partial intent blocks ordinary startup and repeated adoption. It is not permission to generate a replacement identity.

Under fresh worker/effect/operation ownership, refuse pending worker/HBA effects, preexisting conflicting pins or backend markers. Start only the captured source database, preserving application services and recovery targets. Preserve the actual current HBA rules rather than regenerating them from inventory.

Durable checkpoints must distinguish database start observation, generation initialization, owned HBA completion and final exact-container stopped observation. Initialization uncertainty must inspect the same operation/generation rather than repeat INIT. An HBA journal might not yet exist when the adopter dies, so the adoption intent needs its own recovery path.

Cleanup stops only the captured and reverified database. A failed or uncertain stop leaves adoption incomplete. Keep the adoption record until both the exact owned HBA completion and stopped state are verified. Restarting PostgreSQL terminates its sessions and reloads configuration; do not claim unchanged session behavior.

The existing low-level helpers do not yet provide this operation. First implement and crash-test it against disposable legacy fixtures. Same-CID adoption does not implement migration to a recreated container or cover recovery-target writers.
