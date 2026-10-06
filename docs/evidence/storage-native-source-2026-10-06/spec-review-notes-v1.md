# Independent specification review

Reviewer: native-source-spec-critic-v1. Axis: specification. Review concerns frozen original native harness and local authority source only. No source edits or original service effects were performed.

The before-review hosts.py review-state output and the saved after-review output both returned revision 20, plan hash 9af5054961109ad1f0cc09655b44d97929bb7c33093d071dcf9569aaec552d03 and source hash 5f7e8c518ad156f3a76f605861ced7130a52bbfd12c6e3e66ce5d0bd719043cd. Pinned upstream git rev-parse HEAD returned a88e8eea60b69ee629f99f17b21cdb6578a2272a.

## NS1 case coverage

All rows retain missing runtime role, installed provenance, operation/generation/epoch, before-effect ownership, installed launch/ingress/publication fence, original row/file observer, private bucket, bounded request deadline and SDK pin prerequisites. Supplying prerequisite labels changes source status to runtime-unrun, never native acceptance. Disabled features retain their IDs and an enabled-feature prerequisite.

| Case | Actual source stage or explicit pending logic |
|---|---|
| sdk-new-upload | SDK upload followed by downloaded byte SHA256 comparison |
| sdk-overwrite | SDK upsert with changed equal-length bytes followed by downloaded SHA256 comparison |
| sdk-delete | SDK remove followed by independently checked 404 download outcomes |
| sdk-signed-upload | SDK signed token upload followed by downloaded byte comparison |
| sdk-signed-download | SDK signed URL consumed through supplied original transport and byte comparison |
| sdk-exact-byte-download | Ordinary downloaded byte comparison |
| large-stream-upload | Bounded 8 MiB stream, consumed-byte check and full download SHA256 comparison |
| disconnect-before-body-end | Pending bounded raw original HTTP disconnect and partial file/row observer |
| disconnect-after-file-before-db | Pending actual transaction holder and observed file-before-row barrier |
| all-original-processes-ended | Concrete LocalNativeAuthority.stop then all writer exit/PID observations; guard currently refuses |
| lost-stop-acknowledgement | Concrete recover_stop oracle; actual delivered stop with lost durable acknowledgement driver remains pending |
| controller-interruption | Pending actual controller kill after durable intent and exact ledger recovery |
| controller-restart-fence | Pending persistent installed launch fence across controller restart |
| storage-recreation-fence | Pending installed daemon guard against replacement CIDs |
| same-version-tus-resume | Original POST, base64url bucket/key/version location validation, partial PATCH, HEAD offset/length, second PATCH and download SHA256 |
| same-version-tus-cancel | Original POST, partial PATCH, HEAD offset/length, DELETE, missing HEAD and object 404 |
| multipart-parts | Pending pinned original S3 signing/create/upload/list/complete and part/file/row audit |
| multipart-assembly-interruption | Pending actual original assembly barrier and exit/reconciliation observer |
| metadata-file-reconciliation | Concrete ended-writer checks around LocalNativeAuthority.reconcile; guard currently refuses |
| changed-generation-refusal | Concrete exact generation-mismatch refusal oracle; installed generation mutation driver remains pending; generic missing guard cannot succeed |
| multiple-writer-processes | Concrete observation requires at least two live writers; separately admitted original multiple-writer launches remain missing |
| neighbor-upload-preserved | Pending concurrent original neighbor upload during selected fencing |
| queue-pending-active-retry-quarantine | Pending separately bounded original queue workers, DB jobs and retries quarantine |
| redis-lock-and-metadata-quarantine | Pending separately bounded original Redis dependency and observer |
| remote-provider-inflight-settlement | Pending separately bounded actual provider operation settlement |
| remote-version-and-part-reconciliation | Pending actual provider version/part ownership and reconciliation |
| coherent-backup-and-restore | Pending original snapshot holders, stopped writers and actual consumer restore |
| sdk-new-write-after-recovery | Pending installed restore/publication authority followed by new SDK roundtrip |
| exact-owned-cleanup | Pending exact actual cleanup ledger plus resource absence and neighbor observations |

There are 14 limited helpers and 15 implementation-pending entries. The local recovery and changed-generation helpers do not produce their actual fault conditions. All 29 cases remain native-unrun. The fixture unit transports do not establish original SDK or service behavior.

## NS2 source mechanics

validate_spec accepts only file-backed dedicated resources and a closed detached manifest. decode_inventory binds operation, generation, management epoch, daemon, namespace and volume identity; exact before/after full daemon inventory; container CIDs, image config ID associated with the registry digest, effective Config/HostConfig/Mounts digest, start time, restart state and owner/runtime/operation labels; exact PID/start-tick/mount/PID namespace enrollment; exclusive named writable volume; protected neighbors by full observation hash. Positive parsing explicitly returns native_admitted false with installed authority, provenance, direct-writer fencing and enabled-effect barriers pending.

