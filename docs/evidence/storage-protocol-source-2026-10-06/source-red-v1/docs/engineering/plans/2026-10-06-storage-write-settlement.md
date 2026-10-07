# Original Storage writer settlement

Status: authorized source preparation. Original native settlement, installed
authority, shared migration, consumer integration and production remain unproven.
This dependency serves transfer, purge, coherent backup and complete recovery.
Missing proof denial is an interim safety boundary, not the requested outcome.

## Baseline and rules

Work in the isolated checkout from `2c4ec82007a47436ba3e338ac03d1ad5e288df45`.
Imported baseline commit `e626baf` is excluded from delivery. The coordinating
snapshot matched patch SHA256
`3b1dc3f488d20921250862e6e01329cec5d42caf5d50c7425d564fdc2546ec04`
and archive SHA256
`06ff4dc0986be11a5487b5116e5246cd66699a8360e9d7f41b3d2e8ccdfa1d18`.
All 60 ZIP members were relative, unique, regular, mode 0644 or 0755 and matched
the declared inventory. The imported `sbarbase-public-source-v1` had 537 files
and digest `a467711e1024531eb3c26ef2b7b99717313e18b1b59bf63420f646af353e4d2e`.

Read [PROJECT_GOAL](../../../PROJECT_GOAL.md), the full
[product plan](2026-10-03-product-and-portability-plan.md) and
[integration programme](2026-10-06-integration-programme.md). Preserve original
images, native metadata, ETags and features. No long dashes in authored material.
The missing home AGENTS.md was disclosed. The available .codex rules apply.
The initially absent graft graph was built deterministically without deep/LLM
calls; it remains an ignored local source cache, not a shipped dependency.

No Docker build, run, pull, service, database server, VM or retained installation
operation is authorized in this source phase. Disposable source tests may use
private temporary files and their own subprocesses. Existing Catalog, HTTP,
gateway, runtime, lifecycle, backup, restore, installer, verifier, private SQL
and HBA paths remain reserved for their coordinating owners.

## Required checkpoints

| ID | Required result | Evidence and completion boundary |
|---|---|---|
| SS1 | Pin and map actual original writer lifetime | Exact Git blobs and source spans, release-to-commit link; installed image provenance remains separate |
| SS2 | Durable exact inventory, source protocol and strict receipt codec | Python disk-journal tests, ordered Catalog digest cross-language cases, Bun tests, two fresh independent critics; source preparation only |
| SS3 | Original native settlement fixture | Actual original SDK/file/SQL/process observations for every enabled writer class, interruption and lost acknowledgement, owned cleanup; no native result yet |
| SS4 | Installed launch and publication authority with consumers | All real launch/restart paths fenced across crash/restart, current Catalog CAS at final mutation, installed authority plus source-stable regression; reserved paths not changed |
| SS5 | Shared all-tenant migration and general profiles | Complete neighbor/config/data/native-feature preservation, coherent restore, all enabled remote/queue/backend effects, original native admission and full merged verification |

The checkpoint engine run is stored outside source scopes. Delegated acceptance
is authorized for SS1/SS2 preparation. It cannot accept SS3 through SS5 from
fixture tests. Requirement coverage and pending gates must remain visible.

## Source contract and consumers

The pinned writer map is the [source evidence](../reviews/2026-10-06-storage-writer-source-map.md).
Use [storage_write_settlement.py](../../../lab/storage_write_settlement.py) for
the durable source protocol and
[storage-settlement-contract.ts](../../../src/control/storage-settlement-contract.ts)
for strict control-side source receipt comparisons. Neither installs a native
verifier. No native effect may use the source comparison result as admission.

The common wire receipt is version 1, contract `storage-write-settlement-v1`.
It binds the seven-field Catalog binding, operation UUID, purpose, initiating
actor and management epoch, installation UUID, exact daemon digest, namespace,
placement generation, routing revision, original writer manifest digest,
reconciliation digest, current journal digest/sequence and fence incarnation.
The fixture protocol always emits evidence `fixture`. The native entry point
refuses without invoking caller callbacks. A forged `native` JSON value cannot
install native authority or turn a marker into proof.

The Catalog binding stays ordered as `environment,runtime,epoch,coverage,
placement,inventoryDigest,placementDigest`. Inventory and placement digests use
the existing UTF-8 `JSON.stringify` insertion order, not sorted keys. Manifest
and journal checksums use a separately versioned sorted-key JSON codec. Input
is detached from caller mutation. Duplicate journal JSON keys, unsupported fields,
nonfinite/noninteger numbers, stale operation/generation/epoch and unknown
coverage refuse. Fixture/null coverage cannot authorize production.

