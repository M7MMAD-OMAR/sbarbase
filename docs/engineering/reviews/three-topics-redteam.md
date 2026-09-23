# Acceptance criteria and red team: resource distribution, per-environment email, operator notifications

Status: design record, 2026-09-21. Nothing here is implemented yet. Written by a
delegated design pass and reviewed by the parent the same day: the load bearing claims were
re-read against the code and the counts were reproduced (audit_events 224 and 8 in the two
catalogs, the environment Auth at 256m and 0.25 CPU, the database at 1024m and 1 CPU on one
volume, no blkio or cpu-shares anywhere in the tree, the pin count assertion at
lab/test_pinned_images.py:38). Unverified items are labelled unconfirmed in place.


Adversarial review authored without sight of the three drafts. Every claim below is
anchored to a file and line in `/home/sbarah/R/Projects/P/sbarbase` at commit
`ec765f2`, or to a command that can be run in that checkout. Nothing in the
repository was modified and no implementation is proposed here.

Scope of this document: for each of the three topics, (1) the definition of done
that cannot be faked, (2) the ten most likely ways a plausible implementation is
worthless, ranked by severity, (3) three to five acceptance tests with exact
command shape, fixture, observable and failure, (4) the forbidden shortcuts stated
as failures, (5) the questions a draft must answer before implementation and the
questions that have no good answer on one shared PostgreSQL engine.

## The evidence rules this document holds the drafts to

These are extracted from what this repository has already been bitten by, not
invented. They are the lens for every section below.

- **E1. A claim needs a command, not a document.** `docs/RESUME-CHECKPOINT.md`
  records that numeric claims carried from older runs were wrong against the
  committed artifacts and had to be corrected (`docs/RESUME-CHECKPOINT.md:730`).
  Any number a draft asserts must be re-derived from the artifact it names.
- **E2. A check that cannot fail is not a check.** The same review log records
  that "the checks that could not fail now can" for the TLS stub, the port
  announcements, the traversal probes, the freshness gate and the gate's own
  `Preflight: 0 blocker(s)` line, and that a supervised gate row "grepped a 60-line
  journal window that could contain an earlier run's `Preflight: 0 blocker(s)`, so
  it could not fail" (`docs/RESUME-CHECKPOINT.md:593`, `:714`). Every acceptance
  test below must name the failure it is able to produce.
- **E3. Raising a threshold is not fixing a design.** `docs/SUSTAINED-OVERLOAD.md:22`
  forbids hiding client timeouts by reclassifying them or by extending the client
  deadline. A draft that moves a constant instead of bounding an input is refused.
- **E4. A short run is not capacity.** `docs/RESOURCE-ADMISSION.md:24` and
  `docs/reviews/capacity-method.md:3` both say a snapshot or short run does not
  establish capacity. `PROJECT.md:40` says a daily visitor count cannot be
  converted into project capacity.
- **E5. Report the distribution, not the total.** `docs/reviews/capacity-method.md:18`
  forbids summing process RSS because shared pages are counted repeatedly, and
  `:24` requires the one-busy-nine-quiet case rather than an aggregate. A total
  over all environments hides the single loud tenant, which is the only case the
  user cares about.
- **E6. A fixture that makes the hostile case harmless proves nothing.**
  `docs/NOISY-NEIGHBOR.md:13` notes the probe used a scoped credential and created
  no application data; `docs/CONNECTION-BUDGET.md:15` notes the fixture did not
  modify application data. A hostile-neighbour fixture must be hostile.
- **E7. Counts come from the artifact, not from prose.** `PROJECT.md:29`: "Evidence
  files are versioned snapshots, not cumulative independent test totals."
- **E8. Missing measurement fails closed.** `lab/resource_admission.py:30` and
  `lab/pressure_admission.py:37` both raise on incomplete data rather than
  defaulting to admit.

---

# Topic A. Resource distribution and isolation on one server

## A.1 Definition of done that cannot be faked

**State.** Every environment on the host has a hard, named, per-environment
budget for CPU time, memory, disk space and block I/O, enforced by the kernel
(cgroup v2) and not only by application admission; each budget is small enough
that N environments plus the shared services fit under the installation ceiling
`MAX_MEMORY = 6 * 1024**3` and `MAX_CPUS = 6` (`lab/combined_admission.py:11`,
`:12`); and a continuously running responder reassigns or throttles a saturated
environment while it is saturated, not only at the moment a new environment is
admitted.

**Value.** With environment X driven to saturation, every other environment's
p95 request latency and its SQL latency stay within a stated multiple of their
own unloaded baseline, and the reloaded baseline is the one measured in the same
run. The distribution of per-environment latency is published, not its mean.

**Proof.** A live artifact produced by a committed probe, containing for each
environment: its cgroup limit as read from the kernel inside the container
(`/sys/fs/cgroup/memory.max`, `cpu.max`, `io.max`), the load it was offered, the
arrival rate actually issued, the accepted and rejected counts separately, and
its own p50/p95/max latency with the neighbour's. Plus a second artifact, from a
run at 1 busy environment and N-1 quiet ones, in the same file with the same
fields.

