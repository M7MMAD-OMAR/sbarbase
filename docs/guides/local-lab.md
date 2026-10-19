[العربية](local-lab.ar.md)

# Local lab

Test counts and acceptance results change with every checkpoint; [current status](../reference/status.md) has the current ones, their CI runs and what each one proves.

Use your own Linux machine as a local Docker server to evaluate Sbarbase. The current development work uses declared isolated containers on the user's computer; it does not establish a public-server deployment or an independent clean-host pilot. The full instructions live next to the code in [lab/README.md](../../lab/README.md); this page is the short version and the safety notes you should read first.

## Before you start

- **It uses real Docker containers and real resources.** The combined placement is configured for up to 5888 MiB of container memory and 5.75 CPUs with eight environments, before Realtime or Edge Functions, and the preflight also wants a host reserve on top. Some live probes need 4 to 6 GiB of free headroom by themselves. Check free memory first; a refusal for `host_memory_headroom` is the admission check doing its job, not a bug.
- **One checkout per Docker daemon.** Container names are fixed for the whole server (`sbarbase-durable-*`, `sbarbase-restore-*`), so two checkouts or worktrees must not run a placement against the same daemon.
- **Never delete retained state to get past a refusal.** Do not remove volumes, `.lab/`, receipts, journals, generation pins or revocations. Startup refuses on purpose when it cannot prove what happened.
- **Some probes consume or change retained fixtures.** The `lab/*-check.py` and `lab/*-check.ts` scripts are live experiments. Read the paragraph for a probe in [lab/README.md](../../lab/README.md) before running it, and do not run probes alongside the runner.
- **Keep secrets private.** Credentials are generated into `.secrets/` (ignored by Git). Do not print them, and never pass passwords as command arguments.

## Requirements

For the primary container workflow, use Linux container support through Docker and a POSIX shell; see [Install with Docker](docker.md). The legacy foreground developer path below additionally needs a native Linux Docker daemon, [Bun](https://bun.sh), and Python 3.12 or newer at `/usr/bin/python3` with `cryptography` (`python3-cryptography`). Those host tools are not requirements of the primary container workflow.

## Legacy foreground developer path

These procedures run host tooling and retained runtime state. They are not the
primary container-only installation or source verification workflow.

```
bun install --frozen-lockfile
/usr/bin/python3 lab/dev.py
```

`lab/dev.py` builds the console, starts the owned runtime and keeps processing provisioning jobs in the foreground. It prints a loopback URL for the console. If no operator exists yet, create one in another terminal with `/usr/bin/python3 lab/bootstrap.py` (see [operator setup](operator-setup.md)). Press Ctrl+C to stop; volumes are kept.

To run the pieces by hand instead: `/usr/bin/python3 lab/durable_runtime.py up` starts the runtime, `bun lab/upstream-server.ts` serves the console and API on loopback, and `/usr/bin/python3 lab/durable_runtime.py stop` stops the runtime.

## Source verification in Docker

From the repository root, use the public source verification entry point:

```sh
sh deploy/verify/run.sh
```

It builds a disposable image with the public source baked in, then runs the
source checks offline as an unprivileged user with Docker init, no network,
one CPU, a 1 GiB memory cap and a 512-PID limit. It mounts neither host paths
nor a Docker socket. This public wrapper uses a writable container filesystem.
The build installs locked dependencies and needs public package access.

The command starts a verifier container and records its own failed or complete
evidence. It does not exercise Supabase services, installation, native
preparation, private transport or recovery. Read [verification scope](../../deploy/verify/README.md)
and [status](../reference/status.md) for the exact source-bound outcomes.

## Limits

The legacy foreground runner is not a production service manager. See [server deployment](server-deployment.md) for the native Linux installation path. The user's local Linux Docker server is valid for declared isolated evaluation; an independent public-server pilot remains unaccepted.