The exact manifest additionally binds original Storage image and source,
effective configuration hashes, immutable process CIDs and start identities,
file volume/resource/root/device/inode/mount identity within the exact namespace,
tenant prefix, original database CID/image/name/OID, every writer and launch
path, enabled feature inventory, protected neighbor digests and exact source
resources. A selected-tenant manifest cannot include a global/shared writer.
Mount IDs must be obtained in their actual namespace; they are not portable
identities across restart or different namespaces.

Consumers still own their immutable prior/current inventory generation CAS,
signing grant generation/retirement, ownership and credential policy. SB06's
mutation binds `sourceGeneration`, `sourceEpoch`, `heldEpoch=sourceEpoch+1`,
`previousDigest`, `sourcePlacementDigest` and `heldPlacementDigest`, separately
from runtime/placement generations. Its verifier additionally binds the exact
replacement `candidateDigest`, `candidatePlacementDigest`, coverage and native
`proofDigest`. Require original old signed upload/download denial and new grants
working at the same operation, signing generation and protected neighbor set.
The source receipt cannot mint TransferInventoryStore publication capability.
SB06 must revalidate after awaits and inside its final Catalog transaction. SB07 requires
the installed verifier before enrollment/migration and each physical effect.
SB05 requires a receipt for every declared runtime under one installation-wide
fence. It separately binds every live exported snapshot holder to the original
DB CID, database/OID, backend PID/start/transaction identity and owned transport
process. A lost holder or changed fence refuses package publication.

## Durable effect and reconciliation policy

Persist `enrolled`, then `fence-pending` before fencing ingress and every launch
and recreation path. Reobserve the exact current authority after each effect.
Persist `stop-pending` before stopping all exact writers. Process exit is required
for local file writes; gateway permits, abort signals, role/session closure and
maintenance epochs alone do not supply it. Docker restart policy is only one
launch path. Reconcile database sessions, prepared transactions, queue state,
TUS metadata/parts/locks, multipart rows/parts, deferred cleanup and native
row-to-file state while the persistent fence remains held.

Preserve native bytes, UUID versions, xattrs and nanosecond mtime. Capture a
strong digest only after writer closure. A native mtime ETag cannot certify
content by itself. Same-version interrupted writes need their original native
completion/recovery evidence; equal size, stat stability or a marker cannot
approve them. Keep uncertain/orphan/partial versions quarantined. Do not edit
native rows or change native ETag configuration to make a fixture pass.

Persist reconciliation before `settled`, then reobserve the complete authority.
Every restart and every receipt consumption reobserves the live fence and exact
writer universe. Lost stop acknowledgement can settle only after new exact
observations. Before a consumer effect persist `effect-pending`; interruption
or lost acknowledgement retains it for explicit original-native reconciliation.
Automatic replay is forbidden. Hash-linked private records and fsync detect
accidental corruption and interruption, not a malicious administrator with
full write access. Incomplete/corrupt logs never reopen publication.