**Why a reader can check it without trusting the author.** The cgroup numbers are
read from inside the container by the container, not from the host's `docker
inspect` of a value the author wrote; the offered-versus-issued rates are both
recorded (so a closed-loop generator cannot hide overload by slowing down, which
`docs/reviews/capacity-method.md:22` requires); and the neighbour latency has its
own baseline in the same file. The one thing a reader cannot verify is a claim
about capacity at 10 or 100 projects, and the draft must not make one.

## A.2 Ten ways a plausible implementation is worthless, ranked by severity

**1 (fatal). Admission-only isolation: the gate runs at provisioning time and
never again.** `docs/RESOURCE-ADMISSION.md:24` says the snapshot "cannot prevent
another process from consuming resources immediately afterward", and
`lab/resource_admission.py` is only called from the allocation path
(`lab/durable_runtime.py:280`). Why it passes a naive test: a test that provisions
four environments and checks they all answer sees a fully green system, because
no environment is ever loaded. The specific assertion that catches it: with
environment X held at saturation, poll the other environments for 60 seconds and
assert that X's own cgroup `cpu.max` was actually reached and that the admission
gate's refusal reason changed for a fifth allocation. A system with no continuous
response has identical latency in the first second and the sixtieth; the
assertion must span both.

**2 (fatal). The per-environment budget is a container limit on the services and
nothing at all on the database.** The shared database container is launched with
`'1024m', 1` (`lab/durable_runtime.py:187-189`), one `pgdata` volume, and the
per-environment Auth and REST containers get `'256m', .25` each
(`lab/durable_runtime.py:308`). Everything a project does to PostgreSQL shares one
1 GiB, 1 CPU ceiling with every other project. Why it passes a naive test: a
memory check inside `docker inspect` shows a limit exists. The assertion that
catches it: read `memory.max` and `cpu.max` from the database container's own
cgroup namespace while two environments run load, and show that the limit is the
shared one, then show that the per-environment share of it is unmeasured. If the
draft's answer is "the DB cgroup limit covers everyone", the design does not
distribute, it caps.

**3 (fatal). No block I/O limit anywhere.** A search of the repository for
`blkio`, `io.max`, `device-read-bps` or `device-write-bps` returns nothing: every
container launch in `lab/` passes only `--memory`, `--memory-swap`, `--cpus` and
`--pids-limit`, for example `lab/durable_runtime.py:134` and `lab/run.py:57`. The
user's concern is precisely the case where one project's writes make the others
slow, and I/O is the one resource this stack can lose to with no isolation at all.
Why it passes a naive test: the CPU and memory numbers are bounded, so a probe
that measures request rate looks acceptable. The assertion that catches it: a
fixture that writes a large sequential dataset plus `fsync` from environment X
while environment Y records its 95th percentile insert commit time; the artifact
must also record the host's disk saturation (PSI `io full` on the host, which
`lab/combined_admission.py:54` already reads for the host), because without it the
run may have been idle by accident. If there is no `io.max` in X's cgroup, the
draft must say so in the artifact rather than omit it.

**4 (fatal). No disk quota for a tenant.** `docs/RESOURCE-ADMISSION.md:12` states
plainly that the numbers are "not preallocated resources or disk quotas" and that
"PostgreSQL/Storage memory growth and future data growth are not bounded by the
per-environment allowance". A project that grows its database is limited only by
the host filesystem. Why it passes a naive test: the admission gate checks 6 GiB
free and passes, so a test at low occupancy is green. The assertion that catches
it: a fixture that inserts rows until the environment exceeds a stated per-tenant
byte budget, asserting the write is refused or the tenant is fenced before the
host's reserve is touched. If the draft has no per-tenant byte budget, the
assertion has no witness and the test must be reported as not implemented rather
than absent.

**5 (serious). Pressure is measured on the wrong objects.** `CONTAINERS` is fixed
to `('sbarbase-durable-db', 'sbarbase-durable-storage')`
(`lab/pressure_admission.py:7`), so the gate measures the shared services, never a
tenant. A load originating inside one environment shows up in that environment's
own cgroups, which nobody reads. Why it passes a naive test: the gate refuses when
the host is busy, so it looks like a pressure response. The assertion that
catches it: run the saturation fixture and assert that the artifact contains a
per-environment PSI reading that crossed a threshold while the shared-container
reading did not, which is the case the current design cannot produce.

**6 (serious). The "fair share" is one process-wide count, not a share.**
`src/gateway/concurrency.ts:34` defaults to `perEnvironment=8, maximum=32`, with
the class comment at `:1` reading "In-process admission only." There is no
reservation of the process budget, so the first two environments to arrive can
occupy all 32 slots and a neighbour is refused by a system that has capacity.
Why it passes a naive test: the environment that is refused gets a 503, which
looks like correct overload handling. The assertion that catches it: a fixture
with 5 environments each wanting 8 slots, asserting what the 6th gets; a fair
share design has a stated floor per environment and the artifact shows it, and a
count design shows a refusal that has nothing to do with the refused
environment's own load.

**7 (serious). The connection limit is treated as a resource budget.**
`docs/CONNECTION-BUDGET.md:22` warns that these are "login and database connection
controls, not query CPU, memory, lock-duration or disk-I/O isolation". A draft
that answers the user's concern with `CONNECTION LIMIT 6` has bounded the number
of holes, not what goes through them. Why it passes a naive test:
`lab/connection-limit-check.py` already saturates one Auth login and shows a
neighbour working, so the "isolation" test is green by construction. The
assertion that catches it: the same saturation run must report the *query* cost,
by holding 6 REST logins busy with slow statements and asserting the neighbour's
SQL latency, not merely its ability to connect.

**8 (serious). The priority class is a label with no consequence.** A draft may
add a `priority` column to the catalog and route admission on it. `write` cost
only. Why it passes a naive test: the unit test asserts the column round-trips.
The assertion that catches it: two environments in different priority classes,
both offered the same load beyond the shared budget, and the artifact must show
the higher class received a measured larger share of admitted requests or of CPU
time. A class that changes no observed distribution is a naming exercise.

**9 (dangerous). Measurement taken from the document or from `docker inspect`.**
`docs/RESOURCE-ADMISSION.md:18` records a saved live snapshot, but a snapshot is
one instant; `docs/DEPLOYMENT-READINESS.md` records the same admission figure
moving between 8187 and 9453 MiB across runs of the same measurement. A draft that
quotes 8943 or 8766 MiB without re-running has done E1. The assertion that catches
it: the artifact must carry a timestamp and be regenerated by the probe in the
same run as the latency numbers it is compared with, and the acceptance step must
compare an artifact digest, not a remembered value.

**10 (dangerous). The hostile fixture is made harmless.** `docs/NOISY-NEIGHBOR.md:3`
records that the neighbour workload was "a read-only calculation loop for eight
seconds" and 50 small aggregates, and `:11` states the difference "cannot
establish tenant isolation". A draft that reproduces this shape and adds a
priority field will pass. The assertion that catches it: the fixture must be
declared hostile in the artifact (writes, not reads; duration longer than the PSI
averaging window; more than one busy environment at a time), and the acceptance
step must refuse an artifact whose fixture description matches the read-only
shape.

## A.3 Acceptance tests

**A-T1. Isolation under a hostile neighbour, distribution reported (positive
control plus absence of harm).**
Command shape:
```
bun lab/resource-isolation-check.ts --busy 1 --quiet 3 --duration 60 --load write-heavy \
  --artifact /tmp/sbarbase-plan/evidence/isolation-one-busy.json
```
Fixture: N retained environments (4 exist today per `PROJECT.md:61`), one driven
by a write-heavy loop long enough to exceed the PSI `avg10` window, the rest
sampling their own SQL and HTTP latency against a baseline captured in the same
run before the load starts.
Observable: per-environment cgroup limit read from inside each container, its own
p50/p95/max latency in both phases, the neighbour latencies, host `io full` and
`cpu some` recorded at both ends, and the offered-versus-issued request counts for
the open-loop generator.
Failure it must be able to produce: if the shared database container's CPU or I/O
throttling shows the quiet environments' p95 moving by more than the draft's
stated multiple, the run is red. This test fails today by construction because no
`io.max` exists, which is the point.

**A-T2. Per-tenant disk budget, PASS is the absence of host exhaustion.**
Command shape:
```
/usr/bin/python3 lab/tenant-quota-check.py --environment e_<id> --bytes <budget> \
  --artifact /tmp/sbarbase-plan/evidence/tenant-quota.json
```
Fixture: a fresh environment and a fixture table, with `df -Pk` on the host
filesystem captured before and after. The writer must be a real client through the
gateway, not `docker exec`, so the write path is the product path.
Observable: the writer is refused at or before the byte budget, and the host
filesystem's free bytes have not moved by more than the fixture's own row size.
Failure it must be able to produce: if the writer succeeds past the budget, red;
if the writer is refused but the host filesystem lost more than the stated token
amount, red, because the refusal came from the host reserve, which means the
tenant budget was never the thing that stopped it. PASS state is the absence of
any host reserve movement, which is a stronger statement than "the write failed".

**A-T3. Continuous response, PASS is the absence of a neighbour effect over time.**
Command shape:
```
bun lab/pressure-response-check.ts --busy e_<id> --probe e_<neighbour> --samples 60 \
  --interval 1000 --artifact /tmp/sbarbase-plan/evidence/pressure-response.json
