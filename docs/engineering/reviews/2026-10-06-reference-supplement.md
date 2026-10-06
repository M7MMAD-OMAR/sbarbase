# Reference supplement source review receipt

## Result and boundary

The standalone implementation met its source integrity and refusal contract after two fresh final independent reviews. The checkpoint engine accepted local checkpoint `core` at revision 24 with delegated approval. This accepts the implementation contract only. Complete REF-SOURCE external build-input closure, authenticated provenance, build, runtime, security, distribution, G0, SB-03, G12 and production remain unproven or unrun. No root capability acceptance transfers from this receipt.

Only new standalone implementation, tests, contract documents and this receipt are delivered. The original packet, acceptance plan, registry, capability verifier, runtime, installer and lifecycle files were unchanged.

## Frozen identities

- Authoring base: `de70770277ab9864097fb4a807d8a6de069ecc03`.
- Checkpoint source hash: `8e253b4b87e531ec8b42bfd92d9e70ced083f12f1a26a6f7cfd3170a2a886165`.
- Plan hash: `9e9ee057b36b0a5836b55a9dd45debda41d1b6008892308cf1273380a9b834d0`.
- Four-file ordered object delivery identity: `9a47bb36218793042a99cb4199f9c1423ccb60626e82961e66b08a2fc6351784`.

The ordered identity is SHA-256 of UTF-8 JSON for the following ordered object rows, with sorted keys and compact separators. It covers the four implementation/contract files, excluding this later receipt. The checkpoint source hash additionally binds the unchanged original reference and registry inputs in its scoped snapshot. An import into another root needs a new current-root binding and review, without carrying production proof.

| Path | Bytes | SHA-256 |
| --- | --- | --- |
| deploy/verify/reference_supplement.py | 29963 | `f8e84c3985980247e15450896d8bb7f1aa881edaa001cc89c10d79dfcf9e5ca8` |
| lab/test_reference_supplement.py | 25725 | `139c46c118e4f74405534ae303fa25786242948748429bb13f614d392ca52719` |
| docs/engineering/benchmarks/reference-supplement-contract.json | 7957 | `303520c4bbc1d12bd125e4da2400fc60ebe44bb798aecfe1159e1754b901a0ed` |
| docs/engineering/benchmarks/reference-supplement-contract.md | 8808 | `f452ed7fa4cda4067da97beadedd11b1b1c81c24b1e409fcad38cf92083a0dc9` |

## Actual checks and independent reviews

The final declared behavior check executed 70 tests, zero failures and zero skips. Its retained 8,365-byte log SHA-256 is `a6a3dbd553a240051eff147965f3154ce4cbd928b99f41d561de174e498e499b`. The exact command, parser, counts and artifacts are in the checkpoint evidence outside source.

The specification reviewer independently ran the same 70 tests and 25 discriminating probes. The standards reviewer ran the same 70 tests and 44 probes. Both final reports passed with no findings; both independently checked the source and plan hashes before and after. These are separate source reviews, not live archive or image observations.

| Reviewer | Axis | Verdict | Raw report SHA-256 |
| --- | --- | --- | --- |
| supplement-spec-v1 | specification | fail | `c6ed0252f5c291f648e1d65f187f92b6f0dac9a532a0d3afcb4f9335e93e29ea` |
| supplement-standards-v1 | standards | pass | `aed20686bd23df4ec301fea3f2d0227a75697503263cf126ced8b9ab54a2200c` |
| supplement-spec-v2 | specification | fail | `3015dcf2d64461a483229b5cb01ce2982bc2cc3bf5d5919145623be71d750d14` |
| supplement-standards-v2 | standards | pass | `1bd8a0cb1669d8c24e4aedb157c220a1e816815178fb6e6bb44e99a1535e6cf1` |
| supplement-spec-v3 | specification | pass | `970b1a268774ed6e247d44bbbe999a74ce7f4a3a0feb1996790fa544e28fdd49` |
| supplement-standards-v3 | standards | pass | `e600f9f7b34b4587781b4a56299d1bdf8f52c00f89db88b5b92888f070e22a01` |

Earlier specification failures are retained. The first found malformed layer and unselected index descriptors accepted despite valid raw hashes. The second found an 8-byte symlink blob accepted under a declared one-byte file cap. The implementation now validates every descriptor/layer, preserves valid additional platforms/attachments without claiming their content verified, and checks every encoded source blob and recorded size against its declared cap. Discriminating regression cases pass. Historical failed checks, invalid probe attempts, corrected commands and review artifacts were preserved, never rewritten as passing evidence.

Checks ran in a bwrap sandbox with readonly source/toolchain, isolated PID and network namespaces, empty allowlisted environment, 16 MiB scratch plus 8 MiB temporary files, file/descriptor/core limits, systemd 256 MiB RAM/no swap/25% CPU/32 processes and an external 180-second check deadline. The observed interpreter was Python 3.14.7; older interpreter branches are unobserved.

## Public source metadata observation and next role

The prior bounded public Git metadata fetch used two HTTP requests, retained 890,642 bytes and hash-verified the exact pinned commit. It did not check out or execute target source. A subsequent offline `git ls-tree` with lazy fetch disabled enumerated 17,535 blob paths, including 5,255 Studio paths and no gitlinks. Its canonical metadata inventory SHA-256 is `d3e6be7570c46e0ee8caa1527c7eebe9419f859806e8f04729776be0b86df2b8`. This is an object/name inventory; complete source blob bytes and external build inputs have not been observed here.

The next isolated source role is queued with the coordinating root. Its reviewed CLI and fixture contract are in [the machine-readable contract](../benchmarks/reference-supplement-contract.json) and [execution explanation](../benchmarks/reference-supplement-contract.md). Freeze the exact launch/source/input hashes and coordinated resource slot before execution. The authorized collection is one anonymous request to the exact pinned public source URL: 64 MiB compressed, 512 MiB expanded, 16 MiB per header/blob, 50,000 headers, 128 MiB owned disk, 90 seconds overall and 10 seconds per socket operation. No redirects, retry, credentials, private config/cache, image layers, Docker, build or runtime is allowed.

Prefer a trusted fixed-URL bounded fetch followed by offline isolated target parsing, rather than broad target network access. Retain request status, refusal/partial bytes and artifact identities separately; no failed collection yields an accepted source packet. Independently repeat archive/tree/inventory checks offline and inspect the entire Studio workspace, lockfiles, patches, framework and build target. Full external base/package/tool/provenance closure stays open until independently established. Dependent metadata/distribution/runtime roles remain queued behind that complete closure.

## Evidence location

All actual commands, output logs, raw independent reports, engine journal/status/resume and public metadata artifacts remain under:

```text
/home/sbarah/.codex/sbarbase-integration/20261006/reference-supplement/
```

The final reviewer directories are `supplement-spec-v3/` and `supplement-standards-v3/`. Earlier `v1` and `v2` directories remain historical. Draft source-role proposals bound to earlier code are stale and must not be executed against this final source without a fresh command/input binding. The source archive collector has not performed a public GET in this receipt.
