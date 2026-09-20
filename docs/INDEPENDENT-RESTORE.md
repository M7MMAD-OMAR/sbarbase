# Independent database restore

Recorded 2026-09-20. `lab/recovery-restore-db.py` passed 45 live checks in `evidence/independent-database-restore.json`. This is the database stage, not complete disaster recovery.

The authenticated encrypted export was restored transactionally into a fresh pinned Supabase PostgreSQL cluster, with a new administrator password, internal network and separate volume. The source remained stopped. The target is stopped with its volume retained for application verification. Target limits: 1 GiB RAM and 1 CPU.

The original preflight depended on running source containers. It now validates the native Linux daemon and host memory, then measures the actual new destination volume using a bounded read-only helper. Existing disk/inode thresholds are retained, including Btrfs inode semantics. Target resource collisions are refused; cleanup inspects container ownership even after partial creation.

Verified: all 32 ordinary Auth, Storage and public table inventories, row counts and content hashes; database ownership, locale and connection limit; actual collation version; canonical API defaults; three original scoped passwords connect to the restored database and cannot connect to postgres; REST statement/transaction defaults and Auth search path.

Additional retained-target verification passed five checks in `evidence/independent-boundary-checks.json`: complete scoped role attributes, memberships, database ACLs and settings match the encrypted export, and the target stopped afterward.

Adversarial review identified descriptor overwrite, premature success publication, additive ACL reconciliation and unnamed helper cleanup. The restore consumer now refuses an existing target descriptor, publishes success after verified stop, records cleanup failure, resets supported ACL grantees before replay, compares boundary metadata, names and cleans its helper, and bounds pg_restore to 120 seconds. These new fresh-allocation and failure branches are syntax checked but have not yet received a new full restore or fault-injection run.

Still required: fault-injection verification of cleanup; target Auth/REST/Storage startup; tenant connection rebinding; signing-key reencryption under fresh platform credentials; object/xattr restoration; identity and old signed-URL verification; source and neighboring environment isolation. No production transfer or recovery objective is certified.

Private target descriptor: `.lab/upstream/recovery-target.json`. Preserve it and the target volume for continuation. Do not print credentials or raw SQL failure output. The development handoff ZIP excludes private recovery data.

## Original service verification

`lab/recovery-check-services.py` passed 11 live checks on the retained independent target. Pinned original Auth and REST start against the restored database. The fixture logs in using its original password, retains its user ID, receives a working session and reads its original RLS-protected rows. REST rejects a token signed by an unrelated secret. Source stays stopped; target services and database stop after the run with volumes retained. This is direct service HTTP verification, not a restored management gateway or SDK cutover. Auth login modifies session/audit state on the target after the earlier exact table comparison.

The new service containers remain stopped and are intentionally not overwritten on repeat runs. Inspect and explicitly reconcile them before rerunning. At this service-stage checkpoint Storage, objects and original signed URLs were still unverified; subsequent sections record their later results.

## Independent file Storage

`recovery-check-storage.py` created a new target Storage metadata database and object volume with freshly generated platform credentials. Tenant connections were rebound to the target, original tenant signing material reencrypted and restored, and file bytes/xattrs restored exactly. Initial verification failed because it incorrectly expected identical fixture contents for every object. The source and target stopped safely.

`recovery-resume-storage.py` explicitly resumed read-only validation on that retained target and passed eight checks. Each download is now matched to its own exported bucket/name/version bytes. Two objects download with the original environment service key and reject an unrelated signing secret. Exact files/xattrs and decrypted signing rows match the export. Source remains stopped; target stops after verification. This does not yet prove end-user Storage RLS, a pre-export signed URL, gateway cutover or complete platform recovery. These scripts are fixture-specific and still need consolidation and failure-path tests before general use.

## End-user isolation and source signing continuity

`recovery-check-storage-rls.py` passed 12 checks using the original user's real password/session. Own bytes download correctly. Another owner's object cannot be downloaded or signed, anonymous access is denied, and a newly issued signed URL downloads without the user token. Target services stop afterward.

`recovery-check-source-url.py` passed seven checks with sequential source/target startup. Source signing material still equals the encrypted snapshot. A URL issued on the source returns the same bytes on the independent target with unchanged path, token and tenant host. A modified signature is rejected. Source and target are stopped after the probe. Only their internal origin/IP changes; this does not prove a public gateway/DNS cutover. The URL was issued after export, explicitly not a pre-export fixture. Its private copy is in `.lab/upstream/recovery-source-url.json` and must not enter documentation or Git.

Next recovery work: consolidate fixture scripts into explicit resumable lifecycle stages; exercise interrupted restoration and cleanup; capture a valid URL inside a new pre-export fixture for a complete chronological rehearsal; integrate endpoint cutover through management routing with source fencing and rollback. These are still prototype checks, not a production backup service.

## Cleanup failure verification

Six fault-injection unit cases exercise the actual database-restorer cleanup function. Three initially failed: helper-removal failure and helper ownership mismatch skipped DB shutdown, and a missing verified target was promoted to success. Independent cleanup attempts now still stop the owned DB after helper failure, preserve foreign resources, reject missing verified targets and publish cleanup-failed on uncertainty. All 40 Python tests pass.