```
Fixture: the busy environment held at saturation for the whole window; the
neighbour polled once per second. One sample is deliberately taken at second 0 and
one at second 60, and the artifact keeps both.
Observable: the series, not a summary. The assertion is that the neighbour's
series has no step, the busy environment's cgroup was at its limit, and the
responder recorded at least one intervening action (throttle, reassignment or
refusal) with a timestamp inside the window.
Failure it must be able to produce: a system with an admission-time gate only
produces a flat neighbour series *and* zero intervening actions, which must be
red. This is the test that distinguishes "no neighbour was harmed" from "nothing
tried to harm a neighbour".

**A-T4. The admission arithmetic is bounded by capacity, not by a raised ceiling.**
Command shape:
```
/usr/bin/python3 lab/resource_admission.py
/usr/bin/python3 lab/pressure_admission.py
/usr/bin/python3 lab/combined_admission.py
```
Run all three with the placement stopped and with it started, and keep both
artifacts.
Fixture: the four retained environments; a fifth allocation attempt through
`lab/admission-check.py` to produce the refusal.
Observable: the refusal reason tuple from each gate separately
(`memory_headroom`, `disk_headroom`, `inode_headroom`, `cpu_some10`,
`io_full10`, `memory_full10`, `host_memory_headroom`, `installation_ceiling`),
and the same tuple set produced twice at different host loads.
Failure it must be able to produce: if the draft changed a constant instead of a
bound, two runs at different natural host loads must produce different reasons; if
they produce the same reason regardless of load, the gate is reading a value the
author set rather than the host. Also red if any gate returns admit on a missing
measurement, which `lab/resource_admission.py:30` and `lab/pressure_admission.py:37`
currently prevent and the test must re-prove.

**A-T5. Priority class changes the observed distribution, not a column.**
Command shape:
```
bun lab/priority-share-check.ts --class-a e_<id1> --class-b e_<id2> --offered 8 8 \
  --artifact /tmp/sbarbase-plan/evidence/priority-share.json
