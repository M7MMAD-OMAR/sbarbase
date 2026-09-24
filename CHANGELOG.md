[العربية](CHANGELOG.ar.md)

# Changelog

All notable changes to sbarbase. The format follows Keep a Changelog and the
project uses Semantic Versioning, which for a 0.x version means the interface may
still change between releases.

This is a source release. Nothing here is deployed as a service, no server
rehearsal has been run against it, and the repository describes itself as in
development. Read "Not in this release" before treating anything as production
ready.

## [Unreleased]

### Added

- **Browser access.** A page on any domain can call Auth, REST and Storage
  through the gateway, as on Supabase: every answer carries
  `Access-Control-Allow-Origin: *`, a preflight gets 204 without a key, and no
  credentials mode is offered. The operator login stays same-origin.
- **Upgrades with an automatic way back.** `lab/upgrade.py start` checks the new
  version, pulls its images and backs up every environment, then moves the
  checkout; the restart replaces only the Auth, REST and Storage containers whose
  pin or configuration changed. If that start fails, the supervisor moves back to
  the previous version by itself. A version that changes the PostgreSQL image is
  refused. CI upgrades PostgREST v14.15 to v14.16, then recovers from a broken
  version, on a clean machine.
- **Fair share admission at the gateway.** Each environment is guaranteed 8
  requests in flight; a busy one borrows idle slots up to 24 of 32, while the
  unused share of every environment active in the last minute (at least 8 slots)
  stays free. Checked over loopback HTTP (`docs/evidence/fair-share-checks.json`),
  not against Supabase or under sustained load.
- **Saturation notice.** After 15 minutes in a row of refusals at its limit, or of
  crowding out a neighbour, an environment raises one `environment.saturated`
  operator notification. Borrowing alone never does.
- **Telegram** as a third notification channel beside email and webhook.
- **Supabase Studio per environment.** Owners and admins start the original,
  pinned Studio and postgres-meta for one environment from the console, open it
  on its own address behind the console login, and stop it. Studio signs in with
  a per-start login that is not a superuser and exists only while it runs. CI
  uses tables, SQL, users and buckets through it on a clean machine.
- **Install with Docker.** `docker compose up -d --build` on any Linux host with
  Docker: the image carries Python 3.14, Bun and the Docker CLI, and starts the
  pinned Supabase services as sibling containers. CI runs build, first operator,
  first project through supabase-js, restart and a clean stop on a clean machine.
- **Empty-server rehearsal in a local VM.** `lab/vm-rehearsal.sh` boots a
  disposable Fedora 44 Cloud VM, installs from a clean clone with the one-command
  acceptance, creates a first project and reboots. Passed on 4 vCPU and 6 GiB
  (`docs/evidence/vm-empty-server-rehearsal.json`). Not a real server.
- **First project check.** `lab/first-project-check.ts`, and `--first-project` on
  `deploy/server-acceptance.sh`: login, project, environment, key, supabase-js
  Auth, REST and Storage through the gateway, and a refused revoked key.
- `SBARBASE_CONSOLE_PORT` pins the console's loopback port for a TLS proxy.
- CI for the unit suites, with a manual empty-host acceptance job. The Python
  suite also runs against an unreachable Docker endpoint, so a unit test that
  needs the daemon fails in review.
- **Import inspection (phase 0).** `lab/import_inspect.py` reads a Supabase
  source read-only and reports what an import would refuse, warn about or leave
  manual (`docs/evidence/import-inspect-checks.json`). The import itself does not
  exist yet.
- Management API: create an organization (owners and admins of the bootstrap
  organization), list an organization's members, retry a failed or cancelled
  environment; the console has matching screens.
- The control catalog records its schema version and refuses one written by a
  newer release.
- A recovery export records the owning organization, project and environment.
- Documentation restructured into explain, guides, reference and decisions, with
  hand-drawn diagrams, a quickstart, a server guide, `SECURITY.md`, a threat
  model, `CONTRIBUTING.md` and a roadmap.

- **Container generation migration.** `lab/migrate-generation.py` replaces a
  managed database container on the same data volume under an fsynced intent
  record, one checkpoint per phase and an explicit `--reconcile`. Five SIGKILL
  crash points pass on the disposable fixture
  (`docs/evidence/fresh-worker-generation-crash-all.json`). It refuses the
  retained placement: that run is a deliberate attended step and has not happened,
  so `lab/durable-check.ts` stays disabled and the two blocked load measurements
  stay blocked.

