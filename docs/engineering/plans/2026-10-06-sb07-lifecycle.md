# SB-07 retained deletion and audited resource lifecycle

Contract: `SB-07/lifecycle/v1`, frozen 2026-10-06 before actual verification.
This work preserves original Supabase services. Complete SB-05 recovery remains
a prerequisite for final application purge, and currently blocks its admission.
No component fixture or archived historical proof closes that prerequisite.

## Required behavior

1. An organization owner retires an environment. The catalog retains its project,
   ownership, placement, settings and runtime identity for seven days. The response
   states the absolute retention deadline. Existing keys, management sessions,
   runtime authorization and in-flight workspace requests lose their epoch.
2. Retained environments remain visible to authorized owners and count toward
   installation capacity until their resources have actually been reclaimed.
   Active services or unsettled provisioning refuse retirement. A durable intent
   quarantines positively owned resources without releasing their allocation.
3. An owner requests restoration before the retention deadline. Restoration and
   purge exclude each other through a durable operation UUID and epoch. Resource
   health is observed before readiness returns. Missing resources, stale placement,
   unresolved effects or absent health contracts leave a visible blocker.
4. Elapsed retention makes an environment eligible for an explicit purge request;
   it does not schedule automatic destructive work. Purge also needs installation
   authority, current organization authority, a complete immutable inventory and
   a trusted recovery receipt for the same environment, runtime, epoch, placement,
   inventory and recovery-set material.
5. The production recovery verifier checks current complete SB-05 acceptance and
   hashes of exact recovery material. Request bodies cannot provide or override
   admission. Archive verification alone, a completed backup or a readiness-pending
   restore record cannot authorize deletion.
6. Every external effect has a persistent pending receipt before mutation and an
   observed postcondition after mutation. The worker uses installation locks.
   Replaying an interrupted operation reconciles its exact resource identities,
   preserves measured pre-effect bytes and never treats inspection transport errors
   as absence. Unknown outcomes remain visible for operator reconciliation.

## Resource ownership

Container enrollment pins a full CID, installation UUID, environment runtime,
resource UUID and environment-only role labels. Volume enrollment additionally
pins its creation identity and rejects shared or foreign mounts. Restore resources
and retained recovery targets are outside the deletion inventory. No prefix
cleanup, daemon prune, implicit resource discovery or production adoption is used.

Filesystem enrollment pins device and inode below a configured owned root and a
matching resource ownership marker. All path components, payload links, hardlinks,
mount crossings and marker collisions are checked. Directory removal first moves
that exact inode to a deterministic graveyard entry, preserving its ownership
marker until payload removal has settled. Reconciliation handles interruption
before and after marker unlink.
The owned root and every opened directory, marker and regular file must share the
same Linux FD mount ID. Device equality alone cannot admit a bind mount. Rename,
graveyard inspection and removal use pinned parent directory descriptors, with an
exclusive rename that refuses an existing graveyard entry.

Recovery admission delegates its receipt codec and recovery-set digest to
SB-05's `recovery_receipt.verify_purge_receipt`. The installed reader supplies the
exact current Catalog inventory and `RegistryAcceptance` trusted checkout locator
at admission and each effect revalidation. Missing shared modules or unaccepted
current SB-05 and SB-13a proofs refuse production purge. Receipt files are bounded,
private, unlinked regular files read through directory descriptors without following
symlinks. Explicit publication checks Catalog again and atomically refuses an
existing receipt target. No HTTP field provides a receipt or acceptance claim.

Shared placement cannot reclaim its installation database or objects volume.
Its explicit ownership migration must bind the engine CID and owner, database OID,
role OIDs and dependencies, tenant row identity, Storage database OID, admitted file
tree and all writer CIDs. Global `anon`, `authenticated` and `service_role` roles
remain shared. Deletion fences the exact environment database and drains writers
before removing environment-specific database, role, tenant and file resources.
The original shared Storage process has no admitted tenant writer settlement
boundary. Shared ownership enrollment, migration and low-level SQL lifecycle
effects therefore refuse before mutation. This is an interim safety boundary,
not completion of the general shared lifecycle requirement. Native, transfer and
recovery owners must establish complete writer, ingress and file mount ownership
and neighbor-safe drain evidence before that placement can be admitted. No stopped
CID list or operator assertion supplies this proof.