```
Fixture: two environments, one per class, each offering more work than the shared
process budget of 32 (`src/gateway/concurrency.ts:34`) so that contention is
guaranteed rather than hoped for.
Observable: admitted counts per environment, rejection counts per environment,
and the latency of each, all in one artifact. The assertion is a distribution
comparison, not a total.
Failure it must be able to produce: if both environments get the same share, red
(the class has no consequence); if one gets everything, red (the class is not a
floor, it is a preemption). The draft must state which of the two is intended.

## A.4 Forbidden shortcuts, stated as failures

- **Pass: a ceiling was raised.** If the change increases `MAX_MEMORY`
  (`lab/combined_admission.py:11`), `perEnvironment` (`src/gateway/concurrency.ts:34`)
  or any PSI threshold (`lab/pressure_admission.py:6`) to make a test green, the
  change is refused. A larger constant is a declaration that the input is now
  acceptable. The input must be bounded, not the ceiling moved.
- **Pass: isolation is proven in a comment, or on a `docker inspect`.** A
  statement in a doc that environments are isolated, without a per-container read
  of `memory.max`, `cpu.max`, `io.max` from inside the container and without a
  loaded neighbour series, is a failure of this gate. `docker inspect` shows the
  limit the author asked for; it does not show the kernel enforcing it under load.
- **Pass: a memory or disk number is reported from a document.** Quoting
  `docs/evidence/resource-snapshot.json` or `source-stage-footprint.json` as the
  current value fails E1 and `docs/RESUME-CHECKPOINT.md:730`. The number must come
  from a command run in the same session as the conclusion.
- **Pass: the total is reported instead of the distribution.** Summing latency,
  admitted counts or memory across environments fails E5. The one-busy case must
  be in the artifact and the quiet environments must be individually named.
- **Pass: a short run is presented as capacity.** Any sentence that converts the
  60-second run into a statement about 10 or 100 projects fails `PROJECT.md:40`.
  A bounded local run may state a local limit and nothing more.
- **Pass: the shared database is described as per-database isolation.** Separate
  databases in one cluster share memory, CPU, WAL and failure scope; this is in
  `docs/reviews/capacity-method.md:10` and `docs/reviews/security-operations.md:15`.
  A draft that presents database-per-environment as resource isolation is wrong.
- **Pass: the hostile case is made quiet.** A read-only fixture, a short fixture,
  a fixture that runs when no neighbour is loaded, or a fixture on the loopback
  path only, each fails E6 and `docs/NOISY-NEIGHBOR.md:13`.
- **Pass: a refusal is reclassified as correct handling.** A client timeout during
  the busy run is a failure, not an expected overload response
  (`docs/SUSTAINED-OVERLOAD.md:22`).

## A.5 Questions the draft must answer before implementation

1. Which enforcement point owns each of the four resources: CPU time, memory,
   filesystem bytes and block I/O? A design with an answer for three of them is
   incomplete, and the missing one is the one the user is worried about.
2. What is the per-environment floor and the per-environment ceiling, in numbers,
   and what is the arithmetic that guarantees the sum of floors plus the shared
   services fits under `MAX_MEMORY` and `MAX_CPUS` (`lab/combined_admission.py:11`)?
3. What is the response loop, its period, its hysteresis, and what does it do when
   a tenant is inside a PostgreSQL backend that cannot be interrupted? The repo
   already knows a gateway slot can outlive cancellation
   (`docs/REST-CANCELLATION.md`, `docs/GATEWAY-OVERLOAD.md:5`), so "throttle it"
   needs a mechanism for work that ignores throttling.
4. Is priority preemptive or a share, and what happens to work already admitted
   when the class changes?
5. Does the responder act per environment, per database, or per host, and what is
   the blast radius of a wrong decision? A responder that can pause an innocent
   neighbour is a new failure mode.
6. Where does the per-environment budget live so that it survives a restart, a
   reconciliation and a recovery, given that placement names are fixed and drift
   fails startup (`lab/README.md:29-35`)?
7. What is the interaction with `statement_timeout=8s` and `transaction_timeout=12s`
   (`docs/SQL-DEADLINES.md`)? A CPU budget and a wall-clock deadline are different
   tools; the draft must say which one stops a CPU-bound query.
8. What does the artifact record when the measurement is missing, and does it fail
   closed (E8)?

### Questions that have no good answer on one shared PostgreSQL engine

- **Per-database CPU and memory budgets.** `docs/reviews/capacity-method.md:10`:
  separate databases in a cluster do not have independent memory or CPU budgets.
  In one engine the only real partition is a connection-path cost or a statement
  cost, not a budget. A draft that promises per-tenant CPU needs a statement
  (independent cluster, or a pooler mediator that is itself a shared bottleneck,
  which the repo has not adopted).
- **Per-database block I/O limits.** The kernel enforces I/O on a device or a
  cgroup, and every database writes to the same `pgdata` volume
  (`lab/durable_runtime.py:188`). There is no per-tenant `io.max` that a single
  PostgreSQL instance can honour for one database, so the honest answer is a
  shared limit plus admission, or moving the loud tenant out.
- **Per-database memory accounting that is not shared.** PostgreSQL shares one
  buffer pool, one set of WAL buffers and one OS page cache across databases. Any
  per-tenant memory number this installation produces will be an attribution, not
  a bound. A draft that reports a per-environment memory figure as a budget is
  reporting a shared quantity twice (E5).
- **Tenant-level filesystem quotas.** Quotas are a filesystem feature this host's
  Btrfs volume does not currently apply, and `docs/RESOURCE-ADMISSION.md:20`
  already records that Btrfs metadata health and quotas remain open gates. If the
  answer is "quota", the draft must name the filesystem, the quota type and the
  measured behaviour when the quota is hit.
- **Fairness between queries inside one instance.** PostgreSQL has no per-role or
  per-database CPU scheduler that a pooler-free design can lean on. The honest
  answer is the one `PROJECT.md:11` names as the fallback: independent PostgreSQL
  when isolation gates fail.

---

# Topic B. Per-environment email for hosted applications

## B.1 Definition of done that cannot be faked

**State.** Each environment's original pinned Auth service
(`public.ecr.aws/supabase/gotrue:v2.196.0`, `docs/UPSTREAM-UPDATE-POLICY.md:52`)
can send real transactional email through a configured SMTP server, using the
environment's own sender identity and its own SMTP credential, with
`GOTRUE_MAILER_AUTOCONFIRM` false and the upstream rate limits set to finite
values; and when no SMTP server is configured for an environment, that
environment creates no email noise and does not accept an unverified signup.

**Value.** A confirmation link issued by environment A arrives at a real inbox
through a real SMTP server, the link verifies the identity in A, and the same
identity in environment B is unaffected. The measured send rate for one
environment stays under the configured limit, and the limit is not the SMTP
provider's.

**Proof.** One artifact from a run that includes: the SMTP server's own received
message log (envelope recipient, envelope sender, `Message-ID`), the Auth
service's log line for the same operation, the token accepted by A, the same
token rejected by B, and the count of messages the server received when a fixture
attempted more signups than the configured limit in a stated window.

**Why a reader can check it without trusting the author.** The message log is
produced by the SMTP server, not by the platform; the same-identity-in-B check
proves the tokens are environment-scoped, which is the actual isolation claim;
and the count is from the server's log, so a platform counter that agrees with
itself proves nothing.

**What must be true today before this can be claimed.** The environment Auth
configuration is built by `lab/auth_configuration` (`lab/run.py:118-130`), which
currently sets `'GOTRUE_EXTERNAL_EMAIL_ENABLED': 'true'` and
`'GOTRUE_MAILER_AUTOCONFIRM': 'true'` (`lab/run.py:129`) and passes no SMTP
settings at all. `lab/durable_runtime.py:307` uses that same builder for every
environment's Auth. The only place the repository turns auto-confirm off is the
management realm (`lab/durable_runtime.py:353`), which is not an application
environment. So at this commit every environment accepts a signup with no email
and no verification, and there is no "disabled by default" email posture to
point at.

## B.2 Ten ways a plausible implementation is worthless, ranked by severity

**1 (fatal). "Disabled by default" is asserted while auto-confirm is on.**
`lab/run.py:129` sets `GOTRUE_MAILER_AUTOCONFIRM` to `'true'`. A draft that adds
SMTP variables and a paragraph about being off by default, without changing that
line's effect, ships an environment that never sends the confirmation it claims
to gate. Why it passes a naive test: the SMTP variables are absent, so "no email
was sent" is trivially true and the test is green by absence, not by design. The
assertion that catches it: with no SMTP configured, attempt a signup and assert
the identity is *not* confirmed, not merely that no mail was sent. The current
code confirms it.

**2 (fatal). One SMTP credential shared across environments.** The pressure to
share is real: `lab/values` holds one admin password and one set of per
environment secrets (`lab/durable_runtime.py:288`), and there is no per
environment SMTP field. Why it passes a naive test: a single send succeeds, mail
arrives, everything looks correct. The assertion that catches it: a degraded
neighbour test. Rotate or revoke the credential the draft proposes for
environment A only, then assert that A stops sending and B continues to send. A
shared credential cannot produce that result.

**3 (fatal). The SMTP password lands in an env file and is therefore readable
from `docker inspect`.** `lab/run.py:54-55` and `lab/durable_runtime.py:131-132`
write every configuration value to `.secrets/<name>.env` and pass it with
`--env-file`. The repository's own drift check reads those values back from the
container: `actual['Config'].get('Env')` at `lab/durable_runtime.py:121`. So any
SMTP password added to the builder becomes visible to anything that can inspect
the container, which includes every sibling container on the internal network and
any process with the Docker socket (the service account is in the `docker` group
by design, `docs/SERVER-DEPLOYMENT.md`). Why it passes a naive test: the file mode
is `0600` (`lab/run.py:39`), so a permission check passes. The assertion that
catches it: assert that no SMTP credential appears in `docker inspect --format
'{{json .Config.Env}}' <auth container>`. This is a genuine, checkable absence and
it fails against the obvious implementation.

**4 (fatal). The sender identity is per installation, not per environment.** A
draft that names one `GOTRUE_SMTP_ADMIN_EMAIL` and one sender name for the whole
platform has made every environment able to send as every other, and a recipient
cannot tell which project the mail came from. Why it passes a naive test: mail
arrives. The assertion that catches it: two environments, two sends, and the
artifact must show two distinct envelope senders and two distinct `From` headers,
with environment A holding no configuration that lets it use B's sender.

**5 (serious). The rate limit is the provider's, not the environment's.** A
draft that relies on the SMTP provider's per-account throttle has no per
environment limit and will discover it by being blocked globally. Why it passes a
naive test: one send at a time never trips a provider limit. The assertion that
catches it: a fixture that drives more signups in a stated window than the
configured per-environment limit, asserting that the Auth service refused, that
the SMTP server's received count is at or under the limit, and that the refusal
was per environment (a second environment still sends).

**6 (serious). Templates are asserted from the pinned image rather than
rendered.** A draft may point at the default upstream templates and claim
branding. Why it passes a naive test: a string search finds template variables in
the image. The assertion that catches it: the artifact must contain the rendered
subject and body from the SMTP server's received message, for each of the
confirmation, recovery and email-change templates, and the link must actually
resolve (change of email and recovery links are the two that silently break when
the redirect allow-list is wrong).

**7 (serious). The link resolves to the wrong place, and the test only checks
that a link exists.** The environment's Auth URL is set from `API_EXTERNAL_URL`
and `GOTRUE_SITE_URL` in the builder (`lab/run.py:124-125`), both currently
`http://localhost`. A link built from those is unreachable from a real inbox.
Why it passes a naive test: the message arrives and contains a URL that parses.
The assertion that catches it: follow the link from a client that is not the
producer, assert the redirect target is the configured public host and that the
token is consumed exactly once, then re-use it and assert refusal.

