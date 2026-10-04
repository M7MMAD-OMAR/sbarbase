# Synthetic diagnostics across containers

Reviewed 2026-10-04. Acceptance covers three synthetic producer/receiver cases on this computer's isolated Docker daemon. Candidate PostgreSQL startup, vendor message grammar, private credentials, live checkpoints, recovery and production remain unaccepted.

## Result and resource scope

Packet `f0e157818b7f415b9a2b48d63b9d9278` completed 146 commands, 292 public streams, 540226 stream bytes, 680501 pre-receipt evidence bytes and 4.704920835 seconds. Independent saved-public-data review checked 2671 bindings. These counts belong to this packet; the earlier [single-container FIFO](2026-10-04-private-diagnostics-fifo.md) retains its separate 436-check acceptance.

| Native case | Diagnostic gate | Observed private spool |
|---|---|---|
| One permitted synthetic line | ACCEPTED, COMPLETE | 16 bytes, one line |
| One line containing a private generated marker | REFUSED, UNKNOWN | 74 bytes, one line |
| 65537 producer bytes | REFUSED, INPUT_BOUND | 65536 bytes, 4096 permitted lines |

The two negative cases pass the test because their diagnostic gates refused the intended input. They do not establish successful processing of that input. The permitted grammar is one fixed synthetic literal, not a PostgreSQL policy.

Two fresh owned local volumes held runtime controls and separate private diagnostics. An offline UID 0/GID 0 helper used only CHOWN to prepare their empty namespaces. Each of three sequential cases used separate UID 100/GID 101 writer and receiver containers, with effective group 101 and all capabilities dropped. Every helper had network none, a read-only root, a private PID namespace, no new privileges, logging driver none, no ports/healthcheck/restart, 64 MiB memory and swap limit, 0.25 CPU and 16 PIDs. Mounts were exact owned volumes with no copy-up; the writer had no diagnostic-volume mount.

The source bounds each spool to 65536 bytes and a line to 4096 bytes. This is bounded allocation by these trusted sources, not a filesystem quota. Persistent volumes preserve evidence on unexpected failure; deletion is not secure erasure. No original retained key volume, password, provider, SQL or PostgreSQL server was accessed. Public metadata from the seed used a separately admitted fixed source; private writer and receiver output was never attached or fetched as Docker logs.

## Completion and publication

The receiver held separate read and dummy-write FIFO descriptors. Native writer exit 0/OOM false and exact source/resource identity preceded a completed terminal-attestation exec, then exact-CID USR2. The receiver closed its dummy endpoint only after admitting that attestation and writer completion control, and drained to EOF. A classified negative stayed in collection until this terminal proof.

All immutable control/result/commit/ACK files used Linux renameat2 with NOREPLACE and held descriptor/path checks. Unsupported publication refuses; there is no replacing rename or hardlink fallback. This avoids exposing a two-link control file to a one-link reader during hardlink cleanup. Phase and deadline preceded the result-ready commit. The fixed reader reconstructed only closed enums, booleans and bounded counts; its exit 0 and empty stderr preceded exact-CID USR1. ACK alone never released the receiver. Native receiver exit 0/OOM false followed.

Fourteen exact CID/name absences covered all seven helpers. Renewed ownership and zero-consumer observations preceded removal of both successful fixture volumes, with two final filtered absence observations. No uncertain create, interruption or deadline refusal remained. Limits were 360 seconds total, 240 work/120 cleanup, 20/10 seconds per command, 2 MiB complete public streams with 512 KiB cleanup reserve, and 4 MiB evidence.

## Preserved failures and correction

Operator review first found that a late interruption during volume ownership checks could still allow deletion. The corrected operator checks each deletion and records attempted, confirmed and absence states, including partial or uncertain removal. An already issued daemon operation cannot be undone.

The first native seed exited 66; its exact helper was removed and both volumes retained. A separate read-only observer found three correctly owned runtime children and untouched known diagnostic children. That did not identify a failing syscall or errno. A fresh instrumented seed then refused the runtime readback predicate with a count of zero after creating its children. Its failure and two volumes remain preserved.

The corrected scan opens a fresh directory description relative to the held root for each enumeration, verifying identity and stable metadata before and after. It keeps the original empty/exact-name predicates, mutation order and CHOWN-only capability scope. A selected offset-sensitive model exercises the actual old and corrected functions, but is not a claimed native kernel reproduction or universal Python defect. The final native packet completed the corrected seed and all three bridge cases. Earlier refusals retain their original verdicts and source scopes.

## Provenance and remaining work

The immutable helper was `sha256:941cf90d7aec20ec1f6c720cdca552705dc96d77adfc94f8a8809bbfcc12237c`, admitted through its separate positive build-completion receipt. Captured command/source bytes and resources matched frozen reviews. Existing independent nonbuilder reviewers were reused explicitly; no new reviewer identity is claimed.

Local records are `.lab/private-diagnostic-bridge-v4-build-20261004/run-diagnostic-bridge-f0e157818b7f415b9a2b48d63b9d9278/receipt.json` and `.lab/private-diagnostic-bridge-v4-actual-critic-20261004/review.json`. Ignored operator helpers/raw packets are not distributed with a clean clone. This review does not add a shipped logging path or reproducible native acceptance command. Source/stub ownership, I/O, schema, signal and cleanup controls are separate from native proof; hostile races, arbitrary producers, premature external signals, OOM, delayed daemon creation and power loss remain unrun. Standard signals may coalesce.

A live server still needs a separately reviewed bounded checkpoint, exact vendor grammar, protected temporary-server and migration sessions before sensitive SQL, current key guards before every launch, cold/warm Vault continuity and coherent physical/application/object recovery. Final EOF from these finite writers cannot establish a live-server checkpoint or absence of future errors. No general secret-safety, supported-host matrix or production acceptance follows.

Published documentation head `1f8c784d5a8e9592dc4fb81a8463c2a8070f802a` passed [CI 37186068299](https://github.com/M7MMAD-OMAR/sbarbase/actions/runs/37186068299), with checks, Python floor and Docker install successful; conditional empty-host acceptance was skipped. Manually dispatched [Website 37186493777](https://github.com/M7MMAD-OMAR/sbarbase/actions/runs/37186493777) passed build/test/artifact only. These checks do not certify subsequent edits or production.

Primary technical references reviewed 2026-10-04: [Docker volumes](https://docs.docker.com/engine/storage/volumes/), [Linux rename](https://man7.org/linux/man-pages/man2/rename.2.html), [Linux open](https://man7.org/linux/man-pages/man2/open.2.html) and [Python directory enumeration](https://docs.python.org/3/library/os.html#os.listdir). Online Python documentation displays 3.14.8; the admitted helper uses 3.14.4. No identical implementation or dependency update is assumed.
