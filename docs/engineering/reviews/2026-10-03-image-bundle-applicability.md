# Candidate image bundle applicability review

Reviewed: 2026-10-03. Scope: public registry metadata, limited read-only installed-image identity inspection and official maintenance requirements. No service image was pulled, no container or volume lifecycle operation ran, and no private configuration or secret was read. Runtime upgrade authorization is outside this slice. Registry success establishes metadata identity and published architecture availability, not service execution, binary package safety or release acceptance.

## Conclusion

All nine entries in the six candidate lock files resolve to OCI image indexes. Every index publishes Linux amd64 and arm64 service manifests. None of the currently recorded candidate `id` values is a platform-specific image configuration or a single-platform manifest. Therefore no candidate top-level pin needs replacement merely to choose arm64; every resolved child manifest and configuration is platform-specific and cannot be reused for the other architecture. A platform-qualified resolution must select its corresponding child. Attestation descriptors marked unknown/unknown are not runtime platforms.

The candidate differs materially from the selected upstream benchmark. It also records a PostgreSQL 17.6 image lineage covered by a newer official maintenance notice. Package vulnerability status and database-specific migration applicability remain UNKNOWN. Do not pass production security acceptance or automatically replace the database image from this review.

## Runtime inventory and benchmark differences

Runtime source: `lab/durable_runtime.py:337-361` loads `images.lock.json` and overrides its `db` entry with `distro-image.lock.json`, adding Storage, Realtime and Functions locks. `lab/durable_runtime.py:366-414` launches with the resulting `id`. Studio loads its two pins in `lab/studio.py:60-61` and launches them at `lab/studio.py:219-280`. `lab/release_channel.py:65-68` explicitly identifies the ordinary postgres entry as a probe image. Installer inventory retains all six locks in `lab/install_server.py:40`.