**8 (serious). The secret is stored per environment but the disabled state is
global.** A single boolean "email enabled" that turns on the feature for all
environments fails the user's actual need, which is per environment. Why it
passes a naive test: the feature works as a feature. The assertion that catches
it: with one environment configured and one not, assert the configured one sends
and the unconfigured one does not, in the same run, and that the unconfigured
one did not fall back to the configured one's settings.

**9 (dangerous). The verification is proven by a platform assertion.** A test
that reads `email_confirmed_at` from the environment's own database and stops
there proves the platform agrees with itself. Why it passes a naive test: the row
is present. The assertion that catches it: the SMTP server's log must contain the
message whose token produced that row, with matching recipient, and the token
must be refused after one use. Without the server side, a stub mailer satisfies
every platform check.

**10 (dangerous). The count reported is a total, or a count from prose.** "Sent
12 emails" hides that one environment sent 12 and the limit for that environment
is 4. Why it passes a naive test: the number is plausible. The assertion that
catches it: per-environment counts, from the SMTP server, with the window stated,
compared against the configured limit for that environment.

## B.3 Acceptance tests

**B-T1. Real send through a real SMTP server, environment-scoped (positive plus
cross-environment refusal).**
Command shape:
```
bun lab/email-environment-check.ts --environment e_<id> --smtp-host 127.0.0.1 \
  --smtp-port <port> --neighbour e_<id2> --artifact /tmp/sbarbase-plan/evidence/email-send.json
```
Fixture: a local SMTP server that accepts mail without relaying to the internet
(it must be real enough to speak SMTP and to log an envelope), plus a signed-up
identity in one environment, plus the same email address signed up in the
neighbouring environment so the token-crossing check is real. `docs/reviews/durable-runtime.md:39`
records that this installation already separates identities with the same email,
so the fixture is available and must be used, not simulated.
Observable: the SMTP server's received message log (envelope recipient, envelope
sender, `Message-ID`, timestamp), the Auth service's own log line, the token, the
verification result in A, and the same token's rejection in B.
Failure it must be able to produce: with SMTP misconfigured (a port that nothing
listens on), Auth must fail the signup or the resend, not silently succeed; and
with the wrong SMTP password, the run must be red, not green on a retry that the
platform swallowed.

**B-T2. Disabled by default, PASS is the absence of confirmation and of mail.**
Command shape:
```
bun lab/email-disabled-check.ts --smtp-server-log /tmp/sbarbase-plan/evidence/smtp-disabled.log \
  --artifact /tmp/sbarbase-plan/evidence/email-disabled.json
```
Fixture: a fresh environment with no SMTP configuration and no per environment
email enabled, plus the SMTP server running and logging.
Observable: the SMTP server's received count is zero, the attempted signup's
identity is unconfirmed in the environment's own `auth.users`, and the resend
endpoint returns a refusal rather than a 200 with no mail.
Failure it must be able to produce: against `lab/run.py:129` as it stands today,
the identity is confirmed with no mail, so the test is red. It must also be able
to fail in the other direction: an implementation that makes signup fail entirely
(unconfirmed and unusable) is distinct from one that makes signup fail closed with
a stated reason, and the draft must say which it intends. PASS is the absence of a
message in a log produced outside the platform.

**B-T3. Per-environment credential and sender isolation.**
Command shape:
```
bun lab/email-credential-isolation-check.ts --a e_<id> --b e_<id2> --revoke a \
  --artifact /tmp/sbarbase-plan/evidence/email-credential-isolation.json
```
Fixture: two environments with two SMTP identities against one local SMTP server
that requires a password per sender. Revoke A's credential at the server.
Observable: after revocation, A fails to send and B still sends; the artifact
contains both outcomes and both senders. Also: `docker inspect` of A's Auth
container contains no SMTP password (this is the B-T3 pass condition that is an
absence, see test B-T4).
Failure it must be able to produce: a shared credential makes both stop, red; a
per-installation sender makes both senders identical, red.

**B-T4. No credential in the inspectable plane, PASS is an absence.**
Command shape:
```
docker inspect --format '{{json .Config.Env}}' sbarbase-durable-e_<id>-auth \
  > /tmp/sbarbase-plan/evidence/auth-env.json
bun lab/secret-plane-check.ts --artifact /tmp/sbarbase-plan/evidence/secret-plane.json \
  --env /tmp/sbarbase-plan/evidence/auth-env.json --env-file .secrets/upstream/sbarbase-durable-e_<id>-auth.env
```
Fixture: a configured environment; the check reads `Config.Env`, the `--env-file`
path, the container's mounts and its command line, and compares each against the
SMTP credential value that the SMTP server knows.
Observable: the SMTP password appears in none of `Config.Env`, the command line or
a world-readable path; it appears only in a `0600` file that the container reads
as a mount, if it appears in a file at all.
Failure it must be able to produce: the obvious implementation against
`lab/durable_runtime.py:131` puts every configured value in `Config.Env` and fails
this test. This test is the one that makes the secret-handling decision explicit
rather than accidental.

**B-T5. Rate limit is enforced per environment, PASS is the absence of overshoot.**
Command shape:
```
bun lab/email-rate-check.ts --environment e_<id> --attempts <N> --window <seconds> \
  --limit <configured> --artifact /tmp/sbarbase-plan/evidence/email-rate.json
```
Fixture: the local SMTP server with a counter, an environment whose per
environment limit is configured to a small finite number, and a neighbour whose
limit is independent.
Observable: the SMTP server's per-sender received count for the window, the
refusal statuses returned to the extra attempts, and the neighbour's received
count.
Failure it must be able to produce: no configured limit means the received count
equals the attempts, red; a limit that is global means the neighbour is also
refused, red.

## B.4 Forbidden shortcuts, stated as failures

- **Pass: one SMTP credential shared across environments.** This fails the same
  reasoning `docs/ARCHITECTURE-REVIEW.md:80` applies to Supafleet's shared
  passwords: it expands compromise scope. One credential for the installation is
  a failure of this gate, not a simplification.
- **Pass: `GOTRUE_MAILER_AUTOCONFIRM` left true.** An environment that confirms
  an identity without a click is not email-enabled, it is email-disabled with a
  cosmetic SMTP setting (`lab/run.py:129`).
- **Pass: email tested with a stub mailer.** A test double proves the platform
  calls a mailer, not that a real SMTP server accepted a message. The SMTP
  server's own log is the witness, per B-T1.
- **Pass: the secret is protected by a file mode instead of by not being in the
  plane.** `0600` on `.secrets/...env` (`lab/run.py:39`) is not protection from
  the Docker socket or from `Config.Env` (`lab/durable_runtime.py:121`).
- **Pass: "email is disabled by default" is claimed from a document.** It must be
  demonstrated by the absence of a confirmation and the absence of a message in a
  log produced outside the platform.
- **Pass: the rate limit is the provider's.** A per environment limit is a
  number the platform owns and can state and measure; a provider limit is a
  surprise that arrives once and blocks everyone.
