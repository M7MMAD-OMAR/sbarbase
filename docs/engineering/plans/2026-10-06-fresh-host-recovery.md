# SB-05 complete application recovery on a fresh host

The task continues SB-05 after the scoped SB-04 proof. SB-04 artifacts remain historical evidence with their original identities. This plan does not accept complete application recovery before actual native checks on a distinct fresh target host. The coordinator has authorized source work and isolated source tests. Docker, native SQL, HBA mutation, remote host allocation and heavy runtime checks require a coordinated slot. Native feature acceptance depends on SB-13a.

## Recovery boundary

The recovery set is one complete declared installation: all published application runtimes, their control catalog identities and permissions, original Supabase database material, Storage metadata and bytes, settings, service code and required private material. This contract accepts the complete installation only. Environment and Auth user UUIDs remain stable. Target installation, host, daemon, scoped resource OIDs and service credentials are fresh identities.

A distinct target host must consume only the authenticated encrypted set and independently supplied key, pinned public source/images, explicit target bindings and admission records. It cannot access source state directories, source volumes, original private files or expected plaintext fixtures. The orchestrator may retain expected values solely for independent comparison. A fresh cluster on the same daemon is separately classified and cannot satisfy the fresh-host gate.

Application encryption keys and URL signing material must remain usable after restore. Database, API administration, controller session, Storage control/admin and target platform access credentials must rotate before reopening. An application JWT rotation invalidates old API credentials and sessions, while fresh login with the recovered Auth identity must work. Preserved Storage URL signing keys are distinct from rotated platform credentials. Opaque encrypted state whose data key is unavailable refuses before allocation.

Historical recovery material cannot reactivate retired platform credentials or managed API keys. Restored keys are revoked and fresh access material is admitted before reopening. A runtime with transfer history additionally needs independently trusted current ownership, credential generation and runtime epochs, or the admitted original transfer rekey/rebind contract. A historical catalog alone cannot supply that authority. Missing generation authority denies live recovery; scoped historical data checks do not establish post-transfer credential rejection.

Every declared runtime has explicit feature records and scoped material references. Object-store policies identify their runtime and Storage or Iceberg scope. Storage S3 API support can use included file bytes; it does not by itself declare an external backend. An independent external copy must name an exact scoped object-byte member with matching size and digest. Destination placement records map every recovered runtime to its complete independently enrolled resource set. All involved source and destination placements need current canonical native acceptance and the exact per-set observation in their accepted proof logs.

## Reserved paths

New implementation files: `lab/recovery_inventory.py`, `lab/recovery_receipt.py`, `lab/fresh_host_recovery.py`, `lab/fresh-host-recovery-drill.py` and matching `lab/test_recovery_inventory.py`, `lab/test_recovery_receipt.py`, `lab/test_fresh_host_recovery.py`, `lab/test_fresh_host_recovery_drill.py`.

New documentation/evidence: this plan, `docs/engineering/reviews/2026-10-06-fresh-host-recovery.md`, and `docs/evidence/fresh-host-recovery-2026-10-06.json`.

Existing backup, SB-04 implementation/evidence, management authentication, native startup and private SQL/HBA authority paths stay reserved to their existing owners. An adapter must call an admitted existing authority interface; this task cannot issue competing native SQL/HBA effects. Any required integration edit is coordinated before changing its path.

## Requirements and evidence