## Verification and delivery

All tests execute in the coordinated bounded public verifier after the backup,
MFA CLI and FIFO roles. No host tests, existing user resources, ports or data may
be used. Actual deletion fixtures allocate fresh UUID resources with exact labels
and CIDs, and cleanup verifies absence of only those recorded resources.

Required evidence includes owner/admin/viewer and cross-environment denial,
retention deadlines, epoch revocation, retained capacity, restore health refusal,
complete-recovery denial, identity collisions, concurrent restore/purge exclusion,
duplicate operation replay, interruption before and after durable effects, measured
reclaimed bytes and unchanged neighbor hashes. Component tests supplement actual
effects and do not replace them. A fresh independent critic inspects final source
and actual artifacts and returns `SPEC MET` or `SPEC NOT MET`.

The prepared Docker drill uses the same two fresh target and neighbor containers
and volumes throughout. It kills the worker before and after container effects
and after volume removal, reopens its SQLite intent in a fresh process, and checks
preserved measurements and final receipts. The outer verifier records allocations
before creation and independently reconciles those exact identities on timeout.
This measures process interruption; physical host crash and shared SQL runtime
acceptance still require their own evidence.

The runner fsyncs its exact image tag and each outer verifier allocation name
before requesting creation. Uncertain creation resolves only that exact name and
checks its full CID, image, run and stage labels, command, socket mount and resource
bounds. Creation uses the immutable image ID, and the runner inspects those same
identities and bounds before requesting execution. Cleanup first stops the owner
and verifies a successful evidence copy.
Possible helper allocation requires the complete recorded ledger and exact helper
and volume reconciliation before owner removal. Missing or unreadable evidence
fails the run and preserves the stopped owner and its image for reconciliation.
The runner never silently skips a missing helper ledger or deletes its only copy.
An absent owner after requested execution still requires its exact copied helper
ledger and reconciliation. Without that evidence, the run fails and preserves the
image and unresolved allocation journal.

The proposed same-device bind-mount negative has this explicit fixture contract:

- It reuses the same two fresh helper containers and two fresh local volumes.
  The neighbor helper mounts its own fresh volume at `/payload` and mounts that
  same volume once more at `/payload/<exact-runtime>/<exact-resource-UUID>/mounted`.
- Both mount sources are the one recorded, exactly labeled fresh volume. No host
  directory, existing volume, additional helper, privileged mode or added capability
  is introduced. Network, published ports, memory, CPU and PID bounds stay fixed.
- A known Python command from the pinned baked verifier image executes inside that
  exact helper. It records equal devices and unequal FD mount IDs, requests purge
  of its marked disposable directory, and requires refusal before payload changes.
- The allocation ledger records the exact mount declaration before helper creation.
  Evidence records helper CID, volume, directory UUID, observed mount IDs and payload
  hashes. Container removal releases both aliases before exact volume cleanup.
- This contract and source must receive independent review and explicit coordinated
  resource-role assignment before execution. No runtime permission is inferred.

The import baseline is commit `b11613227d00dea3784bd725701303eaea6baed6` above
`2c4ec82007a47436ba3e338ac03d1ad5e288df45`. Delivery is a separate delta commit.
Dependency commit `409536e` imports only the frozen canonical SB-05 inventory and
receipt modules from `d809eb5dfd6a5a83c0369709a37e1cb641d388b5`, with their committed
bytes and modes verified. This imports the consumer API, not source or native
acceptance into this checkout. Current registry refusal remains authoritative.
Root integration owns the final intentional `catalog-schema` registry binding
update. All capability acceptance and production rows stay unproven unless their
own current complete contract is independently established.

The low-level Docker effect boundary uses the public
[Docker Engine API](https://docs.docker.com/reference/api/engine/). Supabase's
[Docker guide](https://supabase.com/docs/guides/self-hosting/docker) documents whole
installation reset; it does not authorize per-environment shared-resource purge.