- **Pass: the template is asserted from the image.** The rendered subject and body
  must be in the artifact, produced by the SMTP server.
- **Pass: a verification token is proven by reading the database.** The row is the
  platform's opinion of itself; the server log plus the token's single use is the
  proof.
- **Pass: "email does not affect provisioning" is claimed without a test.** If any
  mail configuration step can turn a queued environment into a failed one, the
  claim is false. See the notification rule in C.4; the same rule applies here.

## B.5 Questions the draft must answer before implementation

1. Where does the per environment SMTP credential live, and what reads it? The
   answer must survive the drift check (`lab/durable_runtime.py:121`), which
   compares configured values after a container restart.
2. What is the disabled posture exactly: no SMTP variables at all, or SMTP
   variables present with sending disabled? Those have different failure
   behaviour when the variables are wrong, and GoTrue's own handling must be
   read from `v2.196.0`, not assumed.
3. Which email flows are in scope: confirmation, recovery, email change, magic
   link, invite? Recovery and email-change have redirect allow-lists that fail
   silently when wrong. `docs/OPERATOR-SETUP.md:86` lists invitations as missing
   platform features, so the draft must separate an application invitation from a
   platform one.
4. Is auto-confirm off per environment, and what breaks in the existing fixtures
   when it is? `lab/distro-check.py:75-76` configures auto-confirm for the
   distribution probe, and `lab/run.py:129` for the component lab, so turning it
   off changes existing probes and the draft must say which ones.
5. What is the public host for each environment that a link resolves to, given
   `API_EXTERNAL_URL` and `GOTRUE_SITE_URL` are `http://localhost` in
   `lab/run.py:124-125` and `docs/SERVER-DEPLOYMENT.md` serves TLS through
   `deploy/console-tls-proxy.ts` for the console route only?
6. What is the per environment send limit, its window, and what the user sees when
   it trips?
7. Does the platform ever send mail itself (invitations, notifications), and if
   so, from which identity? This overlaps C and must be decided once, not twice.
8. How does the environment's email configuration survive an export and a
   restore? `docs/RECOVERY-EXPORT.md` encloses tenant signing keys under a backup
   key; an SMTP credential is a third party secret with a different rotation
   story and the draft must say where it goes in the recovery bundle.

### Questions that have no good answer on one shared PostgreSQL engine

- **A per environment sender that is really isolated.** The SMTP identity is an
  Auth service setting, not a database setting, so it is answerable per
  environment, but only if the credential is stored outside the shared PostgreSQL
  configuration path. There is no good answer that keeps the sender secret inside
  the shared cluster's configuration row.
- **Email rate limits that account for shared database load.** Send limits are a
  GoTrue counter; the database cost of the signup is a shared PostgreSQL cost.
  Nothing in this architecture makes the email limit and the connection budget
  one decision, which means an email burst can exhaust the shared connection
  budget without tripping any email limit. The draft must state this coupling
  rather than pretend the two limits are independent.

---

# Topic C. Operator notifications and alerting

## C.1 Definition of done that cannot be faked

**State.** Every event the operator must learn about (provisioning failure, a
capacity refusal, an admission-gate refusal, a pressure threshold crossing, a
recovery result) is written durably, in the same commit as the fact it describes,
into a store that survives process death; a separate delivery loop reads that
store and sends through configured channels; and a delivery failure never changes
the outcome of the operation that produced the event.

**Value.** Kill the notification path entirely (unload the channel, make the
sender fail), perform a provisioning refusal and a recovery, and the operation
still reaches its correct terminal state with its correct recorded reason, while
the undelivered event remains pending and is delivered exactly once when the
channel returns.

**Proof.** Two artifacts from the same fixture: one from a run with the channel
healthy, containing the event, its channel receipt and the operation's terminal
state; and one from a run with the channel failing, containing the same event
still pending, the operation's identical terminal state, and a redelivery after
the channel returns where the channel's own message log shows the event exactly
once, not twice.

**Why a reader can check it without trusting the author.** The operation's
terminal state is read from the catalog's own columns
(`provision_jobs.failure` in `src/control/catalog.ts:61`, limited to
`'capacity_exceeded'` and `'runtime_failed'`), not from the notifier; the
duplicate check counts messages in the channel's log, not rows in the platform's
table; and the crash point is a real `SIGKILL`, which the repository already does
this way (`docs/PROVISIONING-RECEIPTS.md:20`, `docs/EFFECT-GUARDIAN.md:11`).

**What must be true today before this can be claimed.** There is no notification
system. The nearest durable record is `audit_events` in the local SQLite catalog
(`src/control/catalog.ts:53-55`, insert at `:87`), which `docs/CONTROL-PLANE.md:11`
explicitly calls "local records, not a tamper-proof external audit log", and the
nearest durability protocol is the effect receipt
(`docs/PROVISIONING-RECEIPTS.md:3`), which is fsynced before an effect and never
overwritten. A draft for C should lean on that receipt protocol's shape, because
the repository has already paid for the lessons in it.

## C.2 Ten ways a plausible implementation is worthless, ranked by severity

**1 (fatal). The notification send happens before the outcome is durable.** If
the code sends and then writes the terminal state, a crash between the two loses
the event and leaves the job running, or writes the state and never sends. Why it
passes a naive test: a test that does not kill the process in the gap sees both.
The assertion that catches it: `SIGKILL` the worker at the exact point after the
outbox row is written but before delivery, restart, and assert the event is still
pending and the operation's state is the one that was committed. This is the
receipt pattern the repo already tests (`docs/PROVISIONING-RECEIPTS.md:20`); the
draft must reproduce the same evidence shape, not a unit test of an in-memory
queue.

**2 (fatal). A notification failure fails provisioning, or a delivery failure
fails the operation.** The opposite error is equally fatal: the notify call is
awaited inside the effect, so a channel timeout turns a successful provisioning
into a `runtime_failed`. `lab/durable_runtime.py:392-396` shows how carefully the
exit code protocol is guarded today (75 means one specific capacity refusal, every
other failure is generic). Why it passes a naive test: with a healthy channel
nothing fails. The assertion that catches it: run the whole provisioning path with
the channel configured to a closed port and assert the operation's terminal state
and failure code are byte-identical to the healthy run. This is a PASS-by-absence
test: the absence of any effect of the channel on the operation.

**3 (fatal). The outbox is memory, a log file, or the SQLite catalog without a
commit boundary.** `audit_events` is written inside the same SQLite transaction as
catalog mutations (`src/control/catalog.ts:87`, inside `record`), which is the
right shape, but it is local, not tamper-proof, and `docs/CONTROL-PLANE.md:11`
says so. A draft that adds a notifier reading `audit_events` at send time has no
delivery state at all, so it either loses events on a crash or re-sends them
forever. Why it passes a naive test: every event appears once in a quiet run. The
assertion that catches it: restart the delivery loop mid-flight and assert each
event is delivered exactly once, measured in the channel's log.