| ID | Required result | Discriminating acceptance |
| --- | --- | --- |
| SB05-IDENTITY | Bind source application/catalog identity, epoch, placement and physical resource inventory separately from target identity | Changed UUID, epoch, placement, source resource identity or target identity invalidates receipt; missing identity denies |
| SB05-INVENTORY | Capture complete declared database, schemas, extension versions, roles, memberships, defaults, ACLs, RLS, settings, catalog identities and permissions | Unknown state cannot disappear; missing required material refuses; native target metadata and permission behavior match |
| SB05-ENCRYPTION | Stream authenticated recovery material with a separately supplied independent key and exact member digests | Wrong key, altered ciphertext, truncation, duplicate/traversal/symlink members, missing material or changed set refuse before native allocation; no plaintext mutation before authentication |
| SB05-AUTH | Recover original native Auth users, identities, factors and account settings | Original password login and identity UUID survive; recovered MFA remains functional where enabled; anonymous and neighboring actors retain their denial boundaries |
| SB05-STORAGE | Recover original native object metadata, bytes, versions, MIME/cache attributes and URL signing state | End-user RLS downloads and uploads work; unauthorized access is denied; a signed URL issued before export works for its remaining lifetime |
| SB05-ROTATION | Rotate access credentials while retaining or explicitly rewrapping data keys | Old DB, platform administration and application JWT credentials fail against the target; fresh credentials work; Vault and encrypted tenant signing material remain decryptable |
| SB05-FEATURES | Inventory every enabled native capability and its required material | Realtime, Functions code/secrets, Vault, cron, queues, webhooks, subscriptions, S3, TUS, Iceberg and any new enabled capability require an adapter and source-bound native acceptance; omission or unknown capability denies completion |
| SB05-EXTERNAL | Declare and enforce external object-store recovery and external effects policy | File bytes are included; external stores need an immutable version/digest inventory and either an authenticated independent copy or an explicitly admitted immutable external dependency. Missing credentials, writable unbound addresses or missing versions deny completeness; no source store is modified |
| SB05-RESTORE | Restore into an empty independently owned target through durable phases, with writes and external effects disabled until admitted | Reopening before material, permissions, key rotation and native verification is impossible; interrupted restore reconciles exact identities, preserves verified writes and never silently replays a completed phase |
| SB05-FRESH-HOST | Exercise application recovery after loss or exclusion of source installation state | Distinct target host/daemon identity, no source mounts/state access, encrypted transport plus independent key, original Auth/REST/Storage HTTP checks and enabled native features all pass |
| SB05-ADMISSION | Publish an exact source-bound recovery receipt for retention consumption | Canonical recovery-set digest, exact source and destination resources, complete verification vector and current SB-13a capability acceptance are mandatory. Source-only tests or scoped historical artifacts cannot mint full recovery admission |

## Checkpoints

1. Establish strict inventory and receipt contracts. Pure import-safe validation, deterministic recovery-set hashing, explicit source/target identities, material classification and external-store policy. Isolated source tests cover mutation, omission, stale native evidence and refusal before effect callbacks. Two fresh independent reviews assess specification and operational safety.
2. Build encrypted package capture and authentication. Reuse the existing authenticated streaming encryption primitive where suitable, with a recovery-specific authenticated context and strict member/schema admission. SQL dumps and native materials are obtained only through coordinated adapters. Authenticate the entire encrypted input before exposing any restore effect. Unit tests cover authentication failure, archive attacks, partial publication, memory bounds and secret-free evidence.
3. Build target planning and durable recovery state. Empty-target inventory is mandatory. Provisioning, import, data-key restoration, re-encryption, credential rotation, address binding, stopped external effects, native verification and reopening are separate durable phases. Existing private SQL/HBA owners supply the effect interface. Unit fixtures discriminate invalid ordering, changed OIDs/CIDs, retries, interruption and reopened writes.
4. Wire the native fresh-host drill only after SB-13a supplies current original Supabase capability admission. Create owned source application data through actual APIs, issue the signed URL, export, transport authenticated material, remove or make source private state inaccessible, restore on the distinct target and run real Auth/REST/Storage and enabled-feature checks. Resource limits, target host, transport, authority leases and execution timeout are planned with the coordinator before running. Two fresh runtime reviews inspect actual bound output; source checks alone cannot accept this checkpoint.
5. Integrate retention admission and final evidence. Publish only after the complete current verification vector passes. Retention remains default-deny until full SB-05 is accepted. The receipt is evidence, not a purge command or permission to remove unrelated resources.

## Native prerequisites and unresolved external choices

The coordinator must supply the SB-13a original-native capability admission schema, an independently owned fresh target host, exact runtime resource slot limits and the admitted private SQL/HBA effect interface. No availability is inferred from a process name, reference tag, Docker label or earlier fixture pass.

The external-store adapter supports explicit policy records. Until an immutable external dependency or independent authenticated object copy is actually verified, that store remains an unmet recovery requirement. External webhook, SMTP, OAuth, subscription and job destinations remain disabled until explicit target rebinding and admission. Source-only work continues while these native prerequisites are pending.

The frozen reference is Supabase `self-hosted/v0.8.2`, commit `564eab8ad7840b13324f68b1bfac074ef8d51c21`. Pinned repository lock files identify candidate service images. Official [self-hosting configuration](https://supabase.com/docs/guides/self-hosting/docker) and [Storage configuration](https://supabase.com/docs/guides/self-hosting/storage/config) inform material classification; they do not replace execution against the pinned images.
