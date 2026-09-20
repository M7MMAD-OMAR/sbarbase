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