### Fixed

- Found by the empty-server rehearsal: the acceptance checked the console before
  building it; block IO limits named a partition, which the kernel rejects, so the
  database never started on a usual VPS disk layout; body-less POST and DELETE
  requests got a body over the real listener, so key issuance and revocation
  answered 400; image pulls ran under a 600 second timeout with no progress; a
  failed first launch was reported as a retained source to adopt.
- An owner or admin of any organization could read every organization's
  notifications, including their ids and failure reasons. Each caller now sees
  only their own organizations' events.
- CI was red on a host whose root filesystem is a partition: a test still
  expected the partition where the runtime correctly names the whole disk.
- Five admission unit tests called Docker for real and failed without a daemon.

### Changed

- The control catalog schema is now version 3: the delivery table accepts the
  Telegram channel. The migration is one way; an older release refuses a catalog
  this release has opened.
- The application gateway lends idle slots instead of refusing every request
  above 8 per environment; the overload probe keeps measuring the fixed share.
- The start requirement is derived from the placement a start runs (1792 MiB of
  limits on an empty server, plus 512 MiB per environment, plus the 2560 MiB
  reserve), and the preflight, the unit's `ExecStartPre` and the runtime use one
  computation. It was a fixed 5888 MiB in the preflight and 6 GiB in the runtime.
- CPU limits are admitted as ceilings: up to twice the cores after one core for
  the host. The old rule asked for 8 cores.
- A new environment is refused when the next restart could not admit it.
- Project names are unique within an organization and environment names within
  a project; a clash answers 409, not 500. The API refuses an environment past
  the installation limit with 409 before queueing it.
- Moving a project cancels its queued jobs in the move and points its jobs at the
  destination, so later events name the new owners.

- The two load vehicles no longer overwrite their own evidence on failure or let
  cleanup replace the real error, and the SDK vehicle probes its fixture first.
- `NOTICE` no longer describes Supabase Studio as served; it is planned.

### Notes

- A `LICENSE` file (Apache-2.0) is present in the repository. The 0.1.0 entry
  below says there is none; that entry is kept as it was released.

## [0.1.0] - 2026-09-21

The first tagged milestone. It carries the platform's isolation and accounting
work, per environment mail, operator notifications, and the measurement evidence
that says what the isolation does and does not do.

### Added

- **Resource tiers and block IO separation.** Every container is launched under a
  policy tier with a CPU weight, a block IO weight and per device read and write
  bandwidth and IOPS limits. The device is resolved from the host rather than
  written down, and a launch refuses rather than starting a container with no
  block IO separation. `lab/resource_policy.py`, `docs/engineering/RESOURCE-POLICY.md`.
- **A counted placement derived from the daemon.** Admission reads the containers
  the daemon attributes to the owner label instead of a hand written name list, so
  a component nobody told the module about is still counted, and a labelled
  container outside the recorded placement is a refusal while it runs.
- **Continuous host pressure sampling and a level 1 response.**
  `lab/pressure_admission.py` takes a bounded series over a window, summarises it,
  and refuses new admissions while the most recent reading is at or over a
  threshold, appending every crossing to a durable ledger. Every decision carries
  what is not implemented next to the action.
- **Per environment mail.** One 0600 configuration file per environment, an Auth
  environment built from it, an explicit reconcile command that recreates one
  environment's Auth to apply or remove a configuration, and a non secret state
  file. `lab/mail_config.py`, `lab/mail_state.py`, `docs/engineering/ENVIRONMENT-EMAIL.md`.
- **Operator notifications.** A durable outbox and delivery tables whose rows
  commit inside the transaction that records the state change they describe, a
  drain inside the worker, an email channel and a signed webhook channel, a
  fail-closed credential redaction gate, and eleven event kinds wired at their
  durable state changes (installation started, stopped and failed start, worker
  restart and restart limit, fence applied and released, export completed and
  failed, restore verified and failed). `lab/notify.py`,
  `lab/notification_producers.py`, `docs/engineering/OPERATOR-NOTIFICATIONS.md`.
- **Console surfaces for both.** A read only route for an environment's mail state
  and one for the undelivered notification count, with the mail state on the
  environment surface and the count in the console shell.
- **Disposable probes with negative controls.** A mail probe that proves delivery,
  an unconfigured environment, a dead relay and a wrong credential, and a
  notification probe that proves delivery, deduplication, the redaction gate and
  that a broken channel records a failure instead of changing the operation it
  reports.
