# Host admission contract, SB-02

This slice implements read-only admission in the existing `local-v1` Docker profile. A passing preflight means the declared candidate has the observed prerequisites for starting verification. **Supported-profile acceptance and production remain unproven.** Clean host installation, real resource enforcement, metadata persistence, reboot and full application recovery are separate required evidence. This source slice does not close SB-03 or the complete product goal.

## Candidate and capability decisions

The initial candidate is Linux x86_64, a rootful local Docker Engine, a local Unix socket, cgroup v2, the default `runc` runtime and builtin seccomp with cgroup namespaces. AppArmor is optional; when present, the controller must use `docker-default`. Rootless, remote endpoints, Desktop, ARM, user namespace remapping, custom seccomp/AppArmor, SELinux and alternative OCI runtimes require their own evidence and stay refused for this candidate. This is a scope decision, not a claim those technologies cannot run Linux containers.

| Observation | Admission result and reason |
|---|---|
| Missing, inaccessible or malformed endpoint, data root or daemon information | Refuse with a named unavailable/invalid reason before files or Docker mutations |
| A different declared daemon root, Unix socket or checkout | Refuse the input mismatch; never create a replacement directory |
| Missing CPU, memory, swap or pids feature, or cpu/memory/pids/io cgroup controller | Refuse the missing resource prerequisite used by current placement |
| An IO backing device that current `resource_policy.io_device` cannot identify | Refuse before resource creation; report unavailable device mapping |
| A read-only host source, network or transient/layered data mount | Refuse the missing persistent local writable storage prerequisite |
| Local ext4, XFS or Btrfs with a resolvable backing device | Admit candidate observations; UID, mode, xattr, atomic rename and fsync behavior still need an owned fixture and recovery evidence |
| Classic overlay2 over XFS with absent or false d_type evidence | Refuse as unproven or unavailable, respectively; this is an actual documented overlay2 prerequisite |
| Default overlayfs containerd store or another recognized driver | Driver identity alone does not refuse admission or establish support |
| An unknown filesystem, driver or security profile | Refuse admission as unproven, with an action to provide profile capability evidence; do not label it an inherently unsupported technology |

`findmnt SOURCE` is resolved before device-number fallback. Btrfs subvolume suffixes are removed, device names are checked against kernel sysfs, and partitions resolve to their whole disk, matching the existing resource policy. Anonymous Btrfs mount numbers do not themselves establish that no disk exists. A single mapped disk does not prove multi-device filesystem enforcement, scheduler behavior or actual byte/IOPS limits. Those remain verification obligations.

The six resource booleans come from Docker's real Info API. IO support is checked through cgroup controllers and device mapping; there are no invented `BlkioReadBps` Info fields. Advertising a controller or accepting a flag does not prove actual enforcement. Existing evidence that `io.weight` does not bind remains unchanged.

Docker 29's default containerd image store can use a separate content root. `DockerRootDir` binds the persistent Docker volumes used by this runtime, not all image-store disk usage. The containerd content-root mapping and headroom are unproven here. A future verified deployment input must bind the actual daemon configuration to that root; an arbitrary path or user assertion cannot establish it. Preflight never changes daemon storage settings to fit a list.

## Inputs and the primary journey

Use `deploy/compose.sh check`, then `deploy/compose.sh up`. `up` runs the same check before its fixed `docker compose up --detach --build`. The launcher also gates `build`, `pull`, `start`, `restart`, fixed `bootstrap`, `smoke` and `upgrade <release>` commands. Bootstrap and smoke invoke the existing tools inside the selected controller. Upgrade accepts only a plain version tag and invokes the existing rebuild upgrade path; it adds no upgrade or management implementation. `ps`, fixed-tail `logs` and `down` remain available for observation and cleanup after a capability failure. Unexpected arguments are refused.

