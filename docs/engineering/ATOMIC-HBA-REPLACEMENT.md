# Complete-file HBA replacement

Runtime.hba now uses `lab/atomic_hba.py` instead of truncating the active pg_hba.conf through `cat > target`.

The helper requires an existing regular, non-symlink target and creates a unique restrictive temporary file in the same directory. It reads stdin, verifies the expected byte length and SHA256, preserves numeric mode/uid/gid, synchronizes the temporary file, renames it over the target, then synchronizes the directory. PostgreSQL reload remains a separate step after successful replacement.

Length and checksum validation are essential: sender death closes the pipe, and cat treats a truncated stream as successful EOF. Atomic rename alone would publish that truncated file. The helper refuses it before rename. Scripts use tools and options exercised on the pinned Supabase image, including BusyBox-compatible checksum and permission commands.

## Verified interruption behavior

Run `/usr/bin/python3 lab/partial-database-crash-check.py --upstream --hba`.

[36 live checks](evidence/upstream-atomic-hba-checks.json) pass in a bounded, network-disabled disposable upstream container. The probe reproduces legacy truncation after producer EOF, verifies rejection of short and equal-length corrupted content, then kills the stopped helper before and after rename. Before rename the complete old file remains. After rename the complete new file remains, although acknowledgment was lost. Mode/uid/gid are preserved; PostgreSQL parses the new file without errors and accepts an explicit reload signal. Exact disposable cleanup succeeds.

The full Python checkpoint remains 121 passing tests. The fresh real worker/Auth/REST/Storage lifecycle also passes 57 checks after this change, with SDK positive/negative access tests and exact isolated cleanup. Independent review found no must-fix in complete-file replacement. A successful reload signal does not prove every backend has adopted the new authentication rules. This is not a power-loss filesystem test.

## Prepared-request revision protection

Runtime replacement now prepares an immutable request containing the captured full Docker container ID, active-file SHA256 and a fresh UUID comment plus the complete desired rules. Apply targets that exact container ID, acquires a stable container-local lock, compares the current whole-file hash, then validates and replaces content while retaining the lock. BusyBox flock supports nonblocking acquisition; a busy writer fails with no change. The lock file is never unlinked by normal updates or temporary cleanup.

Every managed update has a fresh UUID even when rules are identical. The live probe verifies that two competing prepared requests cannot both apply, successful requests cannot replay, same-rule generations do not revive old requests, and a busy writer leaves the target unchanged. Killing the stopped lock holder releases its lock. No expected revision or container identity is recaptured after rejection or uncertain acknowledgment.

## Activation evidence

The probe now uses fresh neighbor connections plus an already-open session. Publishing a restrictive file without reload leaves new connections allowed. After reload, fresh connections receive an explicit HBA rejection while the already-open session still executes SQL. An invalid file appears in pg_hba_file_rules even though pg_reload_conf returns true; the previous restriction remains observed immediately after signaling. That observation alone does not confirm processing of the invalid reload. Explicitly restoring valid rules and reloading permits fresh connections again.

Runtime now refuses reload when the file parser reports any errors and refuses a false signal acknowledgment. These checks do not certify exact activation or revoke existing sessions. The worker services phase remains unresolved after any failure. See [the next authority design gate](HBA-OPERATION-AUTHORITY-DESIGN.md).

## Remaining boundaries

This prevents partial-content publication. It now rejects stale already-prepared managed requests. It does not revoke an old operation that prepares a new request after a newer update, and it does not make rename and PostgreSQL activation one transaction. Administrator writes, lock-file replacement and restored filesystem clones remain outside the managed revision protocol. Failure after rename is uncertain activation, never proof of rollback. SIGKILL can leave a uniquely named temporary file; only the disposable container is removed by the fault fixture, and no broad temporary-file cleanup is introduced in production.

The worker records the services boundary before HBA changes. Unknown later-stage receipts remain blocked. Next bind configuration authority to the exact operation and define activation reconciliation, without replaying unknown effects.