**4 (fatal). The event is a string, not an identity.** A row that says
`"provisioning failed"` with no environment, no attempt, no runtime identifier and
no reason cannot be deduplicated, retried, or reconciled. The repository's own
receipt carries "a version, random token and exact
environment/runtime/claim/attempt identity" and "contains no credentials or
command payload" (`docs/PROVISIONING-RECEIPTS.md:3`). Why it passes a naive test:
a human reading one message understands it. The assertion that catches it: assert
the event row has a stable identity that a retry of the same operation produces
again, and that two attempts of the same environment are distinguishable. Also
assert the payload contains no credential, which is the absence test.

**5 (serious). Alert deduplication and storm suppression are absent.**
`docs/PROVISIONING-INSPECTION.md` and the admission path already produce repeated
refusals: `lab/admission-check.py:82` records a retained
`failure == 'capacity_exceeded'` fixture, and re-running the check reuses it. A
notifier without suppression will fire on every retry of a permanent condition.
Why it passes a naive test: one event, one message. The assertion that catches it:
retry the same refused fixture five times and assert the channel received one
message (or one plus a stated escalation), not five, while the catalog shows five
attempts. The count comparison catches both the storm and the silent-drop.

**6 (serious). The channel is not actually reproducible, so recovery is not
proven.** A draft that proves delivery once, in sequence, never proves the
redelivery path. `PROJECT.md:145` records that recovery required
explicit reconciliation and a real interruption to be proven. Why it passes a
naive test: the happy path is green. The assertion that catches it: kill the
delivery loop with the channel unreachable, restore the channel, restart, and
assert delivery completes and the operation was never blocked by it.

**7 (serious). Pressure notifications are instantaneous readings presented as
pressure.** `docs/PRESSURE-ADMISSION.md:16` states the gate is "not a continuous
monitor" and that short spikes may be smoothed out by the `avg10` average. A
draft that alerts on a single `avg10` sample will both miss sustained low-level
pressure and fire on a harmless spike. Why it passes a naive test: a synthetic
single reading crosses the threshold and the alert appears. The assertion that
catches it: a sustained-load fixture that keeps pressure modestly above the
threshold for longer than the averaging window must produce exactly one alert,
and a short spike must produce none.

**8 (serious). Recovery results are reported from the recovery script's own claim
of success.** `PROJECT.md:141` records that "a missing verified
target being treated as success" was a real reproduced defect. A notifier that
trusts the recovery helper's exit code alone inherits that defect. Why it passes a
naive test: the helper returns zero. The assertion that catches it: inject the
recovery failure branch and assert the notification reports failure, and that the
notification's claim is re-derived from the target's re-verified state, not from
the helper's own summary.

**9 (dangerous). The count reported is a total from prose.** "6 alerts sent"
where the requirement was six distinct events, or one event six times. Why it
passes a naive test: the number is right. The assertion that catches it: the
artifact must enumerate the event identities and the channel's message
identities, and the acceptance step compares the two sets, not their sizes.

**10 (dangerous). The channel is tested with a stub.** A stub sender proves the
notifier calls the channel, not that the channel works. Why it passes a naive test:
the stub returns 200. The assertion that catches it: at least one live test per
channel, against the real sink, with the sink's own record.

## C.3 Acceptance tests

**C-T1. Durable outbox survives the crash boundary, PASS includes no duplicate.**
Command shape:
```
bun lab/notification-outbox-check.ts --crash-after-outbox --channel local-sink \
  --sink-log /tmp/sbarbase-plan/evidence/sink.log --artifact /tmp/sbarbase-plan/evidence/outbox-crash.json
```
Fixture: a refused fifth allocation through `lab/admission-check.py`'s own fixture
path (the repository's retained `capacity_exceeded` fixture exists and is reused
on purpose, `lab/README.md:115`), and a delivery loop that can be `SIGKILL`ed at a
named stage. The crash stage must be a real signal, not a flag.
Observable: after restart, exactly one event row, exactly one message in the
sink's log, and the catalog's `provision_jobs.failure` unchanged.
Failure it must be able to produce: killing after the sink accepted the message
but before the delivery receipt committed must produce exactly one message after
the restart, not two. If the implementation is send-then-record, it produces two,
and the test must be able to show that.

**C-T2. Notification failure cannot change the operation, PASS is an absence.**
Command shape:
```
bun lab/notification-isolation-check.ts --channel tcp://127.0.0.1:1 \
  --artifact /tmp/sbarbase-plan/evidence/notification-isolation.json
```
Fixture: the channel pointed at a closed port for the whole run; the refused
allocation and a reconciliation pass driven through the real worker
(`/usr/bin/python3 lab/worker.py --upstream --settle-only`, `lab/README.md:131`),
not a call into the notifier.
Observable: the job's terminal state, its failure code, its attempt count and the
sink's received count (zero), compared field by field against a healthy-channel
run recorded in the same artifact.
Failure it must be able to produce: any field differing from the healthy run is
red. The PASS condition here is the absence of any observable effect of the
channel on the operation.

**C-T3. Exactly-once under a real retry storm, PASS is the absence of a storm.**
Command shape:
```
bun lab/notification-dedup-check.ts --repeat 5 --sink-log /tmp/sbarbase-plan/evidence/sink-dedup.log \
  --artifact /tmp/sbarbase-plan/evidence/notification-dedup.json
```
Fixture: one permanently refused environment retried five times through the real
worker path, each retry reusing the retained fixture and not allocating a new
environment.
Observable: the catalog's attempt count (five), the event identity set, and the
sink's message set. One identity must map to one message.
Failure it must be able to produce: five messages for one event is red; zero
messages is also red, because silence is the failure mode the user is complaining
about.

**C-T4. Pressure alert is sustained, not instantaneous.**
Command shape:
```
bun lab/pressure-alert-check.ts --mode sustained --seconds 60 --sink-log /tmp/sbarbase-plan/evidence/pressure-alert.log \
  --artifact /tmp/sbarbase-plan/evidence/pressure-alert.json
bun lab/pressure-alert-check.ts --mode spike --seconds 3 --sink-log /tmp/sbarbase-plan/evidence/pressure-spike.log \
  --artifact /tmp/sbarbase-plan/evidence/pressure-spike.json
```
Fixture: two runs, one that holds a resource just above a documented threshold
(`lab/pressure_admission.py:6`) for longer than the `avg10` window, one that
crosses it briefly. Both need the load to be real enough to move the kernel's own
average, which the artifact must record from `/proc/pressure/*` or the container's
own `/sys/fs/cgroup/*.pressure`.
Observable: message count and timestamp per run, plus the raw series.
Failure it must be able to produce: the sustained run must yield at least one
alert and the spike run zero. If both yield one, the alert is reading an instant,
not pressure; if both yield zero, the threshold is unreachable and the test must
report that instead of passing quietly.

**C-T5. Recovery result comes from re-verified state, PASS is the absence of a
false success.**
Command shape:
```
/usr/bin/python3 lab/notification-recovery-check.py --inject missing-target \
  --sink-log /tmp/sbarbase-plan/evidence/recovery-notify.log \
  --artifact /tmp/sbarbase-plan/evidence/notification-recovery.json
```
Fixture: the recorded defect shape from `PROJECT.md:141`, a missing
verified target, reproduced with the existing cleanup failure injection cases.
Observable: the notification's stated outcome, the target's independently
re-read state, and the two agreeing.
Failure it must be able to produce: an implementation that trusts the helper's
exit code sends "recovery succeeded"; the assertion re-reads the target and finds
no verified target, so red. This test must be able to fail, which means the
fixture cannot be pre-verified.

