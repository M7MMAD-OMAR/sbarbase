# Live diagnostic checkpoints and isolated credentials

Reviewed scope: isolated local Docker fixtures on 2026-10-04. These are finite native prerequisite observations. The operator prototypes are ignored local helpers, not a shipped startup path or a reproducible clean-clone acceptance command. Candidate PostgreSQL startup and production remain unaccepted.

## A checkpoint while both actors are live

Independent review `.lab/private-diagnostic-live-checkpoint-v2-actual-critic-20261004/review.json` accepts packet `.lab/private-diagnostic-live-checkpoint-v2-build-20261004/run-diagnostic-live-5d4d3c9370e74043bfb2f4ea566d2801/receipt.json`: 3697 saved-public bindings, 170 commands/340 streams/884909 bytes, 1048369 pre-receipt evidence bytes and 6.996608533 seconds.

| Case | Consumed prefix | Final drained input |
|---|---|---|
| Permitted input | 175 bytes, accepted | 191 bytes, accepted |
| Unknown input before checkpoint | 238 bytes, refused | 238 bytes, refused |
| Unknown input after checkpoint | 179 bytes, accepted | 253 bytes, refused |

The source-bound barrier captures the actual consumed prefix before subsequent writes. Writer and receiver were live without OOM before and after the completed prefix reader. A fixed phase/sequence acknowledgment on a read-only diagnostic mount preceded writer release. The first refusal stayed sticky. Native writer termination and completed terminal attestation preceded receiver EOF; the completed final reader preceded receiver release and cleanup. All seven helper CIDs and names, and both successful fixture volumes, were absent at completion.

A prefix acceptance says which bytes were checked at that point. It is not a continuous server monitor and cannot approve bytes written afterward. This synthetic grammar does not accept real PostgreSQL, initializer or extension diagnostics. Unknown private marker and spool bytes were not read, printed or hashed on the host. Cooperative source-bound actors remain an assumption, including access by the Docker administrator.

The original design and source refusals remain preserved. The source review found cleanup could adopt a matching pre-existing helper before a recorded create attempt. The corrected operator records attempt lineage and uses a CID-only absence projection before creation. The earlier [bridge observations](2026-10-04-private-diagnostics-bridge.md) are not reclassified by this finding.

## A separate PostgreSQL password volume

Independent review `.lab/patched-private-credential-actual-critic-20261004/review.json` accepts packet `.lab/patched-private-credential-v2-build-20261004/run-private-credential-065d336e3b0d4898aa19b5d198294983/receipt.json`: 617 saved-public bindings, 88 commands/176 streams/174315 bytes, 248131 pre-receipt evidence bytes and 2.254651044 seconds.

A tiny root seed transferred a new empty credential directory to native UID 100/GID 101 with only CHOWN capability. The native generator published an exclusive 64-character lowercase hexadecimal password, regular mode 0600 and one link, beneath its mode 0700 directory. Publication used full writes, file and parent synchronization, and no-replace rename. Two fresh read-only guards independently validated format, namespace and the same bounded numeric metadata witness. Private descriptors closed before any public frame. The host received outcomes and metadata only.

All four helper CIDs and names were absent. One new owned local credential volume was deliberately retained with a renewed zero-consumer query. It is separate from the retained master-key/configuration volume and was never mounted by a diagnostic receiver. Future use requires a renewed read-only guard, matching witness and ownership/consumer admission. Do not delete, relabel or silently regenerate it.

The original source deadline refusal remains preserved. Corrected workers reject expiration during public frame construction, writing, closing and final exit. A printed COMPLETE frame cannot override a failed native result. The independent source review used 108 selected public-stub controls; those negative stubs are distinct from this one positive native observation.

This proves a finite filesystem handoff under cooperative actor assumptions. It does not prove vendor password-file import, authentication, password rotation, malicious valid-value substitution detection, crash/powerloss durability or general secret safety. The original master key and failed diagnostic volumes were untouched.

## Public PostgreSQL build metadata

