# Pinned source inventory and supplemental metadata verifier

This implements a standalone integrity path for the [reference acceptance plan](supabase-v0.8.2.reference-acceptance-plan.md). It changes no original packet, registry, capability verifier or runtime code. The [machine-readable contract](reference-supplement-contract.json) defines exact commands, fields, budgets, future roles and the proposed integration boundary. [Implementation](../../../deploy/verify/reference_supplement.py), [offline tests](../../../lab/test_reference_supplement.py).

The original remains `self-hosted/v0.8.2`, commit `564eab8ad7840b13324f68b1bfac074ef8d51c21`. A fresh public anonymous Git metadata observation yielded two HTTP requests, 890,642 retained bytes and a hash-verified raw commit. Its Git root tree is `8d5b6befca58030fd783d7fdad0b97ad6d5bced0`; raw commit SHA-256 is `68afecfaa0283b429df80047aa66518287d801bf7c2cdd02e98b9e686d45de59`. No working-tree checkout or build occurred. Git metadata came from a new empty directory with global/system config and credential helpers disabled, lazy blob fetching disabled, a 45-second deadline, 16 MiB disk ceiling and eight observed-request ceiling. Its logs remain outside source. This observation is distinct from the new archive collector and does not establish full build-input closure.

## Source collection and verification

Provide a hash-verified public raw Git commit, explicit budget JSON, and a new owned output directory. `collect-source` verifies the anchor before connecting. It writes its declaration before a single anonymous GET from the exact commit's public codeload URL. No redirect, retry, proxy, authentication, image layer or installer execution is permitted. Ambient custom trust settings refuse. The finite caps are one request, 64 MiB compressed archive, 512 MiB expanded tar stream, 16 MiB per header/blob, 50,000 headers, 128 MiB owned disk, 90 seconds overall and 10 seconds per socket operation. The collector has a process alarm as well as byte/time checks. Run it inside the declared OS sandbox; the alarm is not a memory, process or filesystem sandbox.

The archive is never extracted or executed. Every regular file contributes its Git blob identity, SHA-256, size and Git mode. Relative internal source symlink bytes can contribute mode 120000, but escaping links, hardlinks, devices, unsafe modes, duplicate/conflicting paths and unexpected root identities refuse. Recursive Git tree reconstruction must equal the root recorded in the hash-verified original commit. Missing/extra files, changed modes or bytes therefore refuse. Gzip checks, bounded tar headers and zero-tail checks prevent silently accepting a hidden archive suffix. The packet records a complete immutable source inventory, not an observed working tree.

The whole Git inventory includes public Studio source and workspace inputs, beyond the original 64 docker files. It still does not resolve external base images, package indexes, build-time tools or authenticated publisher provenance. `build_inputs`, build, runtime, security, acceptance and production stay unproven or unrun. A source packet cannot remove those gaps or promote states. Full REF-SOURCE acceptance remains open until its complete build-input closure is verified independently; subsequent actual metadata/distribution roles stay queued behind that requirement.

For an offline repeat, use:

```sh
python3 -m deploy.verify.reference_supplement verify-source --packet packet/source.json
```

All packet paths are relative to their packet directory. Readers reject symlinked components, nonregular or hardlinked artifacts, changed files, excessive bytes, duplicate JSON keys, float/nonfinite numbers and extra or missing schema fields. Bool is never an integer count. Raw source/archive/commit bytes and the complete inventory are rechecked, not trusted from a claimed successful status.

## Supplemental image and configuration integrity

The supplement references a copied original packet by exact SHA-256 and a separately verified source packet. All original 64 docker file records must agree. The unchanged original packet preserves 14 Compose documents, five auxiliary YAML configurations, 23 references and all ten diagnostics. Every reference requires both linux/amd64 and linux/arm64 records. A previously frozen root, platform or config digest cannot silently change.

For each image retain raw index/single manifest, platform manifest and config blobs. Verify descriptor media type, size and raw SHA-256 at each edge, exactly one platform match, and actual config os/architecture. Config labels cannot authenticate a source revision. This module intentionally supports no verified publisher/provenance claim; its provenance object must remain unverified with empty evidence. It downloads no layers and executes no image.

Every raw index descriptor, including additional architectures and attachments, is checked for media type, digest syntax, integer size and optional field types. Platform descriptors require string os/architecture when present. Every image manifest requires its config descriptor and a layers array; every layer and optional subject is checked. Valid unselected descriptors remain retained without a content verification claim. Embedded descriptor data must agree with its size and SHA-256 or SHA-512. These checks follow the [OCI descriptor](https://github.com/opencontainers/image-spec/blob/v1.1.0/descriptor.md), [manifest](https://github.com/opencontainers/image-spec/blob/v1.1.0/manifest.md) and [index](https://github.com/opencontainers/image-spec/blob/v1.1.0/image-index.md) contracts. Selected platform manifests and config content must use supported image/config media types and match retained raw SHA-256 bytes. Layer bytes and unselected descriptor content remain unobserved.

```sh
python3 -m deploy.verify.reference_supplement verify-supplement --packet packet/supplement.json --gate metadata
python3 -m deploy.verify.reference_supplement verify-supplement --packet packet/supplement.json --gate distribution
```

The first command can exit zero only for complete source and raw metadata graph integrity. That is not complete REF-SOURCE or distribution acceptance. The distribution command refuses because actual build/provenance/startup/native/security admission is absent. Partial new observations cannot replace required config bytes or source closure. The module writes no accepted registry row or second ledger. The existing foundation verifier still rejects the original unresolved packet.

## Exact integration and execution handoff

A later root change may add a separately reviewed adapter that binds the new source/supplement/baseline and schema/code identities into the capability verifier. It must preserve original diagnostics, explicitly adjudicate parser/build-only applicability and require actual complete foundation runtime tests. A boolean supplemental flag cannot bypass `packet_errors`. This delivery proposes that path and implements its standalone integrity prerequisite; it installs no adapter and changes no original binding.

Before any public archive collection, freeze actual command and source hashes, the explicit budget and verified anchor, output ownership and cleanup/promotion rules. The concrete next role is an isolated source verifier with readonly source/toolchain, empty allowlisted environment, one declared public HTTPS request, 256 MiB memory, no swap, 25% CPU, 32 processes, 128 MiB writable owned space and a 90-second wall limit. It must retain status/refusal and artifact hashes, independently inspect the full Studio material closure, and refuse when caps or inputs are missing. Source collection is allowed; build, Docker, image layers and runtime are not.

Offline tests run without network or host credentials using bwrap readonly source, isolated PID/network namespaces, 16 MiB scratch plus 8 MiB temporary files, file/descriptor/core limits, systemd 256 MiB/no-swap/25% CPU/32-process caps and the checkpoint's 180-second timeout. The observed interpreter is Python 3.14.7; older interpreter branches have not been independently observed. Synthetic fixtures establish parser/refusal behavior only. Initial failed source checks are retained alongside corrected evidence, never rewritten as passes.

Queue a future metadata/runtime role only after complete REF-SOURCE closure and a new reviewed resource/fixture/schema contract. It needs all 23 original references and raw platform/config chains, publisher or reproducible build evidence, redacted effective configuration and actual startup/native/SDK/recovery observations in a coordinated slot. The twelve SB-03 applicability/patch/application/recovery cases remain mandatory. Unit/source/metadata integrity cannot close G0, SB-03, G12 or production.