Comparison below is a tag/reference inventory against the official [self-hosted/v0.8.2 Compose file](https://raw.githubusercontent.com/supabase/supabase/564eab8ad7840b13324f68b1bfac074ef8d51c21/docker/docker-compose.yml), fetched 2026-10-03. Its SHA-256 is `8ba18fd43e9afba90e8287c8e8afc4bdda1e38d2228e57ff660968bb550da054`, matching [the frozen source identity](../benchmarks/supabase-v0.8.2.source.json), commit `564eab8ad7840b13324f68b1bfac074ef8d51c21`. Equal version labels across ECR and Docker Hub do not prove equal image bytes. The full upstream packet must also bind registry digests before comparative acceptance.

| Lock entry | Actual role | Candidate tag version | Benchmark tag version | Observation |
|---|---|---|---|---|
| images:db | Lab PostgreSQL probe, runtime override | postgres:17-alpine | No equivalent ordinary postgres service | Metadata annotates 17.11-alpine3.24; this does not upgrade runtime DB |
| images:auth | Runtime Auth | v2.196.0 | v2.196.0 | Same label, different registry names |
| images:rest | Runtime PostgREST | v14.15 | v14.17 | Different |
| distro:default | Runtime Supabase PostgreSQL | 17.6.1.166 | 17.6.1.136 | Different packaging revision |
| storage:default | Runtime Storage | v1.73.1 | v1.74.0 | Different |
| studio:studio | On-demand Studio | 2026.09.07-sha-7996410 | 2026.09.07-sha-7996410 | Same label, different registry names |
| studio:meta | On-demand postgres-meta | v0.99.0 | v0.99.0 | Same label, different registry names |
| realtime:default | Runtime Realtime | v2.138.1 | v2.134.10 | Different |
| functions:default | Runtime Edge Runtime | v1.77.0 | v1.76.2 | Different |

The benchmark also names Envoy v1.39.1, imgproxy v3.31.4 and Supavisor 2.9.12, which have no entry in these six candidate locks. This is a lock-inventory observation, not proof that candidate capabilities are equivalent or absent. Optional upstream override files belong in the full upstream source packet; this review's tag comparison covers the core Compose file only.

## Immutable identity observations

Registry requests used only `docker buildx imagetools inspect REPOSITORY@DIGEST --raw` and `--format '{{json .}}'`. All 18 top-level requests and all 18 child-manifest requests exited zero. All nine raw index byte hashes and all 18 raw child byte hashes equal their requested digests. JSON configuration metadata confirms Linux architecture for both platform children. No missing or guessed platform occurred in this candidate audit.

The `id` field equals the registry index digest. Image indexes, platform manifests and image configurations are distinct objects. On the available daemon, all nine installed public images resolve through both their repository-qualified index reference and their bare lock digest: 18 read-only `docker image inspect` calls exited zero. Each reports `Id` equal to the index, `RepoDigests` containing the same repository-qualified index, and Linux amd64. These observed IDs differ from every amd64 platform configuration digest in the table below.

The daemon reports Docker 29.8.1, overlayfs, and driver type `io.containerd.snapshotter.v1`. No container inspection or image execution occurred. The contributor packet retains the restricted fields in `local-image-identity.json`. This demonstrates why bare index hashes work as local identifiers on this image store; it does not validate the same behavior on classic stores or a fresh host.

[Docker containerd image-store documentation](https://docs.docker.com/engine/storage/containerd/), current documentation reviewed 2026-10-03, identifies containerd as the fresh-install default from Engine 29.0, says upgraded installations may retain classic storage, and notes classic storage cannot retain multi-platform indexes/attestations. [Docker pull documentation](https://docs.docker.com/reference/cli/docker/image/pull/), reviewed 2026-10-03, documents repository-qualified `NAME@DIGEST` as the immutable pull form. The public reference is already present in each candidate lock, so portability should not depend on the available daemon's index-shaped local `Id`. The classic-store failure mode is an inference from different identity/storage contracts, not an executed cross-store failure.

Acceptance should retain the repository-qualified immutable reference, resolve its platform child, and check actual runtime identity using the daemon's documented image-store behavior plus the explicit manifest/configuration relation. Comparing `.Id` directly to a configuration digest unconditionally would falsely reject this observed containerd store. [Docker image inspect](https://docs.docker.com/reference/cli/docker/image/inspect/), reviewed 2026-10-03, supports platform inspection on API 1.49 or later and refuses mismatched platforms on incapable stores. [Docker imagetools inspect](https://docs.docker.com/reference/cli/docker/buildx/imagetools/inspect/), reviewed 2026-10-03, describes remote manifest/index and image metadata inspection. Runtime use of repository-qualified refs and cross-store validation need a separate implementation slice.

All rows below have index media type `application/vnd.oci.image.index.v1+json`. Digest prefixes are not abbreviations: each complete SHA-256 is retained.

| Lock entry | Immutable repository index |
|---|---|
| images.lock.json:db | `postgres@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73` |
| images.lock.json:auth | `public.ecr.aws/supabase/gotrue@sha256:c0c25187a6b835e65a6f6e6c6b39d090e832d40e6de5186f2c038e0411944232` |
| images.lock.json:rest | `public.ecr.aws/supabase/postgrest@sha256:2f8e7b656f09db697a8875177694b417b35cb76c21370de07fc54e711e902326` |
| distro-image.lock.json:default | `public.ecr.aws/supabase/postgres@sha256:b3bfedb107413abb3b8cb0d0874b0414a1dceb3d55bc0c778de6ad22d1f7dc86` |
| storage-image.lock.json:default | `public.ecr.aws/supabase/storage-api@sha256:c24fb33cc2fa38d0f9582a30312907fc56da333fb9ba0646833186197e4982f3` |
| studio-image.lock.json:studio | `public.ecr.aws/supabase/studio@sha256:94a2a9d2906e8b4109e55159a62e241c3044709a492913ea8edd34b14973d288` |
| studio-image.lock.json:meta | `public.ecr.aws/supabase/postgres-meta@sha256:9a079ac1c94d89629262822a4bd1902d5b1be4adb464e5a0a8fd078aaed72158` |
| realtime-image.lock.json:default | `public.ecr.aws/supabase/realtime@sha256:7a6d995635f747b566079e51b1a1388dded8b2d0dfef1eda5afe98f6c9e5567e` |
| functions-image.lock.json:default | `public.ecr.aws/supabase/edge-runtime@sha256:5f555406dc0705d775323c58e27e856cca7b5b8d0ab726cc6a30b11fcfc7c6b3` |

| Lock entry | Platform | Leaf manifest digest | Image configuration digest |
|---|---|---|---|
| images.lock.json:db | linux/amd64 | `sha256:7456ef82e5f5bc43d997f4781bbd7c0d6389bff397564649a356e206ba473aee` | `sha256:1bea307dfb3ee30541a7acf7de14b58bcd6948da98e5d31a04c627c4d35ec64b` |
| images.lock.json:db | linux/arm64 | `sha256:dfc2780980fe6ca2d158bfe4342660db5e4c6431fb969088e543430d09f8d0f2` | `sha256:ff80089083d7365046af7f03d949a2defa14e0b09e14bd5b8f08a242291be8b2` |
| images.lock.json:auth | linux/amd64 | `sha256:7e813221b93fbf54b515036438550e483bfaf057b9db52fe9bc1ce91c47e817e` | `sha256:688edbb786f6d736edaa478438f23c7044a22a6a5f19502a7138d38caca6c2f6` |
| images.lock.json:auth | linux/arm64 | `sha256:93bfec0a41cfe3ed81d03c9c1c8807fd230b8ecea260cf999b8a7204ccc7d5cf` | `sha256:e88fac3e0cf252752039d92855cbfaea0960c705ad6a9c5faa1bb16d22e622b8` |
| images.lock.json:rest | linux/amd64 | `sha256:8b0f6ea9d98a9b13ac7ed331ba7084abc75b39e5222fb8b1a276b3437a307e7e` | `sha256:517a249b3cd058c5515551410da1f5ad4270d74a91fb925ee78f6a9e96fcb241` |
| images.lock.json:rest | linux/arm64 | `sha256:ee20f0a5bca1a5f8b7ffc08160543fe18d80438a144d31e64a48e81e5234d963` | `sha256:fcb9fa67dd53b3b978b52ef07fb195d4bb26434c2e8cba8c96ef257abf2ee753` |
| distro-image.lock.json:default | linux/amd64 | `sha256:d5424e9f4d0c21d63991c3f3dd77881f23f722c3a212452a61bc91924b79e8c8` | `sha256:f55775729ea7a38743f61e2bb4f9bb1e17eaaeaccb39879ae9d9d3cb1c4cb456` |
| distro-image.lock.json:default | linux/arm64 | `sha256:f20491444ff7818267e652ced923991fdab24fc07e0eb2bacfba26ab72a45563` | `sha256:d3ea5c15ee48c83936d9c9e65b8b9e60850e3b6045189ede3f5ef758e22f1dd1` |
| storage-image.lock.json:default | linux/amd64 | `sha256:215ad703189bc4fa1c0ee52b546eb2884d26d29f0d4e97c093a108398e131bf9` | `sha256:23b6e50f18d9cc8e3824ae6117b156828b202c0dbe8660cc3c8bbac6d316ab04` |
| storage-image.lock.json:default | linux/arm64 | `sha256:79ce778edd9a16594c2ad27e9c84174c5aa907c46df34c09c0cd3eddcb8d3803` | `sha256:f792394210221827b079f49ad9b73df7eaf2b4d3f2462b230f5e1f0614c3d522` |
| studio-image.lock.json:studio | linux/amd64 | `sha256:0f797270d236090c79fd26a97a1e5d1cf7ec5f36e93b9b4c3c453e1cb1aeadab` | `sha256:b31d7b34419903abd247d616f4cb6a96328829a1c6c0e48ce22e12e2aefb09de` |
| studio-image.lock.json:studio | linux/arm64 | `sha256:a844098545b1f1f897b9c0987aa50b3d3dffcc395baf77aa433dd83c9688b064` | `sha256:b296d3a09a63e971ffb495883ffc3218120473073c410d40da09755ebb01751d` |
| studio-image.lock.json:meta | linux/amd64 | `sha256:09b00cdd401f830cc8db5c7da14468e99d04a63900371b1ec06463674ac4877e` | `sha256:645aad8dfdd7fe43451263cccff8efe01b0b88c684d4d7e309933d76a7a8ea53` |
| studio-image.lock.json:meta | linux/arm64 | `sha256:472f73e565a3ba64b56b12c7706bf2f4e0c4840f3ce853b32f254127f9775aec` | `sha256:797a1e33d78057b01345f7ada3a3f096ee6322630db13ebdc631e59ceac5a5c0` |
| realtime-image.lock.json:default | linux/amd64 | `sha256:023cd658da8212c67d12eb1a43914bab67e1a3ea51f731a385ad7596d8226ec0` | `sha256:b069a8f97f0d05eadd5a18aebce08f051b3b8e07d6c120eef457f5d24f2523dd` |
| realtime-image.lock.json:default | linux/arm64 | `sha256:839743c3294d69d9eef0d2909338b40be1da1fe58114129e510dfd2fe2020c79` | `sha256:ecc1bc4f347e7565b290ddd68c0079e3e9b0f8cad7ba06d1ee686135bfaa1b97` |
| functions-image.lock.json:default | linux/amd64 | `sha256:962127775b5d90a9f18cb16fa4329e3c95cfc6b01daae135028662743ebffb16` | `sha256:5d0b18800633a0d12fcfa03e10cd95c00b5b677a3ce22edcd79206ab1f0fe0f2` |
| functions-image.lock.json:default | linux/arm64 | `sha256:ac476abea007d2c54d33ee9de3390edfe2990dc607b4f0ad8eed219c48cca28e` | `sha256:1c73de8cf174c013fc6353d276f9a9245f9f1358c92e55c107eb96623ea51c98` |

Contributor investigation packet: `.lab/candidate-applicability-fox3yn3o/`, public metadata only. `platform-configs.json` SHA-256: `bcbbf9071f9411dcadcce7f1d143921fe2cf8f99d7f9c02a8a7922039e6881e2`. The tables above preserve the identity facts in distributed documentation even if this ignored temporary directory is absent. Product verification must collect and verify these public inputs inside the distributed Linux verification image, with no owner paths, private tooling or other workstation dependency.

## Maintenance and migration applicability

The [Supabase maintenance notice](https://supabase.com/changelog/postgres-15-19-17-11-breaking-changes), published 2026-09-25 and reviewed 2026-10-03, covers self-hosted installations and describes 17.6 to 17.11. Its database-dependent requirements are:

1. In every database, use the notice's detection queries. For multibyte or non-libc collation, detect indexes on ltree and ltree arrays; separately detect B-tree ltree indexes containing values with more than 14,653 labels, regardless of encoding.
2. After upgrading, reindex affected ltree and float btree_gist indexes, including NaN cases, using schema-qualified `REINDEX INDEX CONCURRENTLY`, outside a transaction.
3. Detect pgcrypto symmetric legacy-cipher values with the wrong-key probe in the same session as its temporary helper. Public-key values require encryption-history review instead. Re-encrypt affected values using AES256 before upgrading or recover after with `ignore-cipher-failure=1`; consider secret rotation.
4. Detect non-extension custom operators using non-built-in estimators. Existing operators work, but recreation requires superuser; remove their RESTRICT/JOIN clause or use a built-in estimator for portable recreation.

The candidate PostgreSQL configurations for both architectures default to ICU and UTF-8 initialization. This makes a blanket ltree non-applicability claim unsafe, although it does not prove existing database encoding, installed extensions or affected data.

[PostgreSQL 17.11 release notes](https://www.postgresql.org/docs/17/release-17-11.html), released 2026-08-13 and reviewed 2026-10-03, say a 17.X minor upgrade needs no dump/restore, but review all intervening release notes and follow required cleanup. The new `output_plugin_libraries` allowlist defaults to pgoutput and test_decoding; inventory logical slots and any other trusted decoder before migration. This matters to Realtime compatibility. The upstream pgcrypto discussion includes 3des/twofish when OpenSSL rejects them, while Supabase's notice describes its narrower deployment behavior. Preserve and verify actual OpenSSL provider/FIPS configuration rather than generalizing either list.

[PostgreSQL pgcrypto documentation](https://www.postgresql.org/docs/17/pgcrypto.html), current 17 documentation reviewed 2026-10-03, limits `ignore-cipher-failure` to recovery of incorrectly encrypted historical messages. Correctly encrypted messages with a now-unavailable cipher need that cipher enabled, not this option. Recovered plaintext from the option has no authenticity guarantee. A tested cleanup must preserve the OpenSSL conditions under which defective data was created.

The label 17.6.1.166 and configuration history are not sufficient proof of exact packaged PostgreSQL patch/backport, extension binaries, OpenSSL or CVE exposure. No binary package inventory, extension/data query, vulnerability scan or SBOM was obtained. The notice's aggregate CVE count describes its maintenance change and cannot be assigned as a proven candidate vulnerability count. Benchmark packaging is also not production safety proof.

[Supabase updating documentation](https://supabase.com/docs/guides/self-hosting/updating), current documentation reviewed 2026-10-03, separates vendor configuration merging from Postgres and Storage backups. Its version-tracked update script and break-change prompts are upstream reference behavior, not a validated migration mechanism for this multi-database runtime. Configuration backup alone cannot recover database or object contents.

## Concrete acceptance action and limits

This review has delivered all nine candidate index identities and 18 platform manifest/configuration bindings for the next executable metadata gate. Make the gate reject missing descriptors, platform/config disagreement, digest mismatch, unresolved repository references, or substituted pins. Treat unknown/unknown attestations separately. Freeze the full upstream docker subtree and compare immutable upstream and candidate packets before any service-comparison fixture.

Before a separately reviewed runtime database upgrade, require a per-database migration packet: actual server/package/backport and extension versions, encoding/collation, operator/index results, cipher-use findings, logical-decoder inventory, and tested backup/restore including Storage objects and secrets. Run detection and cleanup in disposable restored copies with exact target image identities, then prove original Supabase contracts and Realtime operation. Retain failures and per-database non-applicability reasons. This is an owner acceptance bar, not a claim that official docs require this entire product-specific workflow.

The present result is MET for public candidate architecture metadata collection. Packaged security status, migration applicability, actual service execution, independent-host portability and release acceptance remain UNKNOWN or UNRUN. No runtime image upgrade was attempted.