Enabled queues/Redis need durable generation-bound quarantine and native state
inventory before replay. A remote S3 operation can finish after local process
exit, so v1 refuses even a fixture remote status. Provider barriers, exact
versions/multipart parts, pending requests and recovery policies need a real
adapter plus actual evidence. These features remain required SS5 scope.
The `s3_protocol` ingress and `remote_s3` backend flags are separate. File-backed
S3 protocol writes use the original local writer inventory and multipart policy;
they do not require a remote provider merely because the S3 API is enabled.
This distinction follows the [upstream S3 configuration](https://supabase.com/docs/guides/self-hosting/self-hosted-s3).

## Shared placement migration

Existing shared Storage has no selected-tenant settlement guarantee. Do not
kill a shared process or adopt selected stopped CIDs to purge one environment.
Migration is a separate installation operation with a complete initial tenant,
native configuration, key, database, object, feature and neighbor inventory.
Pause every ingress and persist a restart/recreation fence for the entire writer
universe. Reobserve all current generations, then stop/settle every writer and
reconcile every tenant before taking any coherent copy.

Copy into distinct original-image processes and exclusive volumes. Preserve all
native tenant configuration, encrypted secret references, UUID paths, bytes,
xattrs and exact mtime, plus enabled native features and queued recovery policy.
Prove destination and neighbor consistency with post-copy native SDK reads and
strong file manifests before publication. Journal each source-to-destination
identity/generation. Publication is a current-generation CAS, never an inferred
success from copied files. Interruption retains recoverable source volumes and
the source fence until every destination is genuinely checked. Reopening source
or destination requires explicit one-writer reconciliation. This design is
pending implementation and runtime proof, not accepted migration functionality.

## Native role requested after source freeze

The drill plan and original SDK fixture live in
[disposable-storage-settlement-drill.py](../../../lab/disposable-storage-settlement-drill.py).
Before allocation, freeze exact source/image/config identities, test IDs,
operation UUID, owner labels and predetermined exact network/container/volume
names in an ownership ledger. Inspect absence and host-wide neighbor IDs before
allocation. Persist planned resources before creation; exact owned cleanup must
be independent of success and classify unknown Docker errors as unresolved.

Candidate local phase: private internal network, no published ports, original
PostgreSQL 256 MiB/0.25 CPU and original Storage 256 MiB/0.25 CPU. Auth bootstrap
runs sequentially with Storage stopped. At most two containers and 512 MiB/0.5
CPU run at once. A separate multi-writer phase needs DB plus two Storage workers,
768 MiB/0.75 CPU, explicit separate admission and no untracked daemon/service.
Redis, queue and remote S3 phases need separately frozen original dependencies,
budgets and actual provider authority; their missing execution blocks general
acceptance. Bound each command and role duration, cap pids/logs and retain all
negative observations. No heavy role is assigned yet.

Required actual cases include native new upload, overwrite, delete, signed and
ordinary SDK access, large streaming disconnect, TUS same-version resume and
cancellation, multipart assembly/cancellation, lost stop acknowledgement,
interrupted files/metadata, process/controller restart, changed generation,
multiple writers, queue/Redis deferred effects, enabled remote/backend effects,
neighbor uploads during selected operations and restore/new writes after
reopening. Each must include exact before/after file/DB/process observations and
original native responses. Source tests or synthetic statuses cannot count.

Current native-dedicated routing explicitly refuses admission in placement.ts
and gateway/managed.ts. Their DB identity plus Storage URL/tenantHost lacks the
Storage writer/process/volume authority. The native owner and coordinator agree
to keep that refusal until SS3/SS4 and the wider native placement prerequisites
pass. Full goal and production acceptance remain open.

Public source reproduction, with explicit evidence location supplied by the
operator and no vendor service execution:

```sh
python3 -m unittest discover -s lab -p 'test*storage*settlement*.py' -v
bun test tests/storage-settlement-contract.test.ts
python3 lab/disposable-storage-settlement-drill.py --source-control-check --evidence /absolute/owned/evidence/bun.log
python3 lab/disposable-storage-settlement-drill.py --plan
python3 lab/disposable-storage-settlement-drill.py --sdk-fixture
```

The native SDK source presently prepares ordinary/signed upload/download,
overwrite and deletion probes. The remaining actual native case harnesses and
installed authority are pending SS3/SS4 implementation and assigned runtime
roles. A plan with every test ID is not an executable complete native suite.


## Authorized native source follow-up

The coordinator verified the first delta and authorized source changes only in
this drill, its test, this plan and new exclusive `lab/storage_native_authority.py`
and `lab/test_storage_native_authority.py`. Original-native executions, Docker,
services, VMs and retained resources remain unassigned. The earlier SS1/SS2
acceptance applies to commit `75660a82777689080cd441cc583d6e2522c26100`, not later
source bytes. A new independently reviewed source checkpoint must bind this
follow-up. Every one of the 29 native case IDs needs its own executable logic
or explicit implementation-pending entry. Unavailable real dependencies and
unimplemented logic are separate states; neither counts as a successful case.

Source checks will freeze exact commands before execution. Each check is at
most 120 seconds, Python test address space at most 256 MiB, Bun has its own
declared address-space and aggregate resident-memory ceilings, and the combined
source-test role at most 180 active seconds and 16 MiB output. Only owned private
files and bounded test processes are permitted. Original SDK syntax/protocol
negative units and cross-language checks remain source fixtures, not original
service observations. Separate source test phases avoid inheriting Python's
small address-space ceiling in Bun. Queue, Redis and remote-provider cases need
separately admitted real dependency phases and provider authority.

### Proposed minimal coordinated Runtime integration

This is a proposal against actual source; no shared Runtime path was edited.
The complete `Runtime.launch` caller graph includes start, activate_services,
reconcile_auth, reconcile_mail, realtime_start, functions_start and management,
plus provision, resume, rotate_signing, functions_turn, realtime_turn and
resume_published_environments. Guarding a gateway or one visible start command
does not cover this graph.

1. In `lab/durable_runtime.py` Runtime.launch, currently lines 378 through 426,
   acquire the installed installation-wide launch/effect guard before inspect,
   replacement removal, start, volume creation or run. Bind each exact daemon,
   namespace, container/image/config/mount/resource, operation, generation,
   management epoch and source inventory. Keep the guard across the mutation,
   durably record pending intent first and reobserve immutable current identity
   after any external command. Default refusal must survive process restart,
   lost acknowledgement, direct launch callers and container recreation.
2. In Runtime.start, currently lines 448 through 490, check installation-wide
   maintenance authority before any shared network/database/management or
   original Storage start, and before publishing database.json or resuming
   published environments. One shared multi-tenant Storage container requires
   complete all-tenant coverage and preserved neighbors. A selected-tenant
   manifest cannot stop that shared writer.
3. In Runtime.activate_services, currently lines 589 through 650, hold the same
   persistent publication guard around original Storage tenant POST/PATCH and
   endpoint publication. Revalidate after HTTP/readiness/SQL operations and
   immediately before atomic endpoints.json replacement. Compare authoritative
   current operation, generation, epoch, routing revision and complete ordered
   inventory inside the final publication critical section. Preserve original
   native tenant secrets/config, routes and new-working/stale-denied grant
   semantics. Auth/Rest/realtime/functions callers must not indirectly reopen
   publication during a held Storage generation.

The coordinator should own this shared-file delta and the installed guard
issuer/consumer binding. Proposed API obligations are an installed guard's
exclusive acquisition, before-effect durable intent, immutable reobservation
and final same-authority publication check. The new source authority has no
installed issuer or publication integration and cannot supply a native receipt
or mint admission. Existing source receipt/default refusal remains in force.
Installer/systemd/worker restarts and Catalog publication outside this Runtime
module also require the same installed guard; closing these three spans alone
cannot satisfy SS4 or SS5. Backup snapshot holders and all tenant migration
remain separate coordinated requirements.


The follow-up source matrix declares 14 helpers, not 14 completed native cases:
nine original SDK/HTTP helpers and five local controller/observation helpers.
Fifteen IDs retain explicit pending implementation. Actual lost-stop-ack delivery
and installed generation mutation remain explicit pending stages even where a
recovery or refusal oracle exists. Local commands are gated before subprocess
by missing installed authority. Shared coverage, credential fencing, compiled
provenance and every enabled queue/TUS/multipart/Redis/provider barrier remain
unproven. Source row/file comparisons never admit native settlement.

The initial frozen follow-up source profile separates 128 planned pure Python cases,
three Python/Bun source cases, and 78 Bun contract cases. These are planned
counts until the commands execute. Python allocation/address-space soft limit
is 256 MiB; pure Python test processes also use that hard limit. The hybrid
phase reserves a larger hard ceiling only so its fixed child setup can set Bun's
separate limit immediately before exec. No Python code allocates above its
256 MiB soft limit. Bun's virtual address-space ceiling is 8 TiB; aggregate
resident memory of each owned test process group is capped at 768 MiB by the
supervisor. Source role active time is 180 seconds, command time 120 seconds and
combined captured output 16 MiB. The supervisor binds exact helper/source bytes,
serializes its cumulative budget and kills only its own process group on a bound
failure. Profile scripts and actual logs are evidence artifacts, not production
or portable source dependencies. Normal project reproduction still uses Python
unittest and Bun commands.


The first bounded source attempts failed during import, before any test ran.
Own-process mapping inspection identified glibc locale-archive as a 233381888
byte virtual mapping. Changing allocator/JIT settings did not resolve it; these
failed observations remain retained. The final source commands explicitly use
`LC_ALL=C` and `PYTHONUTF8=1`, with the existing bundled Python interpreter.
The 256 MiB limit is unchanged. This locale choice affects only source check
processes and does not rewrite original service configuration or object metadata.


Implementer inspection found two additional reconciliation obligations. Two new regression tests failed on the
earlier bytes: direct reconciliation accepted running writers or changed
stopped observations. Those failures remain historical evidence. The repair requires the ledger lock, exact completed observed-stop
ownership, detached validated stopped observations and mandatory database
container/config/image/start/process identity before and after file/SQL capture.
The repaired profile plans 144 pure Python cases, three Python/Bun cases and
78 Bun cases. These become verified counts only after bounded execution. Two
new fresh critics must review the repaired source. Native admission remains
false and the installed execution guard still refuses before subprocess.


Signed SDK helpers require both standard and signed ingress prerequisites;
disabling either leaves the signed cases unavailable. Complete unique original
writer observations preserve enrolled manifest order even when the daemon
inventory is inspected by sorted CID. Private source regressions exercise these
feature and multi-writer ordering obligations without original service effects.


The bounded source role also requires two real private-process supervisor
regressions: a reduced retained-output ceiling and a child that closes both
output pipes before termination. These are supervisor-control checks, never
original Storage writer evidence. Their commands and helper/source bindings
must be frozen before execution and their elapsed time/output charged to the
same role budget. Native admission stays false.


The supervisor must continue monitoring until both process termination and
pipe closure, clip retained bytes to the remaining role allowance, and always
terminate/reap its owned process group and persist elapsed/output accounting
on exceptions or interruption. The output budget includes a conservative
reservation for structured supervisor metadata. Closed pipes do not establish
process exit. Reduced-policy private probes exercise these invariants.
