# Operator notification and alerting system

Status: design record, 2026-09-21. Nothing here is implemented yet. Written by a
delegated design pass and reviewed by the parent the same day: the load bearing claims were
re-read against the code and the counts were reproduced (audit_events 224 and 8 in the two
catalogs, the environment Auth at 256m and 0.25 CPU, the database at 1024m and 1 CPU on one
volume, no blkio or cpu-shares anywhere in the tree, the pin count assertion at
lab/test_pinned_images.py:38). Unverified items are labelled unconfirmed in place.

## Parent review note, 2026-09-21

A container's environment is readable in cleartext by anyone who can reach the Docker daemon.
Verified live on this host: a container created with `--env-file` pointing at a 0600 file, then
`docker inspect --format '{{json .Config.Env}}'`, printed the value. So the redaction gate this
design requires must not rest on the file mode of anything: it must be a construction rule, the
allow-list the message is built from, and it must fail closed when a field is not on it. The
same fact bounds every channel's credential: treat a webhook secret or a mail password as
readable by an operator with daemon access, and say so rather than implying otherwise.


Executable design document. Scope: sbarbase on git main, one server, hierarchy
installation > organization > project > environment, original pinned Supabase
components, SQLite control catalog, provisioning worker with durable receipts, composed
Bun gateway, admission checks, and the platform console.

Status of this document: design only. Nothing in the repository is changed by it. Every
command in section 8 is meant to be run from the repository root after the listed file is
written. Every claim about current behaviour carries a `file:line` citation that was read
while writing this document.

Package manager is `bun` everywhere; `npm` is never used. The runtime has Bun 1.3.14 and
`/usr/bin/python3` 3.14.7 (verified on this host: `bun --version`, `/usr/bin/python3 --version`).

## 0. What exists today and what is missing

The operator discovers problems by looking. Concretely, the only durable record of a
platform event is `audit_events` (`src/control/catalog.ts:53-55`), written by the private
`record()` helper (`src/control/catalog.ts:86-89`) inside the same immediate transaction as
the mutation that produced it (`src/control/catalog.ts:93-97`, `:105-118`, `:128-141`,
`:145-149`, `:153-159`, `:202-223`, `:224-231`, `:254-273`, `:276-284`, `:290-299`,
`:314-327`). It stores `actor`, `action`, `subject`, `detail` and `at`.

Measured on this checkout today with `sqlite3`:

```
sqlite3 .lab/upstream/control.sqlite "SELECT count(*) FROM audit_events;"
224
sqlite3 .lab/control.sqlite "SELECT count(*) FROM audit_events;"
8
```

The measured `audit_events` counts do not match the 211 quoted in the task brief. The
retained upstream catalog now holds 224 audit rows, the component lab catalog holds 8. The
difference is a real state change, not a documentation error; the design below does not
depend on either number.

Nothing reads those rows except the console's provisioning status, which is a per-row poll
of one environment (ui/Environments.tsx:7) with a 3 second refresh while something is
pending (ui/Environments.tsx:9-10). There is no mail transport of any kind in the tree
(`grep -rn "smtp\|mailer" lab src ui` returns only per-environment Auth settings, see
section 3.1), no webhook, no alert, no outbox.

## 1. Event inventory

For each event: what it is, where it is observable today, whether that observation is
durable, and what is missing for notification.

### 1.1 Provisioning outcomes

| Event | Observable today | Durable today | Missing |
|---|---|---|---|
| Environment created and queued | `catalog.createEnvironment` writes `provision_jobs` state `queued` and audit `environment.created` (`src/control/catalog.ts:153-159`) | Yes: `provision_jobs.state` plus an audit row | no push |
| Provision started | audit `provision.started` inside `claimProvision` (`src/control/catalog.ts:218`) | Yes | no push, and low value on its own |
| Provision succeeded | `finishProvision` sets `succeeded` and audits `provision.succeeded` (`src/control/catalog.ts:224-231`) | Yes | no push |
| Provision failed, runtime | `finishProvision` sets `failed` with `failure='runtime_failed'` and audits `provision.failed` with the failure code in `detail` (`src/control/catalog.ts:228-230`) | Yes | no push. A row only exists for one environment; the console shows a generic notice and only while that project page is open (ui/Environments.tsx:16) |
| Provision failed, capacity refused | `applyProvisionReceipt` maps worker exit code 75 to `finishProvision(...,'capacity_exceeded')` (`src/control/catalog.ts:234-251`, especially `:247`) | Yes, as the coarse code `capacity_exceeded` (`ProvisionFailure` union at `src/control/catalog.ts:9`) | no push; the operator sees the label `Capacity limit` (ui/Environments.tsx:13) and a notice (ui/Environments.tsx:15) only while looking |
| Provision cancelled by revoked authority | `claimProvision` audits `provision.cancelled` (`src/control/catalog.ts:212-213`) | Yes | no push |
| Retry requested | `retryProvision` audits `provision.retried` (`src/control/catalog.ts:275-284`) | Yes | no push; low severity |
| Preflight requeue and retry limit | `recoverPreflightReceipt` audits `provision.preflight_requeued` or `provision.preflight_retry_limit` (`src/control/catalog.ts:254-273`, `:271`) | Yes | no push; the retry limit is exactly the case the operator must hear about |

### 1.2 Admission refusals

All admission refusals are raised inside the worker's native child and are reduced to a
single exit code on the way out. The fine reason is not durable.

- Environment count limit: `raise AdmissionLimitError('Local runtime admission limit reached')`
  at `lab/durable_runtime.py:278-279`.
- Resource headroom: `resource_admission.refusal(...)` at `lab/durable_runtime.py:281-285`.
  The safe reason strings come from `lab/resource_admission.py:26-38`:
  `measurement_unavailable` (`:31`), `memory_headroom` (`:32`), `disk_headroom` (`:34`),
  `inode_headroom` (`:36`).
- Host pressure: `pressure_admission.refusal(...)` at `lab/durable_runtime.py:286-287`.
  Thresholds are `cpu_some10` 50.0, `io_full10` 20.0, `memory_full10` 1.0
  (`lab/pressure_admission.py:6`), and the refusal returns the metric name
  (`lab/pressure_admission.py:36-48`).
- Connection budget: `lab/durable_runtime.py:288-290` against
  `connection_budget.fits(...)` (`lab/connection_budget.py:8-12`).
- The child then publishes exit code 75 (`lab/durable_runtime.py:387-389`) and exits 75
  (`lab/durable_runtime.py:392-395`). Every other failure collapses to the fixed sentence
  at `lab/durable_runtime.py:396-398`, deliberately, because "Secrets, SQL and HTTP
  response bodies must never reach console output".

Durable today: the coarse code only, `capacity_exceeded`, written by
`applyProvisionReceipt` (`src/control/catalog.ts:247`). The fine reason exists for the
duration of one child process and is then lost. Missing: a durable safe reason code, and a
push.