OwnerLedger holds a private descriptor-bound nonblocking owner lock, refuses symlinks/hardlinks/public files, uses closed chained records, fsyncs its directory and before-effect intent, fixes spec/operation/generation/epoch/container ownership throughout the sequence and forbids duplicate intent or effect replay. Local stop persists intent before its command. recover_stop observes actual ended writers without reissuing stop. Torn records and ambiguous effects refuse.

snapshot_files binds a directory descriptor's device/inode/mount, refuses symlinks, hardlinks and unlisted mounts, reads SHA256, xattrs and native nanosecond mtime without rewriting original rows, files or ETags, and detects changing files/directories. reconcile_material compares original tenant/bucket/key/version path and row sizes, reports missing/unclassified files, refuses active sessions/prepared transactions and keeps enabled TUS/multipart/queue/Redis/provider barriers pending. This is a source comparison, never sufficient native proof, especially for same-version equal-length interrupted writes.

The installed guard is deliberately absent. Every original Docker/process/SQL authority method refuses before subprocess or ledger effects. Live provenance, database/filesystem reach closure and all native timing remain unverified.

Original upstream source corroborates file.uploadObject writing directly through pipeline without consuming its accepted signal, uploader.completeUpload installing a fresh controller after bytes, TUS r+ offset writes and URL generation removing tenant before encoding, multipart part rewrites and final assembly with asynchronously removed staging. Consequently ingress closure, body cancellation, an HTTP response or equal-length files cannot prove settlement.

## NS3 checks and coordinated proposal

I read profile.json, bounded-checks.py and run-python.py before using the exact frozen commands. The supervisor checked the declared helper/source hashes on each execution. I independently executed python, hybrid and bun phases, obtaining 128, 3 and 78 tests respectively, zero failures and zero skips. My actual raw logs are check-python-10.log, check-hybrid-11.log and check-bun-12.log. Each supervisor result returned native_accepted false. Peak owned-group RSS was 29003776, 104644608 and 63627264 bytes respectively. The final shared cumulative ledger values reported 1.8870169770055958 active seconds and 90410 output bytes, within the declared profile. These cumulative totals include other permitted source executions, not just my commands.

The proposal maps to actual Runtime.launch, start and activate_services, including image/config drift replacement, container start/run, volume creation, shared Storage startup, tenant POST/PATCH and final endpoints.json publication. The traced direct launch callers include Auth reconciliation, mail reconciliation, Realtime, Functions and management; indirect start/resume/provision/rotation paths are accounted for. It calls for one installed persistent authority across before-effect durable intent, immutable reobservation and the final current-inventory generation CAS. It explicitly preserves all-tenant shared scope, original secrets/features and neighbor behavior. The proposal is read-only and cannot itself install these guards. Installer/systemd/worker and external Catalog publication paths remain required.

This report supplies one fresh specification critic. The coordinator must separately verify the second required operational critic; I did not inspect that report or its messages.

## Review limits and pending acceptance

No Docker, native service, network, retained resource or independent unbounded probe was executed. The home /home/sbarah/AGENTS.md file was absent; available /home/sbarah/.codex/AGENTS.md and the explicit no-long-dash rule apply. I read the Graft skill but did not execute its refreshing commands because they fall outside this frozen source check profile. I used scoped read-only source inspection instead.

No prior report file, workflow journal/status/export, other critic verdict for this source candidate, implementer history or task/agent inventory was opened. The explicitly scoped product plan contains unrelated historical acceptance text. I encountered its opening current-code 1449-test summary, public55 V3 and BusyBox V8 summaries, the earlier warning-window summary, the CORE-ONLY restore summary at immutable snapshot 514e267628b2487b5664c2f33e1285a1f161ad00daa2813fd42ee9a2a815ecbe, architecture paragraphs describing earlier connection decisions and four cron/five HTTP effects at source 44755e02e24133afd50ae1cb560bc09d3ddf65d5811b1f8a9fa71781c5b658e5, and its Current native prerequisite status section at lines 272 through 306. That section summarizes configured Cron/HTTP, source/Docker CI, original configured bootstrap, ACL diagnostics, filesystem/configuration preservation and subsequent bootstrap fragments, including baked source 85791f0e1061259286822b9686afa817af61d26c6a735380e82d59085e5bcc69. I did not follow any linked report. Those statements supply required product-plan context and are not evidence for this source verdict.

SS3 original native settlement/interruption/cleanup, SS4 installed launch/restart/publication and consumer integration, SS5 all-tenant shared migration and every enabled provider/effect profile, plus complete merged runtime/product acceptance remain mandatory pending. No local fixture, helper, plan or passing source gate satisfies them.
