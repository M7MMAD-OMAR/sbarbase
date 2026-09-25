[العربية](local-lab.ar.md)

# Local lab

Run Sbarbase on your own Linux machine to evaluate it. The full instructions live next to the code in [lab/README.md](../../lab/README.md); this page is the short version and the safety notes you should read first.

## Before you start

- **It uses real Docker containers and real resources.** The combined placement is configured for up to 5888 MiB of container memory and 5.75 CPUs, and the preflight also wants a host reserve on top. Some live probes need 4 to 6 GiB of free headroom by themselves. Check free memory first; a refusal for `host_memory_headroom` is the admission check doing its job, not a bug.
- **One checkout per Docker daemon.** Container names are fixed for the whole server (`sbarbase-durable-*`, `sbarbase-restore-*`), so two checkouts or worktrees must not run a placement against the same daemon.
- **Never delete retained state to get past a refusal.** Do not remove volumes, `.lab/`, receipts, journals, generation pins or revocations. Startup refuses on purpose when it cannot prove what happened.
- **Some probes consume or change retained fixtures.** The `lab/*-check.py` and `lab/*-check.ts` scripts are live experiments. Read the paragraph for a probe in [lab/README.md](../../lab/README.md) before running it, and do not run probes alongside the runner.
- **Keep secrets private.** Credentials are generated into `.secrets/` (ignored by Git). Do not print them, and never pass passwords as command arguments.

## Requirements

Linux with a native Docker daemon, [Bun](https://bun.sh), and Python 3.12 or newer at `/usr/bin/python3` with the `cryptography` module (`python3-cryptography`).

## Run it

```
bun install --frozen-lockfile
/usr/bin/python3 lab/dev.py
```

`lab/dev.py` builds the console, starts the owned runtime and keeps processing provisioning jobs in the foreground. It prints a loopback URL for the console. If no operator exists yet, create one in another terminal with `/usr/bin/python3 lab/bootstrap.py` (see [operator setup](operator-setup.md)). Press Ctrl+C to stop; volumes are kept.

To run the pieces by hand instead: `/usr/bin/python3 lab/durable_runtime.py up` starts the runtime, `bun lab/upstream-server.ts` serves the console and API on loopback, and `/usr/bin/python3 lab/durable_runtime.py stop` stops the runtime.

## Tests that are always safe

These do not start containers:

```
bun test
/usr/bin/python3 -m unittest discover -s lab -p 'test_*.py'
```

Current results are recorded in [status](../reference/status.md).

## Limits

The local runner is not a production service manager. For a server, see [server deployment](server-deployment.md), which has not yet been rehearsed on a real server.
