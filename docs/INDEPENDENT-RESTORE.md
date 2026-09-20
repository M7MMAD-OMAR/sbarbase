# Independent database restore

Recorded 2026-09-20. `lab/recovery-restore-db.py` passed 45 live checks in `evidence/independent-database-restore.json`. This is the database stage, not complete disaster recovery.

The authenticated encrypted export was restored transactionally into a fresh pinned Supabase PostgreSQL cluster, with a new administrator password, internal network and separate volume. The source remained stopped. The target is stopped with its volume retained for application verification. Target limits: 1 GiB RAM and 1 CPU.

The original preflight depended on running source containers. It now validates the native Linux daemon and host memory, then measures the actual new destination volume using a bounded read-only helper. Existing disk/inode thresholds are retained, including Btrfs inode semantics. Target resource collisions are refused; cleanup inspects container ownership even after partial creation.

Verified: all 32 ordinary Auth, Storage and public table inventories, row counts and content hashes; database ownership, locale and connection limit; actual collation version; canonical API defaults; three original scoped passwords connect to the restored database and cannot connect to postgres; REST statement/transaction defaults and Auth search path.

Still required: independent readback of full roles, memberships, ACLs and settings; adversarial review of failure cleanup; target Auth/REST/Storage startup; tenant connection rebinding; signing-key reencryption under fresh platform credentials; object/xattr restoration; identity and old signed-URL verification; source and neighboring environment isolation. No production transfer or recovery objective is certified.

Private target descriptor: `.lab/upstream/recovery-target.json`. Preserve it and the target volume for continuation. Do not print credentials or raw SQL failure output. The development handoff ZIP excludes private recovery data.
