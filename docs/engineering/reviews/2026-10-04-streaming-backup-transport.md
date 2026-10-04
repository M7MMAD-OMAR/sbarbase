# Streaming backup transport, 2026-10-04

Verdict: MET OFFLINE TRANSPORT REGRESSIONS ONLY. The independent retained-artifact review accepts the offline transport regressions at baked source `53ce0999acb9b1c03305e2218bba55e61117bdc19d795a1dd25ef0ebe3ebf762`. All six source stages passed: 1367 Python tests, 301 Bun tests, 6440 Bun expect calls and all 25 offsite tests. Python output does not count runtime assertions, so its test count is not an assertion count.

Tests cover actual 4 MiB chunk boundaries at zero, CHUNK minus one, CHUNK, CHUNK plus one and twice CHUNK plus seventeen bytes, fragmented reads, bounded reads, late authentication failure and corrupted or truncated input. Decryption/fetch uses 0600 files in private 0700 staging directories, verifies before final publication and preserves ownership during cleanup. Linux RENAME_NOREPLACE refuses an existing destination, concurrent destination creation and dangling symlinks; unsupported atomic no-replace behavior fails closed.

Publication is atomic within this scope. It is not a guarantee of durable success under every failure: fsync or cleanup errors after validated publication can raise while the output is already visible. Optional byte bounds do not impose an automatic fetch quota. No immunity to adversarial concurrent local mutation is claimed.

## Evidence scope

Local ignored evidence, not distributed public attachments:

- Source packet: `.lab/stream-transport-source-verification-20261004/run-20261004T020600Z-3467839-0`.
- Actual review: `.lab/stream-transport-actual-critic-20261004/review.json`.
- Second design review: `.lab/stream-transport-design-v2-critic-20261004/review.json`, MET DESIGN ONLY.

The first NOT MET design review is retained. The later correction repairs its cleanup-injection test and adds actual chunk-boundary coverage. Nine literal FAIL messages from negative diagnostic fixtures remain raw and paired with passing tests. Exact source container and tag absences passed. This does not establish physical/native restoration, a remote S3 provider, complete Vault/object/feature recovery, PITR, HA, release or production acceptance. The [ledger](../gauntlet-ledger.json) records source-bound attempts; later documentation publication needs fresh checks.

## Separate PostgreSQL 17.11 candidate

All 29 public immutable layers matched: 367963126 compressed bytes and 1273654272 decoded tar bytes. Immutable pull v2 passed in 458.398 seconds within 600, with manifest identity prefix `d6844e`, exact repository digest, Linux amd64 identity, all 29 root diff IDs and selected installed size 1698105192 bytes. This accepts the recorded pull identity, not candidate startup.

The first static metadata attempt failed before create because the direct Config.User expression could not access the absent key. Its independent actual review, `.lab/patched-static-refusal-actual-critic-20261004/review.json`, remains NOT MET. No static payload, server or container was created. A second static attempt passed image admission and created an owned helper, but its container projection refused the absent HostConfig.Tmpfs key before start. Helper cleanup also refused that projection. Independent root ownership admission removed the created, non-running helper and proved exact CID/name absence; the failed packet remains failed. A third static attempt passed actual image/resource admission and the timeout guardian, observed PostgreSQL/psql/pg_config 17.11 and native account records, then exited 85 at strict extension-directory prefix admission. The directory values were not printed, so neither an alternate root nor content acceptance is established. Its independent actual review `.lab/patched-static-path-actual-critic-20261004/review.json` remains NOT MET; exact owned CID/name cleanup passed. Static completion, candidate bootstrap and patched security-profile acceptance remain unestablished.

The [original configured Cron review](2026-10-04-native-cron-effects.md) remains accepted at its own 43aa baked identity, with four Cron writes and five HTTP echoes on the original PostgreSQL 17.6 image. That comparison evidence is unchanged and does not establish patched production security.

Latest prior published source `2366c8279f2eb25bc174620817f79a959181e1f8` passed checks, Python floor and Docker install in [CI run 37168780889](https://github.com/M7MMAD-OMAR/sbarbase/actions/runs/37168780889); empty-host acceptance was conditionally skipped. Unchanged website paths passed the manually dispatched build-only [Website run 37170014835](https://github.com/M7MMAD-OMAR/sbarbase/actions/runs/37170014835). These prior publication results do not transfer to pending changes or establish full native activation, recovery, all-host portability, physical HA, capacity or production readiness.
