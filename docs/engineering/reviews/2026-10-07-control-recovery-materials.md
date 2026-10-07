# Complete control recovery materials

`lab/sqlite_material.py` implements private SQLite capture, decoding and prepared
file reconstruction for the `catalog` and `managed-keys` recovery material kinds.
It retains complete database bytes, including tables that selected-row backups
omit. This is a source component of full recovery, with no production activation
or native target construction authority.

Capture retains its own read-only descriptor, validates private ownership,
single-link mode 0600, byte budget and file identity, and rejects a WAL main file.
Decode checks integrity, foreign keys, material role and the complete schema.
Reconstruction compares the supplied schema template, retains its own directory
descriptor, creates an exclusive mode 0600 file, verifies restored bytes, and
returns an owned read-only descriptor. Failure cleanup removes only the created
inode. Every route checks the original finite absolute deadline. Private payload
bytes are excluded from the material's diagnostic representation.

The caller must produce the template with the same pinned original constructors.
A supplied template or stable file metadata does not prove original-source
provenance or native writer quiescence. The caller also owns complete pair
publication and recovery policy before Catalog or KeyStore service activation.
Preserved MFA grants and execution leases must not regain authority merely
because their source rows survived.

The 16 bounded source tests invoke the original Catalog and KeyStore constructors
on disposable private files. They compare every table row and exact database
bytes, then reopen the reconstructed stores with the original classes. They
verify installation identity, preserved revocation, runtime epoch binding,
descriptor reuse protection, schema and target refusal, finite deadline handling
and private diagnostic representation. The coordinator repeated those 16 tests
after copying the exact reviewed files to `lab/`: exit 0, no failures, errors or
skips, 3.881 seconds, with 512 MiB memory, 50 percent CPU and a 60-second outer
limit. Docker was deliberately unreachable. This does not prove PostgreSQL,
Storage, Functions, Cron, Vault, guest startup or full installation recovery.

The coordinator also ran the complete 2,118-test Python source suite after
integration: zero failures, errors or skips in 286.795 seconds. The disposable
runner supplied the repository and `lab/` import paths, with 512 MiB memory,
50 percent CPU, a 450-second outer limit and Docker unreachable. The preceding
runner attempt had an import-path error and was corrected before this passing
run. `typecheck:control` also passed. These checks validate source integration,
with the same native recovery limits described above.

The preceding published commit `ea2e181` passed all four jobs in
[CI run 37604042773](https://github.com/M7MMAD-OMAR/sbarbase/actions/runs/37604042773).
Its exact fresh artifacts contain 13 Docker reports with 254 passing checks and
six empty-host reports with 58 unique checklist assertions. The rehearsal and
latest JSON are the same observation and are counted once. Those artifacts
predate this source component and do not validate this change.