The host requires POSIX `/bin/sh`, Docker CLI with Compose, and public Linux utilities: coreutils (`timeout`, `readlink`, `stat`, `env`, `tr`), util-linux (`findmnt`), `uname`, `sed`, `awk`, `cat` and `dirname`. Python, Bun and systemd are inside or outside the primary container path as already designed; none is a host prerequisite for this launcher. Installing Docker or Linux packages is an operator prerequisite and is never performed by preflight.

| Deployment input | Public default or contract |
|---|---|
| `SBARBASE_ROOT` | This existing checkout at its canonical absolute path |
| `SBARBASE_DOCKER_PROFILE` | `local-v1` |
| `SBARBASE_DOCKER_SOCKET` | Existing `/var/run/docker.sock`, or a declared local Unix socket |
| `SBARBASE_DOCKER_DATA_ROOT` | Existing `/var/lib/docker`, or the daemon's actual `DockerRootDir` |
| `SBARBASE_COMPOSE_PROJECT`, `COMPOSE_PROJECT_NAME` | `sbarbase`; when both are provided they must agree |
| `SBARBASE_CONSOLE_PORT` | `8790`, integer 1 to 65535 |
| `SBARBASE_DATABASE_PORT`, `SBARBASE_DATABASE_BIND` | `6543`, distinct port and explicit IPv4, default `127.0.0.1` |
| `SBARBASE_PUBLIC_URL` | Empty for local use, or an explicit HTTP/HTTPS URL without credentials |
| `SBARBASE_RELEASE_SOURCE` | Empty for the canonical signed release source, or the existing public mirror input |
| `SBARBASE_BACKUP_HOUR`, `SBARBASE_BACKUP_KEEP`, `SBARBASE_UPLOAD_LIMIT_MB`, `TZ` | Existing documented deployment settings; no workstation value is inherited implicitly |

The wrapper passes only declared values to Compose, pins the local endpoint, project and absolute `compose.yaml`, and supplies `--env-file /dev/null`. It does not source or load `.env` and refuses endpoint/TLS/API and Compose-file/profile overrides. Export deployment inputs explicitly. Docker authentication uses the public Docker CLI configuration when required; credential provisioning and registry availability need separate actual installation evidence.

All checkout, socket and data-root mounts use `create_host_path: false`. These inputs select existing resources, not a promise to create or migrate Docker storage. The shell queries capability observations without creating files, volumes, networks, containers, pulling images, installing packages or changing a service/daemon. Each Docker capability query has a 15 second timeout. The Python bridge has an outer timeout and refuses absent/malformed probe results.

A user can invoke raw Docker or Compose directly. This product cannot restrict an operator with Docker access. Raw `docker compose up` bypasses host admission and is no longer the installation route. The baked controller validator still rejects incompatible host capabilities and its own endpoint, daemon root, full CID, Compose labels, security configuration, working directory and exact bind mounts before recovery, dependency installation or provisioning, including after rollback.

## Startup and installer wiring

| Boundary | Admission runs before |
|---|---|
| `deploy/compose.sh` startup/build/pull/bootstrap/upgrade operations | Any Compose Docker mutation |
| Baked `deploy/container/start.sh` via `docker_profile.py check` | Upgrade guard, dependency installation and image pulls; checked again after rollback |
| Shipped/rendered `deploy/sbarbase.service` first `ExecStartPre` | Every restart admits through the shell before the upgrade guard or leftover runtime locks and cleanup |
| Native `deploy/server-acceptance.sh` and `lab/supervised_run_check.py` mirror | Rehearsal prerequisites/effects and mirrored service guard/cleanup; declared local profile, socket and root are retained across account substitution |
| `lab/dev.py main` | State directory and supervisor/worker lock files, leftover cleanup and upgrade settlement |
| `lab/worker.py` | State directory, worker/effect/operation locks and notification worker |
| `lab/installation_runtime.py` CLI and `main('up')` | State/diagnostic directories, startup lease, settlement and source/target construction |
| `lab/durable_runtime.py` CLI and `Runtime` constructor | State/private directories, generated secrets and resource startup |
| `lab/run.py up` and CLI up | Component state, operation lock and runtime resources |
| `lab/install_server.py daemon`, `install`, `ensure_images`, `supervise` | Inventory after refusal, installer lock, private files, image pulls, dependencies and rendered/applied service unit |
| `lab/resource_policy.py device`, `lab/resource_admission.py snapshot` | Device consumption and measurement/admission; no unconfigured legacy bypass |

