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

The new service containers remain stopped and are intentionally not overwritten on repeat runs. Inspect and explicitly reconcile them before rerunning. Storage, objects and original signed URLs remain unverified on this target.

## Independent file Storage

`recovery-check-storage.py` created a new target Storage metadata database and object volume with freshly generated platform credentials. Tenant connections were rebound to the target, original tenant signing material reencrypted and restored, and file bytes/xattrs restored exactly. Initial verification failed because it incorrectly expected identical fixture contents for every object. The source and target stopped safely.

`recovery-resume-storage.py` explicitly resumed read-only validation on that retained target and passed eight checks. Each download is now matched to its own exported bucket/name/version bytes. Two objects download with the original environment service key and reject an unrelated signing secret. Exact files/xattrs and decrypted signing rows match the export. Source remains stopped; target stops after verification. This does not yet prove end-user Storage RLS, a pre-export signed URL, gateway cutover or complete platform recovery. These scripts are fixture-specific and still need consolidation and failure-path tests before general use.

## End-user isolation and source signing continuity

`recovery-check-storage-rls.py` passed 12 checks using the original user's real password/session. Own bytes download correctly. Another owner's object cannot be downloaded or signed, anonymous access is denied, and a newly issued signed URL downloads without the user token. Target services stop afterward.

`recovery-check-source-url.py` passed seven checks with sequential source/target startup. Source signing material still equals the encrypted snapshot. A URL issued on the source returns the same bytes on the independent target with unchanged path, token and tenant host. A modified signature is rejected. Source and target are stopped after the probe. Only their internal origin/IP changes; this does not prove a public gateway/DNS cutover. The URL was issued after export, explicitly not a pre-export fixture. Its private copy is in `.lab/upstream/recovery-source-url.json` and must not enter documentation or Git.

Next recovery work: consolidate fixture scripts into explicit resumable lifecycle stages; exercise interrupted restoration and cleanup; capture a valid URL inside a new pre-export fixture for a complete chronological rehearsal; integrate endpoint cutover through management routing with source fencing and rollback. These are still prototype checks, not a production backup service.

## Cleanup failure verification

Six fault-injection unit cases exercise the actual database-restorer cleanup function. Three initially failed: helper-removal failure and helper ownership mismatch skipped DB shutdown, and a missing verified target was promoted to success. Independent cleanup attempts now still stop the owned DB after helper failure, preserve foreign resources, reject missing verified targets and publish cleanup-failed on uncertainty. All 40 Python tests pass.

`recovery-cleanup-check.py` passed six live checks with two disposable 64 MiB containers, no data volumes and no network. Injected helper-removal failure leaves an explicit failed descriptor while the target container actually stops. A subsequent cleanup retry removes the helper and retains stopped target state without promoting the failed operation to success. The probe removes only its own identity-checked disposable containers. Retained recovery and source volumes are untouched. This does not exercise actual pg_restore interruption, SIGKILL, daemon outage or whole-host failure; these remain open.
