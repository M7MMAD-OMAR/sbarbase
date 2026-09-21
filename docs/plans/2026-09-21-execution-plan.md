# Implementation plan: resource distribution, environment email, operator notifications

> Execute task by task, verify each with the command named in the task, and commit only that task's paths. Builders run in isolated worktrees and land by harvesting paths, never by merging their branch wholesale.

**Goal:** make one server carry several projects honestly. Three outcomes: a written and enforced resource-distribution policy, per-environment application email through original Supabase Auth, and operator notifications for the events that currently pass unseen.

**Architecture:** each outcome sits on what already exists. Resource policy changes container creation limits and the admission arithmetic. Email adds variables to the Auth environment the runtime already writes. Notifications add a durable outbox to the SQLite control catalog that the worker already owns.

**Tech stack:** Bun and TypeScript for the control plane and gateway, /usr/bin/python3 for the lab runtime, pinned original Supabase images, no new dependency.

---

## Context, and the honest starting point

The user's question was: on one server, when one project is under load, does it take the resources it needs while its neighbours stay unaffected?

Verified today, from the code:

| Control | Value | Where | What it does not do |
|---|---|---|---|
| Container memory | `--memory` equal to `--memory-swap` | `lab/durable_runtime.py:134` | no relative share, no swap headroom |
| Container CPU | `--cpus` (hard quota) | `lab/durable_runtime.py:134` | quota only, no weight, so an idle environment cannot lend capacity |
| Process cap | `--pids-limit 128` | `lab/durable_runtime.py:134` | nothing about IO |
| Block IO | none | not found anywhere in `lab/` or `src/` | a heavy writer can starve a neighbour's disk |
| CPU weight | none | not found | no priority between production and experiments |
| Placement ceiling | `MAX_MEMORY` 6144 MiB, `MAX_CPUS` 6, `RESERVE` 2560 MiB | `lab/combined_admission.py:11-13` | computed over an explicit container name list (`:29-31`) that does not count any container the list omits |
| Admission gates | memory and volume headroom, cgroup pressure, connection budget | `lab/resource_admission.py`, `lab/pressure_admission.py`, `lab/connection_budget.py` | evaluated at allocation time only, never continuously |
| Gateway | in-process admission, no queue, REST cap 3, 1 MiB body cap, 15 s timeout, 408/504 deadlines, per-environment pause leases | `src/gateway/concurrency.ts`, `src/gateway/handler.ts` | no per-tenant rate or bandwidth shaping |
| SQL bounds | statement_timeout 8 s, transaction_timeout 12 s per login and database | `docs/SQL-DEADLINES.md` | does not bound CPU or IO of an allowed statement |
| Auth email | `GOTRUE_EXTERNAL_EMAIL_ENABLED=true`, `GOTRUE_MAILER_AUTOCONFIRM=true`, and no SMTP variable at all | `lab/run.py:123-130` | no mail is ever sent, so signup is silent and password recovery does not exist |
| Operator notifications | none. `audit_events` records 211 events, the console renders a safe failure reason | `src/control/catalog.ts`, `ui/Environments.tsx` | the operator must look to learn anything |

The repository states the limit in its own words: "This does not isolate query CPU, memory or I/O", "CPU/I/O, quotas and continuous pressure monitoring remain unfinished", and "Continuous overload response ... remain open".

## Decisions taken, with the evidence that drove them

1. **Fair distribution is achieved by weights and IO limits, not by a scheduler.** Evidence: the runtime already sets hard ceilings per container, so the missing half is relative share, which the kernel provides through `cpu-shares` and `blkio-weight`. A userspace scheduler would duplicate the kernel and add a failure mode with no evidence behind it.
2. **Two named tiers, production and experimental**, carried as explicit per-environment values rather than inferred. Evidence: `environments` rows already carry a name and a runtime identity, and admission already treats every environment as an explicit entry, so nothing new has to be invented to hold a tier.
3. **The placement ceiling list must become derived, not literal.** Evidence: `lab/combined_admission.py:29-31` enumerates the containers it counts by hand, so any new container is invisible to the ceiling check, and that is how the Studio pair would have slipped past it.
4. **Email is per environment, generic SMTP, disabled by default.** Evidence: each environment already has its own Auth process with its own environment file, so a shared installation-wide mailer would become a cross-environment credential and a cross-environment boundary. Autoconfirm stays true until an environment is given SMTP, so behaviour today does not change.
5. **Notifications are an outbox in the control catalog, drained by the worker.** Evidence: the worker already holds the exclusive catalog lock and already owns job state, so a separate notifier process would be a second writer against the same SQLite file. A failed notification must never fail the operation it reports.
6. **Nothing here is claimed as capacity.** Evidence: `docs/NOISY-NEIGHBOR.md` and `docs/SUSTAINED-OVERLOAD.md` record a small latency difference and one failed acceptance, neither of which is a capacity result.

## Workstream 1: resource distribution and isolation

Tasks, in order:

1. `lab/resource_admission.py` and a new `lab/resource_policy.py`: the tier table (production, experimental) with memory, cpus, cpu-shares, blkio-weight, pids, and the exact values. Test: a tier table test that refuses an unknown tier and a tier with no IO weight.
2. `lab/durable_runtime.py:114` `launch()`: pass the weight and IO flags, keeping memory and swap equal and the existing log options. Test: a unit test on the argument list, plus a live check that the running container's `HostConfig` carries the values.
3. `lab/combined_admission.py`: derive the counted container set from the runtime's own placement instead of the literal list, and add the new containers to the arithmetic. Test: a mixture test that shows a placement omitting a container is refused, and the same placement with it is admitted.
4. Continuous pressure response: extend `lab/pressure_admission.py` into a sampling loop with a documented threshold and a safe action (refuse new allocations first, then pause leases through the existing gateway drain), and record every crossing. Test: a synthetic crossing produces exactly one recorded event and one action.
5. Documentation: `docs/RESOURCE-POLICY.md` (new) with the tiers, the arithmetic, the measurement method and the limits. Update `docs/RESOURCE-ADMISSION.md`, `docs/COMBINED-RUNTIME.md`, `docs/DEPLOYMENT-READINESS.md`, `docs/evidence/combined-runtime-admission.json` regeneration, and name the invalidated evidence.
6. Measurement: extend `lab/noisy-neighbor-check.py` to a three-state neighbour experiment on two environments (baseline, one environment under a bounded CPU and IO workload, recovery), and record worst, best and typical numbers with the fixture and the host state. Report failure if a neighbour's error rate rises or its latency leaves a stated envelope.

## Workstream 2: per-environment application email

Tasks, in order:

1. Confirm every variable name against the pinned Auth image `gotrue:v2.196.0` source, not against documentation of another version. Anything unconfirmed is written as unconfirmed.
2. `lab/run.py` `auth_configuration`: add the SMTP block only when the environment has mail settings, and keep autoconfirm true when it does not. Test: an environment without settings produces the exact current environment file, byte for byte.
3. Secret layout: the per-environment private set gains the mail block next to `auth`, `rest`, `storage`, `jwt`, with the loader that already refuses to log values. Test: a secret-safe error test.
4. Rate limits and templates: set the upstream limits per environment, name the defaults, and state what unlimited would allow.
5. Live probe `lab/email-check.py`: start the locally present `public.ecr.aws/supabase/mailpit:v1.30.2` on the runtime's internal network, point one environment's Auth at it, request a password recovery for a disposable identity, and assert the message arrives with the expected recipient and link. Include the isolation assertion: environment A's settings cannot send for environment B, and a deliberately wrong password produces a recorded failure rather than a silence.
6. Documentation: `docs/ENVIRONMENT-EMAIL.md` (new) with the variable table, the disabled-by-default posture, the operator surface and the failure semantics.

## Workstream 3: operator notifications

Tasks, in order:

1. Catalog: an `operator_outbox` table plus the enqueue call, in the same transaction as the state change where the change is a SQL write. Test: enqueue and read back, and a rollback leaves no row.
2. Event inventory: provisioning failure and capacity refusal, admission refusal, fence change, backup and restore result, pressure crossing. Each one maps to an existing observable; anything without one is listed as not observable rather than invented.
3. Drain: `lab/notify.py` drains the outbox under the worker's existing lock, claims exactly once, retries with backoff, and ages out. Test: a double drain sends once, a crash mid-send resends at most once more with a stable message identity.
4. Channels: operator email through an installation-level mail path, and a generic webhook with a signed body, a timeout and a bounded retry. A channel failure is recorded as a delivery failure and never propagates into the operation. Test: a deliberately broken channel records a failure, and the provisioning operation it reported still succeeds.
5. Redaction by construction: the message is built from an allow-list of fields, and a test asserts that a message built from a database connection string, a token or a key cannot carry it.
6. Rate limiting and dedupe: a repeated condition produces one message per window with a count, and one message when it clears.
7. Documentation: `docs/OPERATOR-NOTIFICATIONS.md` (new), plus a readiness row and the console surface for the delivery state.

## Files likely to change

- `lab/durable_runtime.py`, `lab/combined_admission.py`, `lab/resource_admission.py`, `lab/pressure_admission.py`, `lab/noisy-neighbor-check.py`, `lab/run.py`, `lab/worker.py`
- new: `lab/resource_policy.py`, `lab/email-check.py`, `lab/notify.py` and their tests
- `src/control/catalog.ts`, `src/control/*.ts` as needed for the outbox and the delivery state
- new docs: `docs/RESOURCE-POLICY.md`, `docs/ENVIRONMENT-EMAIL.md`, `docs/OPERATOR-NOTIFICATIONS.md`
- updated docs: `docs/RESOURCE-ADMISSION.md`, `docs/PRESSURE-ADMISSION.md`, `docs/COMBINED-RUNTIME.md`, `docs/DEPLOYMENT-READINESS.md`, `docs/OPERATOR-SETUP.md`, `docs/PROJECT.md`, `docs/RESUME-CHECKPOINT.md`

