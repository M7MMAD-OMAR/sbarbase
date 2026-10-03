# Docker portability audit

Reviewed: 2026-10-03. Scope: Linux Docker installation, host assumptions, build inputs and the official Supabase comparison baseline. This review is not production acceptance and does not change runtime behavior.

## Verified public reference

The [official Supabase Docker guide](https://supabase.com/docs/guides/self-hosting/docker) currently names `self-hosted/v0.8.2`. The [release page](https://github.com/supabase/supabase/releases/tag/self-hosted%2Fv0.8.2) resolves to short commit `564eab8`. Its [versioned Compose manifest](https://raw.githubusercontent.com/supabase/supabase/self-hosted/v0.8.2/docker/docker-compose.yml) is accessible. This is a verified reference candidate, not a claim that it is the latest release. Resolve the full commit, manifest hashes and architecture-specific image digests before recording a conformance baseline.

## Findings and limits

| Finding | Current source | Evidence and consequence |
|---|---|---|
| Docker data-root is assumed | `compose.yaml:45-47`; `lab/resource_policy.py:253,264-288,343-365` | Compose mounts `/var/lib/docker`, and runtime device discovery uses that path. Docker supports a different daemon data directory. On a host with volumes on another disk, current IO limits can target the wrong disk or refuse a valid install. This audit has not run a nondefault-data-root integration fixture. |
| Socket location is assumed | `compose.yaml:45`; `lab/install_server.py:89-107` | Compose mounts one rootful socket path. CLI context or `DOCKER_HOST` changes do not automatically change that mount. A supported rootless profile requires deliberate endpoint and enforcement verification. |
| Block IO enforcement is mandatory | `lab/resource_policy.py:350-365`; `lab/durable_runtime.py:403-407` | Device discovery failure refuses service creation. Retain this protection. Compatibility requires proof that the resource controls bind on the selected filesystem, cgroup and daemon profile. |
| Measurements assume native local Linux | `lab/resource_admission.py:61-73`; `lab/combined_admission.py:150-168`; `lab/install_server.py:183-204` | Admission reads local `/proc`, CPU counts and root filesystem free space. Desktop and remote support needs a coherent measurement boundary. Disk admission must check the filesystems that actually store persistent data. |
| Build and restart have mutable online inputs | `Dockerfile:7-13`; `deploy/container/start.sh:19-20` | Tagged build images, apt resolution and dependency installation during startup prevent a fully immutable restart contract. Publish a built, tested controller image and a versioned service bundle without weakening upgrade or rollback checks. |
| Architecture evidence is missing from the lock schema | `lab/images.lock.json:2-21`; `lab/distro-image.lock.json:2-5`; `lab/install_server.py:50-64` | Locks do not record target platform and manifest/index relationships. Image names alone do not prove native arm64 support. |
| Tests encode current fixed defaults | `lab/test_compose.py:16-23,34-38`; `.github/workflows/ci.yml:73-87` | String assertions preserve fixed mounts; one Ubuntu live job is not a Linux support matrix. Add rendered configuration and independent disposable-host checks. |

No owner workstation path was found in the reviewed runtime/build/Compose/CI files or product plan. `/home/deno` is an upstream container path. `/home/sbarbase` is a documented configurable service-account default. Neither is an owner workstation integration.

## Hostname hypothesis, resolved locally

Source inspection raised a hypothesis: the controller might have a different hostname from the daemon, causing the admission equality check to refuse Compose mode. A bounded disposable probe disproved this hypothesis on the available native Linux daemon.

The probe used an already available Alpine image, `--network host`, `--read-only`, `--cap-drop ALL`, `--security-opt no-new-privileges`, 32 MiB memory with equal swap ceiling, 0.1 CPU and 16 PIDs. It mounted no paths or Docker socket, exposed no port, performed no network request and was removed automatically. Its only commands printed the hostname and UTS/network namespace identities.

Observed: the container hostname equalled the daemon hostname, its UTS namespace differed from the invoking host process, and its network namespace matched the host process. Therefore, a separate UTS namespace does not establish a hostname mismatch. No hostname/admission change is justified by this audit. This observation covers the available native daemon only; it does not prove every Docker profile behaves identically, or prove hostname equality is sufficient locality evidence.

## Smallest independently testable implementation slice

Support a nondefault Docker volume data root without relaxing IO policy:

1. Provide one documented installation setting for the actual Docker data root, defaulting to `/var/lib/docker` for existing installations. Resolve and validate it against the selected daemon's `DockerRootDir`; refuse mismatches before provisioning. Do not infer the root from the owner machine or silently fall back to another disk.
2. Render the Compose read-only data-root mount from that setting and pass the same value into the controller. Preserve same-path semantics or explicitly map the mounted path to the daemon path.
3. Make resource-policy discovery consume the verified value. Preserve partition, subvolume and major/minor resolution, all IO ceilings and refusal on unresolved devices. Ensure its cache is associated with the selected root.
4. Update configuration tests to inspect rendered default and nondefault Compose configurations, including paths with spaces. Add hermetic tests for root mismatch, unavailable root, rejected relative paths, the selected root reaching device discovery and no unintended fallback.
5. Run default-root and nondefault-root installation/SDK/restart/restore tests on separate disposable Linux daemons. Use a separate filesystem for the nondefault root when proving IO targeting. Record the observed volume filesystem and enforced device limits. Tests against the same physical disk alone cannot demonstrate correct disk selection.

This slice must not change service tiers, remove IO flags, add privileged containers, mount the host root filesystem, or advertise Desktop/rootless/arm64 support. It establishes one portable Linux Docker capability while leaving the full portability program active.

Validation contract: read `DockerRootDir` from the selected daemon, require a nonempty absolute path, and compare it to the configured daemon-side mount source after documented normalization. Require the expected mounted directory to exist inside the controller and verify that the controller's own Docker mount record names that same source. A caller-supplied path alone is not evidence of an actual mount. An absent root must refuse discovery immediately. The follow-up safety fix below removes the former ancestor walk, so a missing Docker root cannot become a successful lookup of `/`. An unreadable daemon, missing mount, mismatched root or unresolved device must produce a named preflight failure before any container or volume creation. Cache validated discovery by daemon identity and normalized root, not one process-global device irrespective of configuration. Retain the current single-root assumption: separately mounted local volumes or volume plugins require explicit validation or refusal rather than an unproved shared-device claim.

The pure-test fixture should make `/var/lib/docker` appear resolvable while the configured root is missing; expected result is refusal and zero calls to the default-root resolver. A second fixture should make the configured root and default root resolve to different devices; every production, Studio, HBA replacement and recovery path must receive the configured root's device. These tests can prove selection and refusal behavior without Docker. They cannot establish actual kernel IO enforcement, which requires the disposable-host gate above.

The source-call graph was traced with `graft callers ... --depth all` for `io_device`, `device`, `io_flags` and `labels`. The graph did not include qualified cross-module consumers, so exhaustive `graft grep` supplied them: `lab/durable_runtime.py:406`, `lab/hba_migration.py:386`, `lab/studio.py:178`, `lab/target-hba-check.py:71`, `lab/recovery-check-services.py:59`, `lab/recovery-check-storage.py:66`, and `lab/recovery-restore-db.py:146`. They must continue receiving the same enforced policy through the shared helper.

## Public constraints

- [Docker daemon data directory](https://docs.docker.com/engine/daemon/), reviewed 2026-10-03: `data-root` is configurable. Engine 29 fresh installations may store image contents and snapshots under `/var/lib/containerd`, while other daemon data such as volumes remains under Docker's data root. A volume-data-root fix does not establish accounting for every Docker disk consumer.
- [Docker bind mounts](https://docs.docker.com/engine/storage/bind-mounts/), reviewed 2026-10-03: bind sources belong to the daemon host, not the Docker client. This constrains remote and Desktop path assumptions.
- [Docker host networking](https://docs.docker.com/engine/network/drivers/host/), reviewed 2026-10-03: native Linux Engine supports it; Desktop 4.34 and newer requires enabling it and has additional limitations. Only Linux containers are supported by this networking profile.
- [Docker rootless resource controls](https://docs.docker.com/engine/security/rootless/tips/), reviewed 2026-10-03: documented cgroup-related resource controls require cgroup v2 and systemd. Resource-control enforcement remains an independently tested requirement.

Windows development should continue to use Linux containers under Docker's Linux environment. Host integration differences should be represented as tested capabilities or profiles, not a separate Windows-native Supabase server implementation.

## Implemented missing-root safety sub-slice

Follow-up on 2026-10-03: a real temporary missing directory and an existing file both returned a plausible parent device before this change. `io_device` now requires its selected path to be an existing directory before calling either mount-source or device-number discovery. It returns `None` for an absent directory or file; the existing shared IO policy refuses unresolved devices.

Red-first validation exposed two failures. After the minimal change, all 39 resource-policy tests passed without skips. The regression fixtures check zero resolver calls for missing/file inputs and exact targeting of an existing custom directory containing spaces. No real Docker daemon was changed. Default-root, endpoint, mount verification and daemon-scoped caching remain pending; this fix does not establish full G1 support or kernel IO enforcement.

The remaining mount contract must use long Compose bind syntax with `create_host_path: false` for socket and data root. A missing input must not be silently created. The selected endpoint must be consistent for inspection and mutation: Docker documents that `DOCKER_CONTEXT` overrides `DOCKER_HOST`. These facts need rendered configuration, refusal and disposable-host tests before support claims. [Compose bind settings](https://docs.docker.com/reference/compose-file/services/#volumes), [Docker CLI environment precedence](https://docs.docker.com/reference/cli/docker/#environment-variables), reviewed 2026-10-03.
