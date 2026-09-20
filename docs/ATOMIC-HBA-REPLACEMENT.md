# Complete-file HBA replacement

Runtime.hba now uses `lab/atomic_hba.py` instead of truncating the active pg_hba.conf through `cat > target`.

The helper requires an existing regular, non-symlink target and creates a unique restrictive temporary file in the same directory. It reads stdin, verifies the expected byte length and SHA256, preserves numeric mode/uid/gid, synchronizes the temporary file, renames it over the target, then synchronizes the directory. PostgreSQL reload remains a separate step after successful replacement.

Length and checksum validation are essential: sender death closes the pipe, and cat treats a truncated stream as successful EOF. Atomic rename alone would publish that truncated file. The helper refuses it before rename. Scripts use tools and options exercised on the pinned Supabase image, including BusyBox-compatible checksum and permission commands.

## Verified interruption behavior

Run `/usr/bin/python3 lab/partial-database-crash-check.py --upstream --hba`.

[16 live checks](evidence/upstream-atomic-hba-checks.json) pass in a bounded, network-disabled disposable upstream container. The probe reproduces legacy truncation after producer EOF, verifies rejection of short and equal-length corrupted content, then kills the stopped helper before and after rename. Before rename the complete old file remains. After rename the complete new file remains, although acknowledgment was lost. Mode/uid/gid are preserved; PostgreSQL parses the new file without errors and accepts an explicit reload signal. Exact disposable cleanup succeeds.

The full Python checkpoint remains 115 passing tests. The fresh real worker/Auth/REST/Storage lifecycle also passes 57 checks after this change, with SDK positive/negative access tests and exact isolated cleanup. Independent review found no must-fix in complete-file replacement. A successful reload signal does not prove every backend has adopted the new authentication rules. This is not a power-loss filesystem test.

## Remaining boundaries

This prevents partial-content publication. It does not reject a stale complete writer, serialize revisions, or make rename and PostgreSQL activation one transaction. Failure after rename is uncertain activation, never proof of rollback. SIGKILL can leave a uniquely named temporary file; only the disposable container is removed by the fault fixture, and no broad temporary-file cleanup is introduced in production.

The worker records the services boundary before HBA changes. Unknown later-stage receipts remain blocked. Next add a revision/authority protocol for delayed configuration writers and test service-phase reconciliation without replaying unknown effects.