Installation startup refusals are a separate path. `CombinedAdmission.check_current`
raises with the reason appended (`lab/combined_admission.py:53-55`; reason vocabulary at
`lab/combined_admission.py:16-22`: `measurement_unavailable`, `unbounded_limits`,
`installation_ceiling`, `host_memory_headroom`, `host_cpu_headroom`, and
`Host pressure too high` at `:55`). `lab/installation_runtime.py:111-118` catches it,
writes the full traceback to `.lab/upstream/diagnostics/` at mode 0600 and prints only the
diagnostic path. Durable today: a private diagnostic file. Missing: a catalog event and a
push.

### 1.3 Source and target fence changes

- Database fence and unfence: `lab/source_fence.py:19` (`fence`),
  `:37` (`unfence`, "Explicit operator rollback only, never automatic during
  provisioning"), `:44` (`prepare_export`).
- Export fence record: `lab/recovery-export.py:112-114` refuses a second fence record and
  writes the phase `exported-and-fenced` at `:153`. Path:
  `.lab/upstream/export-fence-<environment>.json`.
- Routing fence, meaning maintenance pause, stage placement and resume:
  `catalog.changeRuntimeRouting` writes `runtime_routing` and audits
  `runtime.routing_pause`, `runtime.routing_stage`, `runtime.routing_resume`
  (`src/control/catalog.ts:309-328`, audit at `:325`). 152 such audit rows exist in the
  retained upstream catalog today (52 pause, 51 resume, 49 stage).
- Cutover journal: `.lab/upstream/cutover-operation.json`, phase recorded as
  `target-stopped-routing-paused` and later `target-services-verified-routing-paused`
  (docs/RESUME-CHECKPOINT.md:15, docs/INDEPENDENT-RESTORE.md:100).

Durable today: routing changes are durable in `audit_events` and `runtime_routing`;
database fences are durable only as private JSON records. Missing: a push on both, and a
durable catalog record for the database fence.

### 1.4 Backup and restore results

- Export: 34 checks in `docs/evidence/recovery-export-checks.json`
  (`lab/recovery-export.py:161`); the durable per-environment fence record is written at
  `lab/recovery-export.py:153`.
- Restore: `lab/recovery-restore-db.py:94` writes the descriptor with status
  `initializing` and stage `allocate`; `:106` advances the stage; `:194` sets
  `database-verified`; `cleanup_target` (`:34-56`) sets `cleanup-failed` or `failed` and
  never upgrades a failure to success; `:62` refuses to run over an existing descriptor;
  `:208` prints a fixed sentence: "Independent database restore failed; inspect private
  stage descriptor, sensitive output withheld."
- Interrupted restore reconciliation: `lab/recovery_reconcile.py` under the operation lock,
  and `lab/retire_recovery_target.py --reason TEXT` for retirement
  (docs/SERVER-DEPLOYMENT.md:301-318).

Durable today: the private descriptor `.lab/upstream/recovery-target.json` and the evidence
files. Missing: a catalog event and a push for verified, interrupted, failed and
cleanup-failed.

### 1.5 Host pressure crossing a threshold

`lab/pressure_admission.py:21-33` can read cgroup v2 pressure for
`sbarbase-durable-db` and `sbarbase-durable-storage` (`:7`), and `:36-48` can judge it
against the thresholds at `:6`. `lab/combined_admission.py:54` adds host-level
`/proc/pressure/*`. But every one of those reads happens only as part of an admission
decision. Durable today: nothing. Missing: a periodic sampler, because a threshold crossing
that happens between two admission decisions is currently invisible even to an operator who
is looking.

### 1.6 Supervisor and worker lifecycle

- Worker restart after exit: `lab/dev.py:85-97`. The trail is a print:
  `Provisioning worker exited; reconciling retained operations.` (`lab/dev.py:96`), and the
  descriptor `.lab/upstream/supervisor.json` records `workerRestarts`
  (`lab/dev.py:70-75`).
- Restart limit: `raise RuntimeError('Worker restart limit reached; inspect retained
  state')` at `lab/dev.py:93-94`, which stops the whole installation with a line on stderr
  and no push.
- Worker process output: `console.log(\`Provision ${job.environment}: ${ok?'succeeded':'failed'}\`)`
  at `lab/worker.ts:34`, and `process.exitCode=1` for a failed non-watch run at
  `lab/worker.ts:38`.
- Installation runtime failures: the private diagnostic path printed at
  `lab/installation_runtime.py:118`.

Durable today: a descriptor count and a printed line. Missing: a durable event and a push.

### 1.7 Console and management mutations

Membership change (`src/control/catalog.ts:125-141`), project creation (`:143-149`),
project ownership transfer (`:290-299`), installation initialization (`:103-118`). All
durable as audit rows. Missing: a push. Only two of them earn a message by default: owner
demotion or removal, and project ownership change, because both change who can do what.

### 1.8 The subset that must be told to the operator

Default delivery enabled: every `critical` and `warning` row below, plus `info` only when
enabled.

| Kind | Severity | Trigger |
|---|---|---|
| `provision.failed` | critical | `finishProvision` failure `runtime_failed` |
| `provision.capacity_refused` | warning | `finishProvision` failure `capacity_exceeded`, with the durable safe reason |
| `admission.runtime_refused` | warning | installation startup refusal (`lab/installation_runtime.py:111-118`) |
| `admission.pressure` | warning | host pressure at or above a threshold, sampled |
| `admission.headroom_changed` | warning | resource headroom refusal on a provisioning attempt |
| `provision.retry_limit` | critical | `provision.preflight_retry_limit` |
| `worker.restart` | warning | supervisor restarted the worker |
| `worker.restart_limit` | critical | `lab/dev.py:93-94` |
| `fence.applied` | critical | a database fence was applied |
| `fence.released` | warning | a database fence was released by explicit operator rollback |
| `routing.paused` | warning | `runtime.routing_pause` |
| `routing.resumed` | info | `runtime.routing_resume` |
| `backup.export_completed` | info | export phase `exported-and-fenced` |
| `backup.export_failed` | critical | export refused or failed |
| `restore.verified` | critical | descriptor status `database-verified` |
| `restore.failed` | critical | descriptor status `failed`, `interrupted` or `cleanup-failed` |
| `notifier.channel_failed` | critical | a channel failed permanently, sent on the remaining channels |
| `membership.owner_changed` | critical | last owner demoted or removed, or owner membership changed |
| `project.ownership_changed` | critical | `project.ownership_changed` |

## 2. Durable outbox design

### 2.1 Tables

Added to the existing catalog schema in `src/control/catalog.ts`, in the same
`db.exec` string that creates the other tables (`src/control/catalog.ts:21-57`), so a new
field and table arrive the same way the `failure` column did at
`src/control/catalog.ts:58-62`.

```sql
CREATE TABLE IF NOT EXISTS notification_outbox(
  id TEXT PRIMARY KEY,
  at INTEGER NOT NULL,
  last_at INTEGER NOT NULL,
  window_until INTEGER NOT NULL,
  occurrences INTEGER NOT NULL DEFAULT 1,
  digest_sent INTEGER NOT NULL DEFAULT 0 CHECK(digest_sent IN (0,1)),
  kind TEXT NOT NULL,
  severity TEXT NOT NULL CHECK(severity IN ('info','warning','critical')),
  dedupe_key TEXT NOT NULL,
  organization TEXT,
  project TEXT,
  environment TEXT,
  runtime TEXT,
  actor TEXT NOT NULL,
  reason TEXT NOT NULL,
  detail TEXT NOT NULL,
  expires_at INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS notification_delivery(
  event TEXT NOT NULL REFERENCES notification_outbox(id),
  channel TEXT NOT NULL CHECK(channel IN ('email','webhook')),
  state TEXT NOT NULL CHECK(state IN ('pending','claimed','delivered','failed')),
  attempts INTEGER NOT NULL DEFAULT 0,
  claim TEXT,
  claim_at INTEGER,
  next_attempt_at INTEGER NOT NULL,
  last_error TEXT,
  delivered_at INTEGER,
  PRIMARY KEY(event,channel));

CREATE INDEX IF NOT EXISTS notification_due
  ON notification_delivery(state,next_attempt_at);
CREATE INDEX IF NOT EXISTS notification_windows
  ON notification_outbox(dedupe_key,window_until);
```

Two tables, not one, on purpose. The event is a fact about the installation and happens
once. Delivery is per channel and can succeed on one channel and fail on another. Folding
both into one row would make a mail success and a webhook failure overwrite each other.

Column rules, enforced by `notify()` in section 2.2 and not by convention:

- `id` is a `randomUUID()` (`node:crypto`, already imported at `src/control/catalog.ts:3`).
  It is the idempotency key and it never changes.
- `organization`, `project`, `environment`, `runtime` are catalog identifiers only. Nothing
  else is allowed in them. `actor` passes the existing `actor()` validator
  (`src/control/catalog.ts:69-73`).
- `reason` is a member of a closed enum declared in `src/control/notification.ts`. It is
  never free text.
- `detail` is JSON built only from the closed key set of the kind, section 6.
- `last_error` records a short token such as `webhook_unreachable`, `webhook_timeout`,
  `webhook_status_500`, `smtp_refused`, `smtp_temporary_failure`, `redaction_refused`. No
  upstream body, no exception message.

### 2.2 Enqueue in the same transaction as the state change

`Catalog` gains one private method next to `record()`:

```
private notify(kind,severity,dedupeKey,subject,actor,reason,detail,channels) {
  // subject = {organization,project,environment,runtime} or a subset
  // channels = the enabled channel list resolved once at process start, no I/O here
}
```

It is called from inside the existing `.immediate()` transactions, in the same position as
`record()`:

- `finishProvision` (`src/control/catalog.ts:224-231`): after the guarded `UPDATE` succeeds
  and `record()` runs, call `notify(...)` with `provision.failed`,
  `provision.preflight_retry_limit` or `provision.capacity_refused`.
- `createEnvironment` (`src/control/catalog.ts:153-159`): `provision.retry_limit` is not
  here, but `environment.created` is, if the operator enables info messages.
- `changeRuntimeRouting` (`src/control/catalog.ts:309-328`): `routing.paused`,
  `routing.staged`, `routing.resumed`.
- `retryProvision` (`src/control/catalog.ts:275-284`) and `recoverPreflightReceipt`
  (`src/control/catalog.ts:254-273`).
- `setMember` (`src/control/catalog.ts:125-141`) and `transferProject`
  (`src/control/catalog.ts:290-299`).

For events that do not originate in a catalog method, the writer that owns the state
change calls `notify` in one explicit transaction. Two cases:

- Export and restore results. `lab/recovery-export.py:153` and
  `lab/recovery-restore-db.py:94-106,194` already publish through `runtime.atomic`
  (`lab/durable_runtime.py:36-46`). They gain one `catalog.notifyWrite(...)` call inside the
  same Python transaction that writes the descriptor. Because the descriptor is a file and
  the outbox is SQLite, the two are not one transaction; the ordering rule is: publish the
  outbox row first, then the descriptor. A crash between them yields one duplicate-free
  message about a restore that did not reach its announced status, which is a true statement,
  and the descriptor is the authority the operator inspects next. State this ordering in the
  code comment; do not pretend it is atomic.
- Supervisor lifecycle. `lab/dev.py:96` and `:93-94` gain a `bun lab/notify-enqueue.ts`
  call: a small script that opens the catalog, runs one `notifyWrite`, and closes. It holds
  no lock and writes one row, so it does not become a claimer.

This is the lost-send guarantee: the outbox row commits in the same SQLite transaction as
the state change, so a state change can never exist without its event, and an event can
never exist without its state change. Both `provision_jobs` and `notification_outbox` live
in one database with `PRAGMA journal_mode=DELETE` and `busy_timeout=5000`
(`src/control/catalog.ts:21`), so the transaction is a single durable commit.

### 2.3 Claim

`claimNotifications(limit)` mirrors `claimProvision` (`src/control/catalog.ts:202-223`)
exactly: one `.immediate()` transaction, select candidates, update with a guard, return the
claimed rows.

```
claimNotifications(limit = 20):
  within db.transaction(...).immediate():
    rows = SELECT d.event,d.channel,o.* FROM notification_delivery d
             JOIN notification_outbox o ON o.id=d.event
             WHERE d.state='pending' AND d.next_attempt_at <= now
                OR d.state='claimed' AND d.claim_at <= now - LEASE_MS
             ORDER BY o.at LIMIT ?
    for each row:
      claim = randomUUID()
      UPDATE notification_delivery
         SET state='claimed', claim=?, claim_at=?, attempts=attempts+1
         WHERE event=? AND channel=? AND state IN ('pending','claimed')
      return {event, channel, claim, payload}
```

Exactly one process runs this, because exactly one process holds the installation worker
lock (`lab/worker.py:27-30`, enforced again at `lab/worker.ts:7`). That is why the claim
does not need a lease-stealing rule for correctness and why the design does not introduce a
second claimer (section 5).

### 2.4 Settlement, retry, exactly once

```
settleNotification(event, channel, claim, outcome, error?, nextAttemptAt?):
  within db.transaction(...).immediate():
    if outcome === 'delivered':
      changes = UPDATE notification_delivery
                  SET state='delivered', delivered_at=?, claim=NULL, last_error=NULL
                WHERE event=? AND channel=? AND claim=? AND state='claimed'
      if changes !== 1 throw new Error('Stale notification claim')
    else:
      attempt = SELECT attempts FROM notification_delivery WHERE event=? AND channel=?
      permanent = outcome === 'failed'
      changes = UPDATE notification_delivery
                  SET state=?, claim=NULL,
                      last_error=?,
                      next_attempt_at=?
                WHERE event=? AND channel=? AND claim=? AND state='claimed'
      if changes !== 1 throw new Error('Stale notification claim')
      if permanent or attempt >= MAX_ATTEMPTS: state='failed'
      else: state='pending', next_attempt_at = now + backoff(attempt)
```

This is the same shape as `finishProvision`'s stale-claim guard
(`src/control/catalog.ts:224-231`, `throw new Error('Stale provisioning claim')` at `:229`)
and as `applyProvisionReceipt`'s duplicate settlement check
(`src/control/catalog.ts:240-245`, `throw new Error('Provisioning receipt mismatch')`).

Exactly once, stated precisely:

- Exactly-once enqueue is guaranteed by transactionality: section 2.2.
- Exactly-once settlement is guaranteed by the claim guard: only the attempt holding the
  current `claim` can move the row, and a retry mints a new `claim`, so an old attempt can
  never settle the newer one. `attempts` increments on every claim, so a replay of the same
  attempt is a stale claim and is rejected.
- Exactly-once delivery to an external system is not achievable and this document does not
  claim it. The residual window is between the channel returning success and the
  `settleNotification` commit: a crash there leaves the row `claimed`, the lease re-claims
  it (section 2.3), and the message is sent a second time. That duplicate is preventable by
  every receiver, because every request carries `X-Sbarbase-Event-Id` and
  `X-Sbarbase-Delivery-Id` and every mail body and subject carries the same event id. The
  design therefore guarantees no double send *of distinct events* and an at-most-once
  duplicate *of one event id* in a narrow crash window, detectable and deduplicable by the
  receiver.

Lost-send avoidance: `delivered` is written by one statement, guarded by `state='claimed'`,
and only after the channel returned success. A crash before the channel call leaves the row
`pending`, which the next iteration picks up. A crash after the call leaves it `claimed`
with a stale `claim_at`, which the lease requeues. No age-based deletion exists for a row
that is not delivered (section 2.5).

### 2.5 Aging out

Three separate mechanisms, each bounded:

1. Retry exhaustion. After `MAX_ATTEMPTS` (8) the delivery row becomes `failed` and stays
   visible. It is not deleted, because the operator must be able to see that a channel is
   broken.
2. Escalation. When a delivery row reaches `failed`, the drainer enqueues exactly one
   `notifier.channel_failed` event, with `dedupe_key = 'notifier.channel_failed|' + channel`
   and a suppression window of 6 hours, delivered on the channels that are not the failed
   one. If every channel has failed, the only observer is the console (section 2.6).
3. Retention. `expires_at = at + 30 days`. The drainer prunes with a bounded statement, 500
   rows per iteration, only rows past `expires_at` with no `pending` or `claimed` delivery:

```sql
DELETE FROM notification_outbox
WHERE id IN (SELECT o.id FROM notification_outbox o
             WHERE o.expires_at < ?
               AND NOT EXISTS (SELECT 1 FROM notification_delivery d
                               WHERE d.event=o.id AND d.state IN ('pending','claimed'))
             LIMIT 500);
```

The same iteration unlinks the orphan delivery rows. Retention is deliberately longer than
any suppression window so a digest always finds its row.

### 2.6 What the console shows

The console is the last-resort observer when every channel is down. The management handler
gains one read-only route, `GET /management/v1/notifications`, owner and admin only,
returning `id`, `kind`, `severity`, `at`, `occurrences`, and per channel `state`,
`attempts`, `last_error`. It returns no `detail` payload beyond the already-safe fields and
no recipient address. The `cache-control: no-store` and `x-content-type-options: nosniff`
headers already applied by `reply()` (`src/control/http.ts:4-6`) are reused.

## 3. Channels

Channel list is read once per process from configuration, so no I/O happens inside an
enqueue transaction. Non-secret configuration lives in `.lab/upstream/notifications.json`
(0600, inside the ignored `.lab/` tree, `.gitignore:2`). The one secret, the webhook HMAC
key, lives in `.secrets/upstream/notifier.json` (0600, `.gitignore:1`).

### 3.1 Email through the environment-independent operator mail path

There is no operator mail path today. What exists is per-environment Auth mail
configuration: `GOTRUE_EXTERNAL_EMAIL_ENABLED` and `GOTRUE_MAILER_AUTOCONFIRM` in the
environment Auth configuration (`lab/run.py:121-130`, `:129`) and, separately, the
management realm's `GOTRUE_DISABLE_SIGNUP` and `GOTRUE_MAILER_AUTOCONFIRM` overrides
(`lab/durable_runtime.py:353-354`). Both belong to a Supabase Auth instance, and both are
per environment.

Those must not carry operator notifications, for two reasons that are structural, not
stylistic:

1. The moment the operator most needs a message is the moment an environment's Auth is
   stopped or its database is fenced (`lab/source_fence.py:19`, `lab/recovery-export.py:112-114`).
   A mail path that depends on the thing that just went down is not a mail path.
2. The operator identity is a management Auth user in the `management` database
   (`lab/durable_runtime.py:340-359`), not a user of any environment. An environment's
   mailer has no operator recipient to send to.

The design adds exactly one installation-scoped SMTP submission, configured once, used by
no environment, and independent of every environment's lifecycle. Recipient resolution:
`.lab/upstream/notifications.json` field `email.to`; if absent, fall back to the `email`
field of the bootstrap journal `BootstrapState` (`src/control/bootstrap.ts:4`, written at
`:28` into `.secrets/upstream/bootstrap.json` per `lab/bootstrap.ts:8`). The recipient is
never copied into the catalog, because the catalog must not accumulate personal data.

Configuration, `.lab/upstream/notifications.json`:

```json
{
  "schema": 1,
  "email": {"enabled": true, "host": "172.18.0.4", "port": 1025,
            "from": "sbarbase@installation.invalid", "to": "operator@example.invalid",
            "tls": "none"},
  "webhook": {"enabled": true, "url": "http://127.0.0.1:8099/sbarbase",
              "secretFile": ".secrets/upstream/notifier.json"}
}
```

`.secrets/upstream/notifier.json`:

```json
{"schema": 1, "webhookSecret": "<64 hex characters>"}
```

`tls` is one of `none`, `starttls`, `tls`. `host` may be a container IP on an internal
network, which is the normal local case: the project's own convention is that internal
bridge endpoints are reachable by this Linux host and are not published
(lab/run.py:60).

Delivery is one SMTP submission per message using a fixed envelope, with a bounded
timeout. The message carries the subject `[sbarbase] <severity> <kind> <subject id>` and a
body rendered only from the closed field set of section 6. Nothing from the environment
configuration, no connection string, no credential.

### 3.2 Generic webhook

One HTTP `POST`, `content-type: application/json`, to the configured URL. Payload shape,
identical to the email body fields, `schema` 1:

```json
{
  "schema": 1,
  "id": "8f3c1a52-6d2e-4b1a-9f77-0a1b2c3d4e5f",
  "delivery": "6b7e0d41-2c58-4c2f-8a10-9e8d7c6b5a49",
  "kind": "provision.failed",
  "severity": "critical",
  "at": "2026-09-21T05:30:00.000Z",
  "last_at": "2026-09-21T05:30:00.000Z",
  "occurrences": 1,
  "window_seconds": 0,
  "subject": {"organization": "<uuid>", "project": "<uuid>",
              "environment": "<uuid>", "runtime": "e_0123456789abcdef01234567"},
  "actor": "system",
  "reason": "runtime_failed",
  "reason_class": "provisioning_outcome",
  "summary": "Environment provisioning failed and retained state needs inspection.",
  "action": "Inspect the retained operation, then retry from the console."
}
```

Every key is in a closed set. `summary` and `action` are fixed strings chosen by `kind` and
`reason` from a table in `src/control/notification.ts`; no caller supplies them.

Authentication of the webhook call, reusing the HMAC construction the project already uses
to sign tokens (`lab/durable_runtime.py:77-82`, `hmac.new(secret, message, sha256)`):

```
X-Sbarbase-Schema: 1
X-Sbarbase-Event-Id: <event id>
X-Sbarbase-Delivery-Id: <delivery id>
X-Sbarbase-Timestamp: <unix seconds>
X-Sbarbase-Signature: sha256=<hex hmac_sha256(secret, timestamp + "." + raw body)>
```

The secret is read from `.secrets/upstream/notifier.json` at process start and never
appears in the message, in the catalog, or in any log. The receiver must reject a timestamp
older than 300 seconds and must deduplicate on `Event-Id` plus `Delivery-Id`. That contract
goes in the message documentation, because the idempotency guarantee of section 2.4 depends
on the receiver honouring it.

Because the URL itself is a credential in many webhook products, the URL is redacted from
every log and every evidence file, the same discipline as `redacted_arguments`
(`lab/deployment_rehearsal.py:149-162`), whose whole purpose is that "the path to a private
bootstrap file must not travel with it". A URL containing a secret path is put through the
same treatment.

### 3.3 Timeouts and retry policy

- Per attempt budget: 10 seconds wall clock, connect and read, enforced with
  `AbortSignal.timeout(10000)`, the same mechanism already used in this tree
  (`lab/bootstrap-auth.ts:13`, `src/gateway/managed.ts:28-29` uses a 10 second budget,
  `lab/management-check.ts:17` uses 5 seconds).
- Email: one connection, one message, then close. A connection refusal or timeout is
  transient. An SMTP 4xx is transient. An SMTP 5xx is permanent and settles `failed`
  immediately.
- Webhook: a connection failure, a timeout, or any 5xx response is transient. A 4xx is
  permanent. A 2xx is success. Any other status is transient.
- Backoff: 15 s, 60 s, 300 s, 1800 s, 7200 s, then `failed` at 8 total attempts.
- Retry never happens inline. A failed attempt writes `next_attempt_at` and returns.
  Nothing sleeps in the delivery path, so a slow or dead channel costs one bounded 10
  second wait per attempt and nothing else.

### 3.4 No channel may block or fail the operation it reports

Five enforcement points, each testable:

1. Enqueue does no network I/O. `notify()` runs inside the caller's existing transaction
   and only issues local SQLite statements, exactly like `record()`
   (`src/control/catalog.ts:86-89`). The probe in section 7 asserts that the outbox row and
   the `provision_jobs` row commit together, which is only possible if the enqueue path is
   local.
2. Delivery happens strictly after commit, in a separate drain step, outside any catalog
   transaction.
3. Every channel call is bounded by a hard timeout, so the drain step has a bounded worst
   case of `limit` times 10 seconds and can never wait indefinitely.
4. The drain step's own exceptions are caught inside the drain and converted to a delivery
   row. The worker's provisioning control flow (`lab/worker.ts:22-36`) is unchanged, and the
   drain never touches `process.exitCode`, which stays provisioning-only
   (`lab/worker.ts:38`).
5. Per-iteration cap of 20 deliveries, so `claimProvision` (`lab/worker.ts:23`) is never
   starved by a backlog and `busy_timeout=5000` (`src/control/catalog.ts:21`) is never
   approached by a long-held write transaction.

The property that follows: `provision_jobs.state`, `provision_jobs.failure`, and every
`audit_events` row are byte-identical whether every channel is healthy, slow, or absent.
The probe proves this by running the same provisioning path with the webhook pointed at a
closed port and asserting the catalog row is unchanged.

## 4. Deduplication and rate limiting

The problem: a condition that repeats every minute must not become a thousand messages.

Three layers.

### 4.1 Condition deduplication at enqueue

`dedupe_key` is deterministic and derived from the condition, never from the message:
`provision.capacity_refused|installation`,
`admission.pressure|sbarbase-durable-db|memory_full10`,
`restore.failed|<environment id>`, `fence.applied|<environment id>`.

In `notify()`, inside the transaction:

```
open = SELECT id,occurrences FROM notification_outbox
       WHERE dedupe_key=? AND window_until > ?
       ORDER BY at DESC LIMIT 1
if open:
  UPDATE notification_outbox SET occurrences=occurrences+1, last_at=? WHERE id=open.id
  return   -- no delivery row is created, so no message is sent
else:
  INSERT the event with window_until = now + W(kind,severity)
  INSERT one notification_delivery row per enabled channel, state='pending'
```

`W` is 300 seconds for `critical`, 1800 seconds for `warning`, 3600 seconds for `info`.

The thousand-repeats case therefore produces: one immediate message, then zero messages
while the window is open, and one digest when it closes (section 4.2). The window is
durable in the catalog, so restarting the worker does not reset it, which is the failure
mode a purely in-memory rate limiter has.

The first message says so, in the `summary`: "This condition is suppressed for 1800 seconds;
repeats inside that window will be summarised in one message." The operator is never left
wondering whether the silence means recovery.

### 4.2 Digest and recovery messages

The drainer has a second pass, over events, not deliveries:

```
for each outbox row o where o.window_until <= now and o.digest_sent = 0:
  if o.occurrences > 1:
    set o.digest_sent = 1, o.detail = detail_with(count=o.occurrences,
        first_at=o.at, last_at=o.last_at, window_seconds=now - o.at)
    set every 'delivered' delivery row of o back to 'pending' with next_attempt_at = now
  else:
    set o.digest_sent = 1
```

What the operator sees instead of a thousand messages:

- One message at the first occurrence, with `occurrences: 1`.
- One digest when the window closes, with the real count, the first time and the last time:
  `summary: "Environment provisioning failed 412 times in the last 1800 seconds."`
- One recovery message when the condition stops, of kind `<kind>.recovered`, sent once,
  carrying the total count and the window span. Recovery detection is per condition: a
  successful provisioning of that environment clears `provision.capacity_refused|<env>`;
  pressure below every threshold for one full window clears `admission.pressure|<container>|<metric>`.
  Clearing closes the window immediately and sends the recovery message even if the digest
  was never sent.

### 4.3 Per-channel token bucket

Distinct kinds can still burst. Each channel holds a token bucket in the catalog, in a
third small table `notification_budget(channel TEXT PRIMARY KEY, tokens REAL NOT NULL,
updated_at INTEGER NOT NULL)`, refilled at 20 tokens per hour for email and 120 per hour for
webhook, with a bucket depth of 20 and 120. A delivery that finds no token is not dropped:
it is deferred with `next_attempt_at` advanced, and if the deferral exceeds 900 seconds the
drainer emits exactly one `notifier.overflow` event per window summarising how many events
are waiting. Nothing is ever silently discarded; the `occurrences` counter is the durable
proof.

## 5. Where the notifier runs

Three placements were considered.

**A. A separate process.** A `lab/notifier.py` under its own unit, or spawned beside the
supervisor. It would open its own `Catalog` handle and run `claimNotifications`, which makes
it a second active writer against the catalog that already serialises on
`PRAGMA journal_mode=DELETE` (`src/control/catalog.ts:21`) and whose only provisioning
claimer is protected by the exclusive installation worker lock
(`lab/worker.py:27-30`, `lab/worker.ts:7`). A failure mode the project has already paid for
once, so this is rejected.

**B. A stage of the supervisor.** `lab/dev.py` stages are startup-only and must exit
(`run_stage` at `lab/dev.py:116-128` with a `timeout` argument and a cancellation path). A
long-running notifier stage would either exit immediately or hold a second `Catalog` handle
for the life of the installation, which is the same second-writer problem as A. The
supervisor owns exactly two long-lived children, the API and the worker
(`lab/dev.py:101-102`); adding a third long-lived process with catalog write access changes
the ownership model. Rejected.

**C. Inside the worker.** Recommended.

The worker already is the one process that holds the exclusive installation worker lock
(`lab/worker.py:28`, refusal message at `:30`), already owns the single provisioning
`Catalog` handle (`lab/worker.ts:11`), and already runs a loop with an idle sleep
(`lab/worker.ts:22-24`, `await Bun.sleep(500)` when watching). The drain is one bounded step
at the top of that loop:

```ts
while(!settleOnly && !stopping) {
  await drainNotifications(catalog, channels, {limit: 20});   // new, bounded
  const job = catalog.claimProvision();
  if(!job) { if(!watch) break; await Bun.sleep(500); continue; }
  ...
}
```

The drain runs before the claim, so notifications are always attempted even when no job is
queued, and the 500 ms idle sleep becomes the natural delivery cadence.

Why this does not create a second writer: the enqueue path is part of transactions that
already exist (section 2.2), and the only process that claims and settles delivery is the
worker, which already writes the catalog. The console Bun server already writes the catalog
for management mutations; nothing new is added there either. The count of processes that
write `control.sqlite` is unchanged.

Tradeoffs accepted with C: the notifier shares the worker's fate, so a stopped worker
delivers nothing. That is acceptable because the cases where the worker is down are
themselves events (kinds `worker.restart`, `worker.restart_limit`) and are enqueued by the
supervisor path, which runs a one-shot `lab/notify-enqueue.ts` without competing for the
worker lock. Those two messages will age in the outbox until the worker comes back, which is
the correct behaviour, and the console route of section 2.6 shows them.

One more property worth stating: `--settle-only` mode (`lab/worker.ts:9`, `:21`, invoked at
`lab/dev.py:154`) must not drain, because it runs before the runtime starts and its whole
purpose is to settle one receipt and exit. The drain is skipped when `settleOnly` is true.

## 6. What a message may and may not contain

### 6.1 Allowed

All identifiers already present in the catalog: `organization`, `project`, `environment`
(uuid), `runtime` (matching `e_[a-f0-9]{24}`, `lab/source_fence.py:8-9`), `actor` (validated
by `src/control/catalog.ts:69-73`), timestamps, `kind`, `severity`, a `reason` from the
closed enum, `occurrences`, and the fixed `summary` and `action` strings for that kind and
reason. A reason class name from the admission vocabulary
(`lab/resource_admission.py:31-37`, `lab/pressure_admission.py:6`,
`lab/combined_admission.py:16-22`). A container name from the fixed owned set
(`sbarbase-durable-db`, `sbarbase-durable-storage`), which is not a secret. A diagnostic
file name under `.lab/upstream/diagnostics/`, and only the name, not the content, following
`lab/installation_runtime.py:111-118` which already keeps the cause private and prints only
the path.

### 6.2 Forbidden, without exception

- Any credential, database connection string, password, JWT, signing secret, API key or key
  digest. Including a publishable key, because the console's rule is already that listings
  "return key metadata only" (`docs/CONTROL-PLANE.md:36`).
- Any customer data: rows, table contents, object keys, bucket names, user emails, Auth user
  ids, RLS policy text.
- Any raw SQL, any upstream response body, any stack trace, any exception message.
- Any private filesystem path to a credential file. The lesson is recorded in
  docs/RESUME-CHECKPOINT.md:717-718: "the recorded-command redaction missed the
  `--bootstrap-file=PATH` form, which leaked the private path into committed evidence."
  Message templates never carry a path to `.secrets/` or to a bootstrap file; the
  diagnostic path exception in section 6.1 is limited to `.lab/upstream/diagnostics/`, which
  holds no credential and is already 0600 (`lab/installation_runtime.py:114-118`).
- HTTP request bodies, query strings, cookies or credentials in any log line, which is the
  rule the TLS proxy already keeps (docs/SERVER-DEPLOYMENT.md:287-288: "logs only method, path
  and status: never bodies, query strings, cookies or credentials").

### 6.3 Enforced by construction, not by care

Four mechanisms, in order, none of which relies on a human or a model remembering:

1. **Closed reason enum.** `reason` is typed as a union of string literals in
   `src/control/notification.ts`, and the insert path rejects any value not in the union.
   A raw error string cannot become a reason, so it cannot become a message field.
2. **Per-kind closed detail schema.** Each `kind` has a declared key list, for example
   `provision.failed` is exactly `{reason, attempt}`. `notify()` rejects a detail object
   with any key outside the list, any non-primitive value, and any string longer than 200
   characters. There is no field that accepts free text from a caller.
3. **Fixed renderer.** `summary` and `action` are looked up from a constant table keyed by
   `kind` and `reason`. They are not interpolated from input except by inserting the safe
   identifiers of section 6.1 in fixed positions. This is the same technique the project
   already uses to keep failure text safe: a fixed sentence instead of the cause
   (`lab/durable_runtime.py:396-398`), a fixed public message with the cause in a 0600
   diagnostic (`lab/installation_runtime.py:112`), and a refusal to echo Docker and SQL
   errors because they "may contain generated credentials" (`lab/run.py:26-28`).
4. **Final denylist gate, fail closed.** Before a channel call, the fully rendered envelope
   (subject, body, every header value, the webhook URL) is scanned for credential shapes:
   `postgres://` and `postgresql://`, `sb_publishable_`, `sb_secret_`, a three-part
   base64url JWT (`^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$`), a standalone 64
   character hex string, 32 or more consecutive base64url characters, `password=`,
   `apikey`, `authorization:`, and `BEGIN PRIVATE KEY`. A match means no bytes leave the
   process: the delivery row settles `failed` with `last_error='redaction_refused'`, and a
   `notifier.redaction_refused` event of severity critical is enqueued containing only the
   refused event id and kind. The probe in section 7 exercises this path and asserts that
   both channels received nothing.

The design consequence worth naming: because the gate is fail-closed, an over-zealous match
costs a missed message, not a leaked credential. That is the correct direction of error for
an installation where the operator is trusted but the transport is not.

## 7. Local verification path

A probe that produces a real event and reads the delivered message back, using only local
resources.

### 7.1 Resources

- **Local HTTP receiver** for the webhook: a loopback listener on a free port chosen at run
  time. The host rule against fixed ports and profiles applies
  (/home/sbarah/AGENTS.md, multi-agent browser and dev-server conventions: verify the port
  is free before binding, choose another rather than killing the owner). The receiver writes
  each request's method, path, headers and raw body to a per-run temporary directory and
  answers 200.
- **Mailpit** for email, from the image already present on this host. Verified present:

```
docker images public.ecr.aws/supabase/mailpit:v1.30.2
public.ecr.aws/supabase/mailpit   v1.30.2   37a38e48e933   50.6MB
```

  Started with the project's own container conventions: a private internal network, an
  ownership label, bounded memory and CPU, no published port, a log cap
  (the shape used at lab/run.py:49-62). Mailpit listens on SMTP 1025 and serves its HTTP API
  on 8025. The probe reads the container IP with `docker inspect`, which is the idiom
  `lab/run.py:84-89` already uses, and then talks to `http://<ip>:8025` for reading back.

```
docker network create --internal --label io.sbarbase.owner=notification-check sbarbase-notify-net
docker run -d --name sbarbase-notify-mailpit \
  --label io.sbarbase.owner=notification-check --network sbarbase-notify-net \
  --memory 128m --memory-swap 128m --cpus .25 --pids-limit 64 \
  --log-opt max-size=5m --log-opt max-file=2 \
  public.ecr.aws/supabase/mailpit:v1.30.2
```

### 7.2 The probe, `lab/notification-check.py`

Steps, in order:

1. Verify the pinned Mailpit image digest is local; refuse to pull.
2. Create the private network and start Mailpit by exact identity. Record its IP.
3. Pick a free loopback port, start the HTTP receiver in a thread, write its URL.
4. Write a per-run notification configuration pointing `email.host` at the Mailpit IP with
   `port: 1025`, `tls: "none"`, and `webhook.url` at the receiver, with a fresh random
   `webhookSecret`. Both files mode 0600, both under the per-run temporary directory.
5. Produce a real event through the real catalog path, not by inserting a row: create an
   isolated catalog in the temporary directory, create an organization, a project and an
   environment, claim the job, and call `finishProvision(environment, claim, false,
   'capacity_exceeded')`. Assert that the `notification_outbox` row and the
   `provision_jobs` failure commit together, by reading both from the same file. This is the
   same fixture shape the existing catalog tests use
   (`tests/worker-receipt.test.ts:6-8` creates a `Catalog` on a temporary path and drives
   the real state machine).
6. Run the drain once: `bun lab/notify-drain.ts --catalog <path> --config <path> --once`.
7. Read back the webhook receiver's recorded request. Assert: 200 answered, the exact
   header set of section 3.2, `id` equal to the outbox id, `kind` `provision.capacity_refused`,
   `reason` from the closed enum, `occurrences` 1, and the HMAC recomputed over
   `timestamp + "." + raw body` equals `X-Sbarbase-Signature`.
8. Read back the mail. `GET http://<mailpit ip>:8025/api/v1/messages`, take the newest
   message, assert the subject contains the event id and the severity and kind, and assert
   the body contains the same `summary` string and none of the denylist shapes of section
   6.3.
9. Assert the delivery rows for both channels are `delivered` with `attempts` 1.
10. Remove only the exact containers and network it created, after verifying the ownership
    label, the way `lab/recovery-cleanup-check.py` and `lab/fresh-worker-check.py` already
    clean up. Write evidence to `docs/evidence/notification-checks.json` in the shape the
    other checks use (`lab/deployment_rehearsal.py:283-300`).

### 7.3 The negative control, and why it is the important part

Each of the following must record a failure. A silence is a failed probe, not a pass.

| Run | Deliberate fault | Required observation |
|---|---|---|
| `--broken-webhook` | `webhook.url` on a closed loopback port | a `notification_delivery` row with `state='pending'`, `attempts>=1`, `last_error='webhook_unreachable'`, `next_attempt_at > now`; the email row still `delivered`; the receiver saw nothing |
| `--broken-email` | `email.host` on a port with no listener | the email row has `attempts>=1` and a non-empty `last_error`; the webhook row still `delivered`; Mailpit received nothing |
| `--timeout-webhook` | a receiver that accepts and never responds | `last_error='webhook_timeout'`, and the probe's own wall clock for the drain step is under 15 seconds, proving the timeout is the `AbortSignal.timeout(10000)` budget and not a hang |
| `--redaction-refused` | an event whose detail carries a `postgres://` connection string, injected through the internal path | both rows `state='failed'`, `last_error='redaction_refused'`; the receiver saw no bytes and Mailpit received no message; one `notifier.redaction_refused` event exists |
| `--catalog-unchanged` | the broken-webhook run, checked afterwards | `provision_jobs.state`, `provision_jobs.failure` and every `audit_events` row are byte-identical to the healthy run, proving the channel failure did not touch the operation it reported |

The core assertion the probe must make explicit, in its own check line, is: "a broken
channel produced a recorded failure, not a silence." Concretely, the probe fails when the
outbox row exists, the fault was injected, and there is no `notification_delivery` row at
all, or all rows are still `attempts=0`. That is the failure mode a naive fire-and-forget
notifier has, and this probe exists to catch it.

Commands:

```
/usr/bin/python3 lab/notification-check.py --local
/usr/bin/python3 lab/notification-check.py --local --broken-webhook
/usr/bin/python3 lab/notification-check.py --local --broken-email
/usr/bin/python3 lab/notification-check.py --local --timeout-webhook
/usr/bin/python3 lab/notification-check.py --local --redaction-refused
```

## 8. Implementation task list

Each step names the exact file and the command that verifies it. Steps 1 to 6 are the
minimum viable system; steps 7 to 13 add the surfaces and the proof. Verify after each
step, not at the end.

### Step 1: schema and enqueue path

File: `src/control/catalog.ts`. Add the two tables and the two indexes of section 2.1 to the
`db.exec` string at `:21-57`. Add the private `notify()` beside `record()` at `:86-89`. Add
`notify()` calls inside `finishProvision` (`:224-231`), `retryProvision` (`:275-284`),
`recoverPreflightReceipt` (`:254-273`), `changeRuntimeRouting` (`:309-328`), `setMember`
(`:125-141`) and `transferProject` (`:290-299`).

Verify:

```
bun test tests/catalog.test.ts
```

### Step 2: closed event vocabulary and renderer

File (new): `src/control/notification.ts`. Declare `NotificationKind`, `NotificationSeverity`,
`NotificationReason` as closed unions; `DETAIL_KEYS` per kind; the fixed `summary` and
`action` table; the renderer; the denylist gate of section 6.3; the `detail` validator of
section 6.3 mechanism 2.

Verify:

```
bun test tests/notification-schema.test.ts
```

### Step 3: claim, settle, suppress, prune

File: `src/control/catalog.ts`. Add `claimNotifications`, `settleNotification`,
`sealWindow` and `pruneNotifications` as described in sections 2.3 to 2.5, in the
`.immediate()` transaction style of `claimProvision` (`:202-223`) and `finishProvision`
(`:224-231`).

Verify:

```
bun test tests/notification.test.ts
```

### Step 4: channels

File (new): `src/control/notification-channels.ts`. Implement the SMTP submission of section
3.1 and the signed webhook of section 3.2, both bounded by `AbortSignal.timeout(10000)`,
both returning a classification (`delivered`, `transient`, `permanent`, `redaction_refused`)
rather than throwing past the caller. Read the secret from the configured path only. Redact
the webhook URL from every string that leaves the module.

Verify:

```
bun test tests/notification-channels.test.ts
```

### Step 5: configuration loader

File (new): `lab/notification-config.ts`. Read and validate `.lab/upstream/notifications.json`
and `.secrets/upstream/notifier.json`, resolve the email recipient with the bootstrap
fallback of section 3.1, refuse a relative path, a symlink, a world readable file, and an
unknown `schema`, in the spirit of `lab/operator_file.py:66-90`.

Verify:

```
bun test tests/notification-config.test.ts
```

### Step 6: drain entry and worker wiring

Files: new `lab/notify-drain.ts` (a `--once` mode and a `--limit` option, importing the
catalog, the config loader, the channels and the renderer), and `lab/worker.ts` where the
drain step is inserted at the top of the loop (`:22`) and skipped when `settleOnly`
(`lab/worker.ts:9`).

Verify:

```
bun test tests/notification.test.ts
bun lab/notify-drain.ts --help
```

### Step 7: supervisor lifecycle events

Files: new `lab/notify-enqueue.ts` (opens the catalog, one `notifyWrite`, closes; never
claims), and `lab/dev.py` at `:96` and `:93-94`, calling it for `worker.restart` and
`worker.restart_limit`.

Verify:

```
/usr/bin/python3 -m unittest discover -s lab -p 'test_supervisor*.py'
```

### Step 8: admission and fence events

Files: `lab/durable_runtime.py` (persist the fine reason code next to the exit code at
`:386-395`, in the receipt it already writes through `effect_receipt.native_outcome`), and
`lab/installation_runtime.py` at `:111-118` (enqueue `admission.runtime_refused` with the
sanitized reason), and `lab/source_fence.py` at `:19` and `:37` (enqueue `fence.applied` and
`fence.released`).

Verify:

```
/usr/bin/python3 -m unittest discover -s lab -p 'test_pressure_admission.py'
/usr/bin/python3 -m unittest discover -s lab -p 'test_source_fence.py'
/usr/bin/python3 -m unittest discover -s lab -p 'test_resource_admission.py'
```

### Step 9: backup and restore events

Files: `lab/recovery-export.py` at `:153`, `lab/recovery-restore-db.py` at `:34-56` and
`:194`, following the ordering rule of section 2.2.

Verify:

```
/usr/bin/python3 -m unittest discover -s lab -p 'test_recovery*.py'
```

### Step 10: host pressure sampler

File (new): `lab/pressure-sampler.py`, invoked by the drain cadence from `lab/notify-drain.ts`
by shelling out to `/usr/bin/python3 lab/pressure-sampler.py --json`, reusing
`pressure_admission.snapshot` (`lab/pressure_admission.py:21-33`) and `combined_admission`'s
host readings (`lab/combined_admission.py:54`). It prints measurements only; it never
decides.

Verify:

```
/usr/bin/python3 lab/pressure-sampler.py --json
```

### Step 11: console surface

Files: `src/control/http.ts` (the read-only route of section 2.6), `ui/api.ts` (the type),
`ui/Environments.tsx` (near the existing notices at `:14-16`, a line saying how many
notifications are undelivered when the count is non-zero).

Verify:

```
bun test tests/management.test.ts tests/ui-static.test.ts
bun run build:ui
bun run typecheck:ui
```

### Step 12: the local probe and negative controls

File (new): `lab/notification-check.py`, as specified in section 7.

Verify:

```
/usr/bin/python3 lab/notification-check.py --local
/usr/bin/python3 lab/notification-check.py --local --broken-webhook
/usr/bin/python3 lab/notification-check.py --local --broken-email
/usr/bin/python3 lab/notification-check.py --local --timeout-webhook
/usr/bin/python3 lab/notification-check.py --local --redaction-refused
/usr/bin/python3 lab/notification-check.py --local --catalog-unchanged
```

### Step 13: documentation and the full suite

Files: new `docs/NOTIFICATIONS.md` (configuration, the webhook receiver contract of section
3.2 including the 300 second replay window and the deduplication requirement, the failure
semantics of section 3.4, the retention of section 2.5), plus one paragraph in
`docs/SERVER-DEPLOYMENT.md` under "Verify after install" and one in `lab/README.md`.

Verify:

```
bun test
/usr/bin/python3 -m unittest discover -s lab -p 'test_*.py'
/usr/bin/python3 lab/pinned_images_check.py
```

The final gate is the one that matters: the `--broken-*` and `--redaction-refused` runs
must exit non-zero when the failure is not recorded. A probe that passes while a broken
channel is silent has proved nothing.