Stop/cleanup retains its ownership guards and can run without promotion. Native/systemd tooling remains a legacy optional route with its own Python/Bun requirements; it uses the same host admission but is not an alternative primary installer. PostgreSQL pins, native startup/private SQL/HBA, Catalog/API/UI, backup/restore and CLI MFA have not been changed.

## Replay and evidence limits

Run source-only checks without Docker:

```sh
/usr/bin/python3.14 -m unittest discover -s lab -p 'test_host*.py' -v
/usr/bin/python3.14 lab/host_caller_checks.py
sh -n deploy/host-preflight.sh deploy/compose.sh
```

Python here is the development verifier, not a host installation dependency. Tests execute the actual shell against owned fake executables, real temporary Unix sockets and an explicit projection fixture. They compare checkout/data-root trees before/after and reject unexpected Docker mutations. Declared scalar inputs reject control characters and multiline valid-prefix or valid-suffix values before parsing; numeric ports cannot alias one another through leading zeros. Negative inputs cover architecture, endpoints, cgroups, security, storage/device mappings, malformed/unavailable diagnostics and fixed launcher options. Custom project/socket fixtures exercise the whole documented bootstrap, smoke, observation, upgrade and cleanup journey. Positive fixture cases include custom paths, Btrfs SOURCE resolution, the containerd driver projection and builtin seccomp without AppArmor. The bridge/caller tests isolate deployment admission while exercising the changed interfaces and entrypoint refusal boundaries. Those inert stubs never count as host acceptance.

Required runtime verification after the coordinated role is available:

1. Bind final source and verifier image identity to an owned fixture, with a public verifier limited to 256 MiB and 0.25 CPU.
2. Record before/after resources and configured paths, exact CIDs, labels and cleanup; demonstrate real negative refusal without bypasses.
3. In a fresh owned filesystem fixture, verify UID/mode/xattr persistence, atomic rename, file/directory fsync, and resolved IO/CPU/memory/pids behavior at the actual limits. Bind evidence to device, mount and daemon identity.
4. Perform clean host install, actual Supabase startup, reboot/reconnection and full application recovery under the candidate. Preserve missing and failing cases.

A machine observation, existing image, CI recipe, fixture unit pass or this document cannot promote a host. SB-01 registry bindings and the broader roadmap remain unchanged. SB-02 source preparation can be reviewed and merged independently; complete supported-profile acceptance remains open until real evidence satisfies the contract.

## Primary sources

Reviewed 2026-10-06. These sources explain prerequisites and observed fields, not acceptance of Sbarbase on a host:

- [Docker resource constraints](https://docs.docker.com/engine/containers/resource_constraints/): kernel support and advertised Info features versus measured enforcement.
- [Docker Info API structure](https://raw.githubusercontent.com/moby/moby/master/api/types/system/info.go) and [Docker CLI Info formatter](https://raw.githubusercontent.com/docker/cli/master/cli/command/system/info.go): actual fields and Go template names.
- [Docker containerd image store](https://docs.docker.com/engine/storage/containerd/): Docker 29 default snapshotter and separate content storage.
- [Docker OverlayFS storage driver](https://docs.docker.com/engine/storage/drivers/overlayfs-driver/): XFS directory entry type prerequisite for overlay2.
- [Docker default seccomp](https://docs.docker.com/engine/security/seccomp/) and [AppArmor](https://docs.docker.com/engine/security/apparmor/): builtin/default security behavior and alternative profiles.
- [Docker contexts](https://docs.docker.com/engine/manage-resources/contexts/): endpoint selection and override precedence.
