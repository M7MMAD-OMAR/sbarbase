# Private diagnostic FIFO mechanics

Reviewed 2026-10-04. Acceptance: one isolated synthetic FIFO prototype only. PostgreSQL startup, actual vendor diagnostics, credentials, Vault continuity, recovery and production remain unaccepted.

## Problem and observed result

Suppressing SQL statement and parameter logging does not guarantee that extension or server errors exclude private values. A later bootstrap needs diagnostics that remain inside its fixture while the operator receives only bounded status fields. This prototype tests the receiver mechanics before any real password or SQL is introduced.

The corrected native packet `440ff8975d984fb198dac7e4eb6a9d44` passed 32 intended case outcomes. Independent saved-data review recorded 436 checks, 19 commands, 38 complete public streams, 118334 retained stream bytes, 201838 evidence bytes and 1.188615498 seconds. The earlier coherent packet `b3ebaf2d1e664aed905ce4bb551c44b3` remains separately accepted with 435 checks; its later identified READY publication ordering issue was corrected before reuse. Counts belong to separate scopes and must not be added together.

The prototype ran as native UID 100/GID 101 with effective group 101 in one owned helper container: network none, read-only root, private PID namespace, capabilities dropped, no new privileges, no ports/healthcheck/restart, logging driver none, 64 MiB memory and swap limit, 0.25 CPU and 32 PIDs. Its only writable mount was a new 4 MiB tmpfs. No named volume, retained key fixture, provider, password, SQL or PostgreSQL server was accessed.

## Tested mechanics

| Scope | Evidence and limit |
|---|---|
| FIFO identity | Exclusive 0600 FIFO and spool in private 0700 directories; no-follow descriptor/path checks, type/mode/link admission and native substitution refusals. Wrong-owner metadata is an explicit stub test. |
| Bounded input | 65536-byte total ceiling, 4096-byte line ceiling, complete versus fragmented/coalesced input, exact total boundary and one byte over. Incomplete, empty, invalid UTF-8, NUL/control, unknown and warning/error/fatal/panic input refuses. |
| Spool failures | Real private spool descriptors with injected partial writes, zero writes and OSError. These four injected scopes, including ownership, are not native fault-injection claims. |
| Producer completion | Distinct read and dummy-write descriptors, bounded control pipe, independently reaped writer, byte-count/exit mismatch and timeout refusals. Successful EOF requires completed producer and dummy closure. |
| Public result | Worker runs detached, with no runtime attach or logs route. A fixed reader validates private result files and reconstructs only known case IDs/reasons, booleans and bounded counts; no raw JSON, spool, marker or fingerprint is emitted. |
| Completion order | Phase and deadline precede READY publication. Completed reader exit and empty stderr precede exact-CID USR1 release. ACK alone never stops the worker. Final worker exit 0/OOM false and exact CID/name absences were independently matched. |

The successful input grammar contains only two synthetic literals. It is not a PostgreSQL message policy. The schema case exercises internal scalar and file parsing; it does not inject a malformed frame into a separate running reader process. True forced early EOF, actual WAIT, premature external signals, OOM and late daemon creation are not native negative proofs. Static controls cover several refusal paths but are recorded separately.

No host read, print or hash of private spool bytes or random markers occurred. This finite check is not a general noninterference or secret-safety proof. Tmpfs can reach host swap, and deletion is not secure erasure. The explicit native limits do not establish power-loss durability, capacity or supported-host coverage.

## Preserved losses and source binding

The original design lacked independently closeable FIFO endpoints and a result-ready lifecycle before tmpfs deletion. A corrected proposal still allowed worker exit before the result reader completed. Source review then found two manufactured negative outcomes and failure to retain signal-arrival phase. Operator review found uncertain create could be mistaken for complete cleanup, and final deadline/interruption admission was missing. Each refusal and frozen input remains preserved; no failed receipt was rewritten as acceptance.

The reviewed corrected worker and fixed reader were supplied as captured public Python command bytes to the admitted immutable helper `sha256:941cf90d7aec20ec1f6c720cdca552705dc96d77adfc94f8a8809bbfcc12237c`. Exact selected container Cmd/image/owner/resources and source copies match the independent frozen inputs. This is operator prototype evidence, not a change to the shipped startup path. Its helpers and complete raw packets are ignored local artifacts and are not distributed in a clean clone; this note supplies scope and provenance, not a reproducible acceptance command.

Local records are `.lab/patched-diagnostic-transport-v4-build-20261004/run-diagnostic-fifo-440ff8975d984fb198dac7e4eb6a9d44/receipt.json` and `.lab/patched-diagnostic-transport-v4-actual-critic-20261004/review.json`. Earlier planning/source/operator and v3 actual reviews remain distinct. The existing independent nonbuilder reviewer was reused explicitly because new thread creation was unavailable.

## Remaining work

A real server needs separately admitted shared FIFO storage, private persistent or temporary spool policy, exact producer CID/owner completion attestation, and a live checkpoint distinct from final EOF. The next synthetic bridge proposal and cold preflight advice are unrun. Before sensitive vendor SQL, prove protective settings in the actual temporary server and inherited migration sessions. Then wire the existing key guard before every launch, prove cold/warm identity and the old encrypted Vault sentinel, and complete coherent physical/application/object/key recovery. None of those gates is accepted by this prototype.

Published source `34e4aee729b125acdbb779924186f97f47b11259` passed [CI 37183462996](https://github.com/M7MMAD-OMAR/sbarbase/actions/runs/37183462996), with checks, Python floor and Docker install successful and conditional empty-host acceptance skipped. Manually dispatched [Website 37183499961](https://github.com/M7MMAD-OMAR/sbarbase/actions/runs/37183499961) passed build/test/artifact only. Those checks do not certify later edits or production.

Primary primitives reviewed 2026-10-04: [Docker tmpfs](https://docs.docker.com/engine/storage/tmpfs/), [Docker logging drivers](https://docs.docker.com/engine/logging/configure/), [Linux pipes](https://man7.org/linux/man-pages/man7/pipe.7.html) and [Python selectors](https://docs.python.org/3/library/selectors.html). Their documented behavior informs the design; it does not certify the custom protocol.