- **Documentation.** Three design records, an adversarial review that states the
  unfakeable acceptance criteria, an execution plan, a plan for the deferred
  container generation migration, and the measurement evidence under
  `docs/evidence/`.
- Per environment upstream Studio presented on the administration path, and the
  console following the operating system theme by default.

### Changed

- Placement arithmetic is derived rather than listed, and the block IO rows are
  the mechanism that binds, because the weight column was measured not to.
- The unwritten `environment_mail` catalog table was removed in favour of the
  runtime's own summary file, which is the single source the console reads.
- The mail configuration comparison runs in both directions, so a key the desired
  configuration no longer has cannot survive in a reused container.

### Fixed

- Mail: the unchanged path recorded a state the vocabulary does not define, so a
  second run against an unchanged configuration raised instead of reporting. A
  source level test now walks every record call site and requires a defined state.
- Mail: a configuration an operator deleted could survive a restart and keep a
  live SMTP credential, rate limits and relay cooldown in the reused container.
- Notifications: the escalation window was the severity window, so a broken channel
  would have escalated about 72 times per six hours instead of once, and two broken
  channels could escalate each other.
- Placement: an owned container outside the recorded placement refused installation
  startup where the earlier revision admitted, and a labelled container under any
  other owner escaped the count while the module's own documentation claimed it was
  counted.
- Placement: every recovery target creation site now carries its tier label, and
  the guard that claimed every launch site passes a tier is a floor rather than an
  exact count that a new launch site breaks.
- Notification probe: its default run now exercises the fail-closed redaction gate,
  which it did not, and its summary comparison no longer re-reads a render time
  sentence that made one check a coin flip.
- Mail: a dangling symlink is refused instead of silently reading as unconfigured.

### Measured, with the evidence committed

- `docs/evidence/resource-policy-cgroup-mapping.json`: `--cpu-shares` maps to the
  cgroup v2 `cpu.weight` sublinearly (2048 asks for 174, not 800), `--blkio-weight`
  did not bind at all on this host, and `--device-write-bps` did, landing a numeric
  `wbps` in `io.max`.
- `docs/evidence/noisy-neighbor-sql-runs.json`: five runs of the neighbour
  experiment, three preserved. No repeatable effect: the median, the p95 and the
  maximum stay flat between the baseline and the phase where a second environment
  saturates the shared engine. One earlier run showed a tail spike that four later
  runs did not reproduce, and it is recorded as an outlier rather than as the
  neighbour penalty.
- `docs/evidence/pressure-response-checks.json`: a real threshold crossing on a
  disposable container, the level 1 refusal, one durable ledger line, and a return
  to admitting after recovery.
- `docs/evidence/auth-templates-source-v2.196.0.json`: what a template variable of
  the pinned Auth version does, read from the tag's own source with a digest per
  file. A template value is an HTTP URL, never a file.
- Probes re-run by the maintainer on the landed tree: mail 36 checks, notifications
  21 checks, both cleaning up what they created. Gates: 575 Python tests, 87 Bun
  tests, UI typecheck, console build check.

### Not in this release

Stated here rather than left to be discovered:

- **No server rehearsal.** The deployment readiness matrix is implemented and
  proven on a workstation, and it is not a rehearsal against real hardware.
- **The container generation migration is deferred**, so a retained database
  container cannot be recreated. The tier contract therefore applies to placements
  created after it, and the retained placement on a development host stays
  grandfathered. `docs/engineering/CONTAINER-GENERATION-MIGRATION.md` is the plan.
- **Two measurements are blocked, not merely unrun**: the arrival driven pressure
  experiment and the mixed SDK load. Their only fixture generator,
  `lab/durable-check.ts`, is disabled pending that migration, and its fixture on a
  development host names a retired environment. One load vehicle also overwrites
  its own evidence when it fails, and its cleanup replaces the real error.
- **The tier tables are uncalibrated.** The block IO rows bind and separate the
  tiers, and no experiment has measured what a tier receives under load.
- **Three notification kinds are emitted by nobody**: pressure, runtime refused and
  headroom changed. Nothing calls the pressure response on a schedule.
- **Mail is read only in the console** and the operator still chooses an SMTP
  provider. Templates are the upstream defaults, and an override has to be served
  from inside the runtime network.
- **No licence file.** `NOTICE` states the copyright and the upstream attributions,
  including that Supabase Studio and postgres-meta are used as unmodified pinned
  images and that the console is sbarbase's own work. Choosing a licence is the
  maintainer's decision and is not made here.