## C.4 Forbidden shortcuts, stated as failures

- **Pass: letting a notification failure fail provisioning.** Stated as a
  failure: the operation's terminal state differs by one field between a healthy
  channel and a dead channel.
- **Pass: sending before the outcome is durable.** Stated as a failure: a crash
  in the gap produces a message with no recorded fact, or a fact with no message
  and no pending row.
- **Pass: an in-memory queue, or a bare log line as the outbox.** The only
  durable facts this repository trusts are ones with a commit boundary, a token
  and an identity (`docs/PROVISIONING-RECEIPTS.md:3`). A design without those is a
  failure.
- **Pass: one channel with no delivery state.** "The webhook is the sink" is a
  failure: without a per-event delivery state, redelivery and deduplication are
  both impossible.
- **Pass: a credential or a command payload inside the notification.** The
  receipt protocol says "contains no credentials or command payload"
  (`docs/PROVISIONING-RECEIPTS.md:3`); an alert carrying a connection string,
  a password or a full environment dump is a failure even if it arrives.
- **Pass: alerting on the notifier's own counter.** The count must come from the
  channel's log.
- **Pass: a stubbed channel.** At least one real sink test per channel.
- **Pass: a total instead of per-event identities.** Set comparison, not size
  comparison.
- **Pass: presenting an admission-time refusal as proof that pressure was
  handled.** `docs/PRESSURE-ADMISSION.md:16` already says the gate is not a
  monitor; an alert that only fires at admission is not a pressure alert.
- **Pass: silently swallowing the unresolvable event.** An event that cannot be
  delivered after a stated bound must remain visibly pending and must block
  nothing, or it must escalate. Deleting it is a failure. The repository's own
  rule for pending receipts is "do not delete a pending receipt"
  (`docs/PROVISIONING-RECEIPTS.md:32`); the same rule applies here.

## C.5 Questions the draft must answer before implementation

1. What is the commit boundary: which transaction writes the fact and the event
   together, and what is the exact crash point the test kills at?
2. Where does the outbox live: the local SQLite catalog
   (`src/control/catalog.ts:53`), a file beside the durable state, or PostgreSQL?
   The answer must work for a notification about the database, which cannot be
   stored in the database that is down.
3. What is the event identity, and what makes two events the same event?
4. What are the channels, and what is the failure policy for each: retry with
   what backoff, at what bound, then what?
5. Does the operator's own identity get the notifications, or a separate
   endpoint? A notification that requires management Auth to be healthy is
   useless exactly when management Auth is what failed.
6. What is the suppression policy, and how is a permanent condition distinguished
   from a transient one?
7. Which events are worth waking someone for (`docs/OPERATOR-SETUP.md` has no
   paging concept today), and which are records only? `audit_events` already
   records mutations; a design that duplicates every audit row as an alert makes
   the channel useless.
8. How does the outbox survive an export and a restore, and does a restored
   outbox replay old events? `docs/reviews/security-operations.md:40` states the
   rule exactly: "Quiesce or disable outbound jobs, webhooks, and email during
   restores. A restored scheduler can replay already completed side effects."
   The draft must say how it suppresses replayed alerts.

### Questions that have no good answer on one shared PostgreSQL engine

- **Notifying about a database that is the thing that failed.** If the outbox is
  in the shared PostgreSQL cluster and the cluster is the incident, nothing can
  be written or read. The honest answer is that the outbox must live outside the
  cluster it reports on, which means the local SQLite catalog or a file, and the
  draft must accept the durability trade that comes with that.
- **A single durable order across the cluster and the notifier.** There is no
  distributed transaction between the shared cluster's commit and the outgoing
  message. Any design here is at-least-once with deduplication, and a draft that
  claims exactly-once across the boundary without naming the deduplication key is
  wrong.
- **Backpressure that knows the difference between a busy engine and a broken
  one.** Connection saturation in the shared cluster looks identical to a
  connection-limit refusal (`docs/CONNECTION-BUDGET.md`), and both look identical
  to a client's misuse. The event schema can carry a reason code, but the shared
  engine itself cannot distinguish them for the notifier; the draft must state
  which component is responsible for the classification and that it is a
  judgement, not a measurement.

---

# Severity ranking, for the parent to verify in this order

**Topic A**
- S1 continuous response is missing (`docs/RESOURCE-ADMISSION.md:24`); every
  accepted env can be starved after admission. Verify first.
- S1 no per-tenant I/O control exists anywhere in the tree.
- S1 the shared database container is the only ceiling for all databases
  (`lab/durable_runtime.py:187-189`) and it is 1 GiB, 1 CPU.
- S1 no per-tenant disk budget (`docs/RESOURCE-ADMISSION.md:12`).
- S2 pressure is measured on shared containers only (`lab/pressure_admission.py:7`).
- S2 the gateway budget is per process (`src/gateway/concurrency.ts:34`) with no
  floor per environment.
- S2 a connection limit is not a resource budget (`docs/CONNECTION-BUDGET.md:22`).
- S2 priority class may be a column, not a share.
- S3 measurement quoted from a document, or a total instead of a distribution.

**Topic B**
- S1 the environment Auth builder enables email and auto-confirm
  (`lab/run.py:129`), so "disabled by default" is currently false. Verify first.
- S1 no per-environment SMTP credential exists, and one shared credential is the
  path of least resistance.
- S1 the SMTP password would land in `Config.Env`
  (`lab/durable_runtime.py:121`, `:131`).
- S1 sender identity per installation instead of per environment.
- S2 the rate limit is the provider's.
- S2 templates and redirect targets (`lab/run.py:124-125`, `http://localhost`).
- S2 a stub mailer used as the witness.
- S3 verification proven from the platform's own table.

**Topic C**
- S1 no notification system exists at all; the nearest durable record is a local
  SQLite table described as not tamper-proof (`docs/CONTROL-PLANE.md:11`). Verify
  first.
- S1 the send-versus-durable ordering.
- S1 a channel failure being able to change the operation's outcome.
- S2 no delivery state, no deduplication key.
- S2 pressure alerts fired on an instant rather than a sustained crossing.
- S2 recovery success taken from the helper's own claim
  (`PROJECT.md:141`).
- S3 totals instead of event identity sets.

# The three rules a draft cannot satisfy with prose

1. Every number in the draft must be regenerable by a command in the same session,
   and the artifact must be named. `docs/RESUME-CHECKPOINT.md:730` is the
   precedent for what happens otherwise.
2. Every test must name the failure it can produce, in the shape of
   `docs/RESUME-CHECKPOINT.md:593` and `:714`.
3. Every hostile case must be hostile in the fixture itself, not in the prose
   around it (`docs/NOISY-NEIGHBOR.md:13`).