Independent review `.lab/patched-public-build-defaults-v4-actual-critic-20261004/review.json` accepts packet `.lab/patched-public-build-defaults-v4-build-20261004/run-public-build-defaults-137220d4f7364c1f82024a4b7c7b666e/receipt.json`: 250 saved-public bindings, 13 commands/26 streams/14401 bytes, 37448 pre-receipt evidence bytes and 0.446371061 seconds. One immutable PostgreSQL 17.11 helper ran fixed public CLI commands under native 100:101, without mounts, server or connection. Attached output and native wait were successful with empty stderr and no OOM. Its exact CID and name were absent, and the immutable image was renewed.

The eight-field frame observes PostgreSQL and pg_config 17.11, native group 101, the installed public executable/library directories and 20 configure tokens parsed as data without evaluation. No socket-directory configure option occurs. This metadata alone does not establish the installed client's effective empty-host socket default or successful early vendor connection. The separate corrected negative observation below establishes the socket path for one fixed command; early vendor connection remains unrun.

Two native refusals remain preserved: missing optional Config.User broke the initial image template before any helper creation; inherited health timing fields caused the second created-helper admission to refuse before startup. Corrected projections use nullable map access and preserve exact inherited disabled-health fields. A separate 144-binding actual cleanup review accepted removal of that one earlier unstarted helper after two created-state admissions and exact CID/name absence. The original cleanup-source counterexample remains preserved; corrected prior-only cleanup rechecks created status at removal admission. This is a bounded cooperative observation, not atomic daemon ownership protection.

## Original installed-client refusal and corrected negative observation

The subsequent empty-host client trial remained FAIL. Independent `.lab/patched-empty-host-client-actual-refusal-critic-20261004/review.json` binds 175 saved-public controls to packet `.lab/patched-empty-host-client-build-20261004/run-public-empty-host-c81871ecc4dd44b88dfe084fff869f71/receipt.json`: ten commands, twenty streams, 7927 stream bytes and 29683 pre-receipt evidence bytes. The fixed public no-mount client returned native 2 with empty stdout, but its 238-byte stderr included a non-regular password-file warning for the operator's explicit `/dev/null` input before the expected local socket diagnostic. The strict grammar correctly refused; it was not widened. The exact owned helper CID and name were absent after cleanup, with native exited2 and no OOM in the saved ownership snapshot.

The separately reviewed correction uses the fixed absent-parent password-file path `/nonexistent/sbarbase-public.pgpass`, with existence and symlink guards before invoking the installed client. Independent `.lab/patched-empty-host-client-v2-actual-critic-20261004/review.json` accepts 225 saved-public bindings for the distinct corrected packet: thirteen commands, twenty-six streams, 12374 stream bytes and 61048 total evidence bytes. Native start, independent wait and terminal status all report 2, with empty stdout and exactly 183 bytes of expected two-line ENOENT stderr for `/run/postgresql/.s.PGSQL.5432`. No warning occurs. Exact helper CID/name absence, no OOM and unchanged immutable image are independently recorded.

This establishes the default socket path only for that fixed client command and environment. The original source, failed packet and 175-control refusal remain preserved. The parent observed outer exit 0 without a separate outer transcript. Successful server connection, authentication, client-library source provenance, early vendor bootstrap and production acceptance remain unresolved or unrun.

## Remaining startup and recovery work

The computer is the selected isolated local server fixture. No external server purchase is needed for this scope. Next bind installed-client socket behavior, the real vendor diagnostic grammar, effective protective settings before sensitive SQL, complete configured preloads and fresh key/password guards to candidate startup. Then prove cold/warm PostgreSQL and original Vault-cipher continuity, followed by coherent database/object/service recovery with one writer. Capacity, security maintenance, public reproducibility and broader host support remain separate open acceptance requirements.

Previously published documentation head `24d48e2c00269ab48754cb9bc92308081e8bc644` passed [CI 37190012912](https://github.com/M7MMAD-OMAR/sbarbase/actions/runs/37190012912): checks, Python floor and Docker install succeeded; conditional empty-host acceptance was skipped. Manually dispatched [Website 37190020770](https://github.com/M7MMAD-OMAR/sbarbase/actions/runs/37190020770) passed build/test/artifact only. These results do not transfer to later edits or constitute deployment or production acceptance.
