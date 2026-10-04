# Patched factory data, whole configuration preservation and root key source

Recorded 2026-10-04. These are separate source and public-data fragments. A later [native filesystem review](2026-10-04-native-root-key-filesystem.md) accepts key publication and metadata guards only. PostgreSQL 17.11 startup, provider wiring, Vault continuity, physical recovery and production readiness remain unaccepted. The original image pin and its bounded Cron evidence are unchanged.

## Public data and configuration preservation

All candidate observations bind immutable Linux amd64 PostgreSQL 17.11 manifest `sha256:d6844e71062a3c8e2f8021d644802850760ef970a45ac163ec8c9f0171075bda`. No source-to-image attestation or security certification follows.

| Accepted scope | Independent checks | Exact result |
|---|---:|---|
| Corrected finite factory data | 631 | Three include files, 840 bytes;64 initialization objects,61 regular files; key absent |
| Fixed initializer text, read as data | 1193 | All 61 files, 67274 bytes; custom root33 objects/13 dirs/20 regular files, no links |
| Fixed custom text, read as data | 745 | All 20 files, 18544 bytes; optional `/etc/postgresql.schema.sql` absent before/after |
| Native whole custom-root preservation | 1256 | Original 20 file bytes/metadata and 12 untouched subdirs preserved across six phases; eight exact helper absences; one positively retained baseline volume with no consumers |

Whole-root experiment `2f30af25973c45e69f0ed281d0230102` used native UID 100/GID 101, one owned local volume and four separate helpers. It took 9.674947395 seconds, 47 commands/94 streams/446537 retained bytes within 180 seconds and the declared 64 MiB/.25 CPU/16 PID/network-none envelope. An exclusive 30-byte marker was independently read, rebound before removal and absent in the final phase. Root time/size changes were allowed only at the marker create/unlink transitions. This was prepared public-baseline handoff, not fsync/powerloss durability or bootstrap authority. Renew ownership, consumers and complete baseline before any future use.

The earlier direct configuration proof removed its volume. Its nine exact absences still describe that earlier packet; the later whole-root proof explicitly retained a different volume and does not claim its absence. No candidate provider, initializer, SQL or server ran.

Data review found the provider generates a new key when its fixed file is missing, and the initializer can interpolate passwords while the default server logs DDL. Future bootstrap must protect the key before every launch and establish effective secret-safe logging/statistics settings before sensitive SQL. Vendor-local child psql password arguments remain a documented private-PID limitation. Text review is not runtime proof.

## Root key primitive, source acceptance only

[`lab/native_root_key.py`](../../../lab/native_root_key.py) opens an absolute parent through anchored nofollow descriptors. The caller must separately admit its immutable runtime and exclusively owned local volume. Parent ownership/modes 700/750/755 must match the native caller; group/other writable parents refuse. Same-UID adversarial writers and network filesystems are outside this primitive's scope.

Cold publication refuses any existing fixed key or pending artifact. It generates 32 random bytes internally, writes a private 0600 temporary file, verifies 64 lowercase hexadecimal characters and ownership, fsyncs it, publishes with an atomic no-replace hard link, fsyncs the parent and removes only the identity-bound temporary sibling. There is no overwrite/rekey fallback. Publication or close failure returns a sanitized nonzero reconciliation reason; a valid published file can remain after failure and must not be silently adopted or regenerated.

The guard requires a regular 0600 native-owned file, one link, 64 bytes, bounded read and unchanged descriptor/namespace metadata including nanosecond times. An optional metadata witness rejects drift. No key bytes or key-derived fingerprint are emitted. A wrong but valid key requires comparison against an already encrypted Vault sentinel; format/metadata alone cannot establish restore continuity. This primitive is not wired into candidate entrypoint/startup yet.

Independent source review accepted the corrected primitive and 28 meaningful unit tests after preserving two initial blockers. Baked source `54418ff32151847af9785d7ac492559e072ae6d57828f04ea154df81d3400c0a` passed six stages: 1395 Python tests with no skips, 301 Bun tests, UI typecheck/build and compile. Exact regular baked module 9107 bytes/test 14681 bytes were captured from the just-tested full CID and matched the independent frozen-v2 copies; both capture streams were empty. Exact generated container and image tag absence was independently checked. These are source/filesystem unit results in the verification image, not a native 100:101 key-volume or PostgreSQL result.

## Retained scope and failures

Raw packets are ignored local-only artifacts, not distributed evidence:

- Factory: `.lab/patched-factory-inventory-v2-build-20261004/evidence-identity-patched17-a6d79216db59421ba9e22bfbd5ee71e0`; review `.lab/patched-factory-corrected-actual-critic-20261004`.
- Initializer data: `.lab/patched-initializer-data-build-20261004/evidence-identity-patched17-7e797dab4469435c9c23f17d3bda1cf8`; review `.lab/patched-initializer-data-actual-critic-20261004`.
- Custom data: `.lab/patched-custom-data-build-20261004/evidence-identity-patched17-1b7221a1bc0249679249fb0ec380a982`; review `.lab/patched-custom-data-actual-critic-20261004`.
- Whole preservation: `.lab/patched-whole-preservation-build-20261004/evidence-identity-patched17-preserve-2f30af25973c45e69f0ed281d0230102`; review `.lab/patched-whole-preservation-actual-critic-20261004`.
- Key source: `.lab/native-root-key-capture-verification-20261004/run-20261004T045817Z-2582809-0`; review `.lab/native-root-key-capture-actual-critic-20261004`.

Preserve the original factory ordering refusal, whole-root metadata design refusal, first key source refusal and first actual source byte-binding refusal. The corrected factory ordering, readonly root metadata equality and explicit baked public-file capture were separately reviewed. The first byte-binding packet retains its successful test observations and NOT_MET verdict. The same independent nonbuilder reviewed separate design/actual scopes because new agent threads were unavailable; no new reviewer identity is claimed.

Latest previously published source `3b2e32dfcebc71ef1911f1eb964bbf77b8352ed7` passed [CI 37179850916](https://github.com/M7MMAD-OMAR/sbarbase/actions/runs/37179850916), with empty-host acceptance conditionally skipped, and build-only [Website 37179873086](https://github.com/M7MMAD-OMAR/sbarbase/actions/runs/37179873086). Later edits need fresh final source and publication checks. The owner chose this computer as the isolated local server; no external server purchase is a prerequisite. Independent-host portability, physical HA, load capacity and full production acceptance still require their own evidence.
