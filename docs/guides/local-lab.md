[العربية](local-lab.ar.md)

# Local lab

Updated 2026-10-06: complete current-code verification passed 1449 tests with no failures, errors, warnings or skips and independent actual review. Repairs cover SQLite and HTTPError response closure, tempfile ownership and expected test diagnostics. All 52 preparation cases also passed; native PostgreSQL startup, Cron/Vault restart continuity and physical restore remain unaccepted. See [current status](../reference/status.md) for CI results and evidence scope.

The separate public55 V3 role has actual MET: 25 component and 30 pump tests passed, original 290 and added 4 actual mode/byte captures matched, and all three known helpers were removed with no unresolved resources. Public BusyBox V8 help metadata also has actual MET: fixed-path Bash 5.3.3 and BusyBox 1.37.0 help returned zero, with its one helper removed. Neither result accepts private request operation, timeout timing/signal behavior, installed binary source provenance, candidate startup, cold/warm Vault or Cron continuity, physical restore or production.

The preserved warning-window V4 negative run reported 1447 ordinary tests OK but six ResourceWarnings, native 1 and observed window 839 for every warning. Three records have exact sqlite3.Connection type; three report tempfile.py line 484 with NoneType source. Observation windows are not allocation causes, and the three tempfile origins remain unknown. Fresh complete source/identity/mode/full bindings, final-revision CI and manually dispatched empty-host acceptance remain required; published 0e745394 CI and Website results retain only their earlier revision scope.

Use your own Linux machine as a local Docker server to evaluate Sbarbase. The current development work uses declared isolated containers on the user's computer; it does not establish a public-server deployment or an independent clean-host pilot. The full instructions live next to the code in [lab/README.md](../../lab/README.md); this page is the short version and the safety notes you should read first.

## Before you start

- **It uses real Docker containers and real resources.** The combined placement is configured for up to 5888 MiB of container memory and 5.75 CPUs, and the preflight also wants a host reserve on top. Some live probes need 4 to 6 GiB of free headroom by themselves. Check free memory first; a refusal for `host_memory_headroom` is the admission check doing its job, not a bug.
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