`recovery-cleanup-check.py` passed six live checks with two disposable 64 MiB containers, no data volumes and no network. Injected helper-removal failure leaves an explicit failed descriptor while the target container actually stops. A subsequent cleanup retry removes the helper and retains stopped target state without promoting the failed operation to success. The probe removes only its own identity-checked disposable containers. Retained recovery and source volumes are untouched. These cleanup checks did not exercise actual pg_restore interruption; the later interruption section records that additional result. Controller SIGKILL, daemon outage and whole-host failure remain open.

## Interrupted restoration and operator reconciliation

`recovery_reconcile.py` is an explicit recovery shutdown command under the operation lock. It validates the retained target prefix, placement and inventory ownership before mutations, stops services before the DB using inspected container IDs, attempts remaining stops after a failure, and records stopped/stop-failed. It retains all containers and volumes. Partial operation status becomes interrupted, never verified. Existing database-restored status remains a prior verification record. Five new unit cases pass; all 45 Python tests pass. A live run stopped the four retained target containers without data deletion. This command resumes control and shutdown, not unfinished data restoration.

`recovery-interruption-check.py` passed 38 live checks. A real pg_restore of the encrypted dump into a disposable database was paused by an event trigger and its PostgreSQL backend terminated. The transaction rolled back, leaving no partial Auth/Storage schemas or application table. After removing the fixture trigger, replaying the same dump succeeded and all 32 table counts/hashes matched export. The disposable database was removed and the target stopped. The retained environment database was not changed. This is server-side session termination, not controller SIGKILL, power loss or automatic resume orchestration.

## Chronological fenced export and fresh target

`cutover-export.py` has now run on the owned source. It persisted maintenance for all four ready source environments before source startup, because shared Storage must stop for this export. A source URL was issued and verified before the dump, then enclosed in the encrypted artifact. Scoped service logins were disabled during export, followed by full database refusal. Source shutdown and 34 export checks passed. Direct standalone cutover mode was removed after independent review because it bypassed maintenance.

The old target descriptor was copied durably into `.lab/upstream/recovery-target-history/` and recorded in the private cutover journal before replacing the active descriptor. Its containers and volumes remain stopped and retained. A new cluster from the new artifact passed 49 database checks, 11 Auth/REST checks, nine Storage checks and 14 end-user Storage checks. The last set verifies the unchanged pre-export URL downloads matching bytes on the new target. Earlier evidence snapshots remain in Git history; counts are different scopes, not additive coverage.

Current operation phase: target-services-verified-routing-paused. Source and both targets are stopped; all four source runtime routes remain in maintenance. Selected source database and scoped logins remain fenced. The target has not received public gateway traffic. Private journal: `.lab/upstream/cutover-operation.json`. Resume from it; do not rerun allocation/export or discard the old target.

This is a downtime rehearsal. Persisted maintenance prevents new requests, but the controller does not yet signal/wait for drain inside every live gateway process; service stop can abort admitted uploads. Snapshot consistency after service stop is distinct from graceful migration. Actual managed SDK cutover, neighbor service restoration and a controlled rollback/forward-only policy after new writes remain open.

## Managed gateway and SDK target publication rehearsal

`cutover-target-check.py` started only the current verified target, with all source containers stopped. `cutover-sdk-check.ts` staged current target addresses in the persistent catalog, verified continued refusal during maintenance, resumed that environment and used the original Supabase SDK through the composed loopback application gateway. Eleven checks pass: original-password login and identity, RLS reads, row insertion confirmed directly on the target database, Storage upload/download and the unchanged pre-export signed URL through the gateway without an API key. Probe row/file were removed, temporary key revoked, routing paused and all target containers stopped.

The private operation is now managed-target-verified-routing-paused. Current target placement is persisted but inactive. Refresh target addresses on the next startup before resume. `target_writes_may_exist` is set before publication; target Auth sessions and other changes mean blindly reverting to the older source is unsafe even though probe data was cleaned up. Source remains fenced and all four source routes remain paused. Unaffected source environment restoration is next.

Parent cleanup now independently restores durable maintenance if the SDK child fails or times out, then stops all owned target services even if one stop fails. Three unit cases cover this maintenance helper; all 54 Python tests pass. This is not a controller SIGKILL test. Strict checking of the SDK probe and full application dependency chain now passes after preserving the fetch transport preconnect interface in the management wrapper; four management/application tests also pass.

## Unaffected source environments restored

`cutover-neighbors.py` ran the normal source startup path while recovery targets remained stopped. The selected source database remained closed, had no sessions and its old Auth/REST containers remained stopped. Three unaffected environments restarted. Sixteen composed-gateway checks verified their maintenance refusal, explicit revision-checked resume, Auth settings and REST root responses while the moved environment stayed paused with target placement. Temporary keys were revoked and all source containers stopped after the foreground probe.

Private operation phase: neighbors-restored-target-paused. Three neighbor routes are active in the catalog and work on normal source startup; they are not currently online because containers are stopped. The moved target remains paused. This run does not claim complete neighbor data workloads or simultaneous source/target capacity. A startup/stop lifecycle that manages the moved target's dynamic addresses and persistent pause state remains to implement before routine full-platform use.
