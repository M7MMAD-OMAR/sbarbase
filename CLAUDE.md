# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

## What this is

Sbarbase is an in-development, self-hosted administration layer for running many
Supabase projects on one host while keeping original upstream Supabase services
(PostgreSQL, GoTrue Auth, PostgREST, Storage). Hierarchy: installation >
organization > project > environment. The current candidate shares one PostgreSQL
cluster with a separate database and scoped service logins per environment, runs
original Auth/REST per environment, and shares one tenant-aware Storage process.
Independent PostgreSQL is the fallback. Operators are trusted; application
visitors are not. Each environment is meant to be administered through the
original upstream Studio (specified in `docs/engineering/STUDIO-INTEGRATION.md`, not served
yet); the console in `ui/` covers only the platform layer.

Nothing here is production ready. Docs and commits consistently avoid claiming
production readiness, fixed project capacity or automatic later-stage recovery;
keep that tone.

## Commands

Package manager is bun (never npm/npx). Python is always `/usr/bin/python3`
(3.14; a bare `python3` is a different venv and the lab code needs new f-strings).

```bash
bun install --frozen-lockfile
bun test                                   # all Bun tests in tests/
bun test tests/catalog.test.ts             # one file
bun test tests/catalog.test.ts -t "name"   # one test by name
bun run typecheck:ui                       # tsc over ui/ only
bun run build:ui                           # vite, outputs to .lab/ui
/usr/bin/python3 -m unittest discover -s lab -p 'test_*.py'        # all Python tests
/usr/bin/python3 -m unittest discover -s lab -p test_hba_journal.py # one file
```

Website (`website/`, own `package.json`, Cloudflare via wrangler; CI in
`.github/workflows/website.yml` runs only this): `cd website && bun run build && bun test`.

`lab/test_doc_references.py` fails if the operational docs (the guides,
`reference/status.md`, `reference/configuration.md`,
`reference/deployment-readiness.md`, `engineering/INDEPENDENT-RESTORE.md`,
`engineering/UPSTREAM-UPDATE-POLICY.md` and the agent handoff; the list is its
`DOCUMENTS` tuple) name a `lab/` or `deploy/` script or a `docs/evidence/*.json`
file that does not exist. Run it after renaming scripts or evidence.
`lab/test_docs_links.py` fails on any broken relative Markdown link under `docs/`,
in `README.md` or here, and on any em or en dash in the docs. Run it after moving
or renaming a document.

### Live lab (Docker) commands

Read `lab/README.md` before running anything that starts containers. The unit
tests above are safe; the `lab/*-check.py` / `lab/*-check.ts` probes are live
experiments that allocate containers, need several GiB of host headroom, and
some consume or mutate retained fixtures. Key entry points:

- `/usr/bin/python3 lab/dev.py`: foreground local runner (builds the console,
  starts the owned runtime, runs the provisioning worker). Ctrl+C stops it and
  preserves volumes.
- `/usr/bin/python3 lab/durable_runtime.py up|stop`: the durable upstream runtime.
- `bun lab/upstream-server.ts`: composed loopback API; descriptor in `.lab/upstream/server.json`.
- `/usr/bin/python3 lab/bootstrap.py`: interactive initial operator setup (never
  pass passwords as arguments).

## Architecture

Two languages with a clear split:

- **TypeScript (Bun) control plane and gateway, `src/`.**
  - `src/control/catalog.ts`: `Catalog`, the SQLite (`bun:sqlite`) control
    catalog of organizations, projects, environments, memberships and
    provisioning jobs. The central hub; most handlers take it.
  - `src/control/keys.ts`: `KeyStore`, hashed publishable-key metadata only (no
    raw keys or signing secrets).
  - `src/control/auth.ts`: management identity via a dedicated management Auth
    realm, never an application environment's Auth.
  - `src/control/http.ts`, `key-http.ts`, `handler.ts`: management API
    (owner/admin/viewer policy) and key issuance/revocation.
  - `src/gateway/`: per-environment API-key gateway in front of Auth/REST/Storage,
    with in-process concurrency admission (`concurrency.ts`, no queue), body caps,
    deadlines and drain/pause leases.
  - `src/control/application.ts` composes catalog + keys + management + gateway;
    `src/http/local-server.ts` serves it on loopback.
  - `lab/*.ts` (e.g. `upstream-server.ts`, `worker.ts`, `ui-static.ts`) are the
    runnable entry points wiring `src/` to real runtime state.
- **Python runtime and operations, `lab/`.** Container lifecycle against pinned
  images (`images.lock.json`, `distro-image.lock.json`, `storage-image.lock.json`),
  provisioning worker (`worker.py`, `provision.py`), admission gates
  (`resource_admission.py`, `pressure_admission.py`, `connection_budget.py`,
  `combined_admission.py`), HBA authority/journal/migration (`hba_*.py`), SQL
  fencing, effect receipts/leases, recovery export/restore, mail and operator
  notifications, and the server installer (`install_server.py`). Unit tests are
  `lab/test_*.py`; live probes are the `*-check.*` files.
- **`ui/`**: React 19 + Vite platform console, built into `.lab/ui` and served by
  the loopback API.
- **`deploy/`**: systemd unit, TLS proxy and `server-acceptance.sh` for a real
  server (see `docs/guides/server-deployment.md`).

Cross-cutting invariants that span both halves:

- Provisioning is crash-oriented: durable effect receipts, exact worker identity,
  and fail-closed startup. Unknown outcomes block replay; never delete pending
  receipts, journals, generation pins or revocations to get past a refusal
  (`docs/engineering/PROVISIONING-RECEIPTS.md`, `docs/engineering/SOURCE-HBA-INTEGRATION.md`).
- Retained containers are checked for image and environment drift before reuse;
  startup never recreates a database as an implicit upgrade. Only
  `lab/migrate-generation.py` may replace a managed database container.
- Container names are installation-wide constants (`sbarbase-durable-*`,
  `sbarbase-restore-*`), so only one checkout or worktree may run a placement per
  Docker daemon.

## State, secrets and evidence

- `.lab/` holds runtime state (catalogs, pins, journals, built UI); `.secrets/`
  holds credentials. Both are gitignored. Never read or print `.secrets/`, and
  never remove retained volumes or state.
- `docs/evidence/*.json` are versioned snapshots written by live probes; each
  cites its own scope. Counts in docs are per checkpoint, not cumulative.

## Where to orient

`docs/README.md` is the docs map. Current state and next step for agents:
`docs/engineering/handoff/README.md`. What to build next, in order:
`docs/engineering/plans/2026-09-23-roadmap.md`; the change workflow is
`CONTRIBUTING.md`. What works and every number:
`docs/reference/status.md`. An empty-server install is rehearsed in a disposable
local VM with `lab/vm-rehearsal.sh --image <Fedora 44 Cloud qcow2>` (needs about
9 GiB free on the host for a 6 GiB guest). Terms: `docs/reference/glossary.md`. Design in plain
words: `docs/explain/`. Why this design: `docs/decisions/README.md`. Active plans:
`docs/engineering/plans/`. Each subsystem has its own note in
`docs/engineering/` (indexed by `docs/engineering/README.md`) with scope and
limits; read the relevant one before changing that subsystem. The chronological
log is `docs/engineering/checkpoints.md` (formerly `PROJECT.md`).
`docs/START-HERE.md`, `docs/HANDOFF.md` and `PROJECT.md` are one-line pointers
kept for old references. `docs/evidence/`, `docs/design/` and `docs/diagrams/`
stay where they are; code reads and writes `docs/evidence/`.