## Verification gates

Every workstream ends with: the full Python suite, the full Bun suite, the unit tests it added, one live probe that produces the real observable, and one deliberately broken variant that records a failure. A green run with no failure variant is not accepted, and no number is quoted from a document.

## Risks, limits and open questions

- One shared PostgreSQL engine and one shared Storage process remain shared failure boundaries. Weights and IO limits bound the damage; they do not make the engine private. The escape hatch is placement: an environment can be moved to its own cluster or another server, and ownership is independent of placement.
- Continuous pressure response can itself cause harm if the threshold is wrong. The first response is refusing new allocations, which cannot hurt a running project; pausing a live environment needs its own gate and is not in the first change.
- A weight only matters when the host is contended. On an idle host the experiment will show nothing, which is a real result and must be recorded as such.
- The mail provider is the operator's to supply. The design ships a working configuration path and a local proof, not a provider.
- The outbox adds a write to the provisioning path. Its cost is measured, not assumed.


## Status, 2026-09-21 06:35 (authoritative: `git log`, not this file)

| Commit | What it carries |
|---|---|
| `46d8d46` | The three design documents, the independent review's acceptance criteria, and this plan. |
| `7878411` | The two facts the parent verified after the designs were written: the daemon reads a container's environment in cleartext, and a test only mailer does not belong in the pin table. |
| `b21170d` | The recorded decisions: the unconfigured mail posture, the mail state that reaches no console yet, and the retained containers that need one recreation. |
| `3e14668` | The implementation of all three topics, the recovery target tier labels, and the cgroup mapping evidence. |
| `0064183` | The seven defects one adversary pass proved, each with a test that fails before it and passes after. |
| `64a122f` | Block IO separation through the per device limits, since the weight flag was measured not to bind. |

Built and verified on this host: the tier table and the container flags; the
counting rule that reads the daemon by label instead of a literal name list; the
per environment mail configuration, its Auth wiring and its reconcile command;
the notification outbox, its worker drain, its email and signed webhook channels
and its fail closed redaction gate. Gates at `0064183`: 530 Python tests, 78 Bun
tests, the mail probe at 36 checks and the notification probe at 21 checks, both
re-run by the parent, both cleaning up what they created.

NOT built, so nobody has to read the design to find out:

1. The remaining measurement experiments in section 5 (the continuous pressure
   response and the mixed SDK load), so the block IO rows stay uncalibrated. The
   neighbour experiment (section 5.1.1) ran and found no repeatable effect.
2. Block IO separation is built (commit `64a122f`, section 3.1.2 of the resource
   policy): every tier carries read and write bandwidth and IOPS rows, the device
   is resolved from the host, and `io.max` was read back inside a container. The
   numbers in those rows are a starting point, not a measurement of a tier.
3. The console surfaces: the mail state route and screen, and the notification
   route with its undelivered count. A table and a file exist that nothing reads.
4. Nine of the notification event kinds are declared and emitted by nobody,
   because their producers live in files this workstream did not own.
5. The mail templates, the provider decision, and the reauthentication variables
   the designer could not confirm against the pinned Auth tag.
6. The retained placement stays grandfathered, and section 3.6 of the resource
   policy was corrected after reading `docs/CONTAINER-GENERATION-MIGRATION.md`: the
   retained database container has no recreation path, because its authority
   registry lives in its own filesystem. The tier contract therefore applies to
   placements created after it, which the fresh worker check exercises, and a tier
   measurement has to run on a fresh placement.

7. Two measurement vehicles refuse to run on the retained placement for the same
   reason, found and verified by the parent on 2026-09-21 and not yet fixed.
   `lab/gateway-overload-check.ts --sustained` takes its two fixtures from
   `.lab/upstream/probe.json`, and the first of those environments resolves through
   `catalog.getProvision('durable-probe-owner', ...)` to the retired environment
   `e_60332245e3a0426dd242492f`, whose Auth container is not running and whose role
   is NOLOGIN, so the run dies on its first SQL command. Its cleanup then throws
   `Overload fixture cleanup incomplete` at line 163, which replaces the real error
   with a cleanup error, so the failure it prints is not the failure it hit. The
   neighbour harness carried the same stale fixture trap and is fixed: it now
   probes each environment and selects the ones that answer
   (`lab/noisy-neighbor-check.py`, commit `72fbbc7`). Until the overload vehicle is
   fixed the same way, and the cleanup stops masking, the sustained arrival
   measurement in section 5.2 stays unrun, and it should not be quoted as pending
   capacity evidence either way.
   A third defect in the same vehicle, found the hard way: it writes its result to
   `docs/evidence/gateway-sustained-failure.json` whatever the outcome, so a run
   that dies in setup replaces the previous run's committed evidence with an empty
   record. Running it on the retained placement did exactly that to a 660 sample
   run, and the file was restored from git. Nobody should run that probe against a
   host it cannot complete on, and the write should be made conditional or
   versioned before it is run again.
