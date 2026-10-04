# Patched PostgreSQL observations, 2026-10-04

Four independent retained-artifact reviews accept distinct bounded observations on the PostgreSQL 17.11 candidate. Direct native configuration-volume preservation now passes alongside finite static, public configuration and public-script observations. No PostgreSQL server, factory initializer or key provider ran. Effective configuration, bootstrap, secure key persistence/recovery, security rollout and production readiness remain unaccepted.

## Identity and separate publication evidence

All four candidate packets bind Linux amd64 manifest/daemon image `sha256:d6844e71062a3c8e2f8021d644802850760ef970a45ac163ec8c9f0171075bda`, registry config `sha256:4011bb9bf67341889fc52b7c12f91cf602a92c948559f4aa23144b2b06f69cca` and its ordered 29 root filesystem layer identities. This is registry/daemon identity binding, not independent reproducible source-to-image attestation. The original pinned image remains unchanged.

Separately, published source `c8f286a1febfe5b33c74a3ba0f223d91545bd50c` passed [CI run 37171876490](https://github.com/M7MMAD-OMAR/sbarbase/actions/runs/37171876490): checks, Python floor and Docker install passed; empty-host acceptance was conditionally skipped. Manually dispatched [Website run 37171889234](https://github.com/M7MMAD-OMAR/sbarbase/actions/runs/37171889234) passed build/test only, without public deployment. Final offline publication verification at baked source `f6dea99d62276412e9fd0673f04a7a44759f2611e6e05c6994936239b71045be` passed six stages, 1367 Python tests, 301 Bun tests, 6440 expect calls, all 25 offsite tests and two exact source-resource absences. Its independent review is `.lab/stream-final-publication-actual-critic-20261004/review.json`. None of these results establishes candidate startup or transfers to later edits.

## Accepted finite observations

The ignored local packets below are retained evidence references, not distributed public attachments. Counts belong to their individual scopes and are not additive production coverage.

| Scope | Independent review | Packet |
|---|---|---|
| MET STATIC OBSERVATION ONLY | `.lab/patched-static-complete-actual-critic-20261004/review.json` | `.lab/patched-static-probe-build-20261004/evidence-identity-patched17-6bed7a50810141f9984c1f4e8a15bdf5` |
| MET FINITE PUBLIC CONFIGURATION BASELINE ONLY | `.lab/patched-config-baseline-actual-critic-20261004/review.json` | `.lab/patched-config-baseline-build-20261004/evidence-identity-patched17-984418744e6842d78bf4e6d22b7990af` |
| MET PUBLIC SCRIPT DATA OBSERVATION ONLY | `.lab/patched-provider-actual-critic-20261004/review.json` | `.lab/patched-provider-observation-build-20261004/evidence-identity-patched17-8252a06e760c43f0892b7a96ba5aab14` |
| MET NATIVE DIRECT CONFIGURATION PRESERVATION ONLY | `.lab/patched-direct-preservation-actual-critic-20261004/review.json` | `.lab/patched-direct-preservation-build-20261004/evidence-identity-patched17-preserve-9dc6eab5d7454856a61944c7b896cdf1` |

Static observation confirmed PostgreSQL, psql and pg_config 17.11, native PostgreSQL UID 100/GID 101 with groups 101/102, WAL-G group 102, fixed utility paths and bounded public extension control/Cron script content. Eight of nine requested control files existed; `supautils.control` was absent. Control-file presence or version does not establish extension loading or catalog grants.

The finite baseline read the main configuration, supautils, HBA, ident and exactly five default files under `/etc/postgresql-custom/conf.d`. These five regular nonsymlink files total 97 bytes, all UID 100/GID 101, mode 0644 and link count one. Main configuration text names additional includes and `/usr/lib/postgresql/bin/pgsodium_getkey.sh`; observing references does not establish complete include semantics or effective settings.

Public-script observation read the 208-byte key-provider script and 14040-byte entrypoint as data, with before/after native metadata and exact fixed key-path absence. Neither script was executed. The provider text lacks explicit atomic creation, private umask/mode handling, link admission and existing-key type/length validation. These are planning risks, not an observed key-generation outcome. Factory initializer scripts and remaining include inventory still require a separately bounded review.

## Direct native configuration-volume preservation

The accepted direct preservation packet used one owned local default-options volume mounted at the direct `conf.d` directory, four separately admitted helpers and native UID 100/GID 101 with group 101 only. Helpers had read-only roots, no network or exposed ports, 64 MiB memory/swap, 0.25 CPU, 16 PIDs, all capabilities dropped and no-new-privileges. Seed/removal mounts were writable; independent reader/final mounts were read-only.

The actual critic checked 293 comparisons across all 47 commands and 94 retained raw streams. The packet retained 110645 stream bytes and finished in 5.844 seconds within its 180-second bound. Five defaults retained content and full same-volume native metadata across six phases. Image-to-volume inode/device/timestamp equality was deliberately not claimed.

An exclusive 30-byte comment marker, mode 0644 and native owner 100:101, appeared in the seed, independent read-only reader and pre-removal observation. Removal rebound exact marker metadata and contents before unlink. The final independent read-only reader contained only the five defaults. All four helpers and the volume were removed with four full-CID absences, four name absences and one volume absence. No volume was retained for bootstrap handoff.

Native timestamp comparisons used emitted seconds. This proves bounded filesystem preservation and visibility, not power-loss durability, hostile parallel mutation resistance, crash recovery or PostgreSQL include loading. No key bytes were read and no key-provider action occurred.

## Preserved failures and remaining acceptance

All earlier design refusals and actual failed packets retain their original verdicts. The first static attempt failed before create at absent Config.User projection; the second failed before start at absent HostConfig.Tmpfs projection and needed exact owned root reconciliation; the third refused its extension-directory prefix before content reads. Later successful observations do not relabel these losses. The earlier publication record is retained in the [transport review](2026-10-04-streaming-backup-transport.md), and the [ledger](../gauntlet-ledger.json) binds the individual attempts. The direct preservation root-gate refusal remains UNRUN with zero Docker commands.

The [original configured Cron effects](2026-10-04-native-cron-effects.md) remain accepted at their unchanged 43aa source identity on the original image. They are comparison evidence, not patched security acceptance.

Before candidate bootstrap acceptance, complete the factory initializer/include inventory and prove secure native key-path permissions, generation, persistence and recovery without logging key bytes. Server startup, effective configuration, extension initialization, complete physical/Vault/object/service recovery, restart/fencing continuity, HA, PITR, capacity, deployment and production/release acceptance remain open.
