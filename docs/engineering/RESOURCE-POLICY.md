# 01 Resources: distribution and isolation policy

Status: design record, 2026-09-21. Nothing here is implemented yet. Written by a
delegated design pass and reviewed by the parent the same day: the load bearing claims were
re-read against the code and the counts were reproduced (audit_events 224 and 8 in the two
catalogs, the environment Auth at 256m and 0.25 CPU, the database at 1024m and 1 CPU on one
volume, no blkio or cpu-shares anywhere in the tree, the pin count assertion at
lab/test_pinned_images.py:38). Unverified items are labelled unconfirmed in place.


Status: design only. Nothing in this document was executed against a runtime, no
container was started, and no file in `/home/sbarah/R/Projects/P/sbarbase` was
modified. `.secrets/` and `.lab/` contents were not read.

Read on 2026-09-21 against git `main` of `/home/sbarah/R/Projects/P/sbarbase`
(read from the worktree `/home/sbarah/R/Projects/P/sbarbase/.worktrees/subagent-sa-0-93de7637`,
branch `hermes-subagent/subagent-sa-0-93de7637`). `/home/sbarah/AGENTS.md` was read first.

## 0. Labels used

- VERIFIED: read directly from a file, or read from live Docker state on this host in this session, with the source named.
- INFERRED: a conclusion drawn from the verified facts, not itself observed.
- PROPOSED: a policy this document puts forward. None of it is implemented.
- not found: the repository has no answer. What was searched is listed next to it.

## 0.1 The question, and the honest answer it can carry

The user's concern: on one server, if one project is under heavy load, will it
automatically take the resources it needs while the other projects stay
unaffected. Today the answer is no, and the reason is structural: the shared
PostgreSQL engine and the shared Storage process are single processes with
per-environment logins, and neither Docker nor cgroup v2 can partition CPU, page
cache or disk inside one process among its clients.

This document designs the strongest policy that is honest on top of that
structure:

1. Every container that belongs to the installation gets an explicit CPU weight,
   block-IO weight, memory ceiling and process ceiling, so the parts that ARE
   separable (per-environment Auth, per-environment PostgREST, Storage, the
   database engine) can no longer starve each other in a fixed ratio. That is a
   fair-share guarantee between front ends, not between tenants inside the engine.
2. Capacity is reserved before allocation and re-checked before every start, with
   the plan derived from one table instead of five hardcoded constants.
3. Pressure is watched continuously, not only at admission, and the response is
   the pause lease that already exists in the gateway.
4. What cannot be guaranteed is stated in section 6, together with the escape
   hatch that is already designed: ownership is independent of placement, so one
   environment can be moved to its own cluster or to another server.

## 1. What exists today

Every row is a real control. The last column is what it does NOT do, which is the
whole subject of this document.

| Control | Exact value | Where | What it does NOT do |
|---|---|---|---|
| Container memory ceiling | `--memory <memory>` | `lab/durable_runtime.py:134`, `lab/run.py:57` | Nothing below the container. Per-environment Auth and REST cannot grow past their own ceiling, but the shared database's memory is one pool for all tenant databases in it. |
| Swap disabled | `--memory-swap` set equal to memory | `lab/durable_runtime.py:134`, `lab/run.py:57` | Not verified live: `HostConfig.MemorySwap` for `sbarbase-durable-e_3ac7584e45004caac76be27c-rest` is 268435456, equal to its memory (VERIFIED, `docker inspect` this session). Prevents swap, does not allocate. |
| CPU ceiling | `--cpus <cpus>` | `lab/durable_runtime.py:134` | `--cpus` is a CFS quota (`cpu.max`), a hard upper bound, not a share. A container that wants 0.25 CPU of a contended 24-CPU host gets whatever the runqueue gives it; nothing protects a neighbour from being the one queued. |
| Process ceiling | `--pids-limit 128` | `lab/durable_runtime.py:134`, `lab/run.py:58` | Bounds threads only. Measured idle process counts: REST 62, Auth 8, database 12 (`docs/evidence/idle-snapshot.json`, VERIFIED as data). 128 has headroom today; a fork bomb inside 128 tasks still consumes CPU. |
| CPU **weight** | `CpuShares: 0` on every owned container | VERIFIED by `docker inspect --format '{{json .HostConfig}}' sbarbase-durable-db` and `...-e_3ac7584e45004caac76be27c-rest` this session | Zero means no explicit weight was requested, so every container competes as an equal sibling. There is no production/experimental priority anywhere in the code. Grep for `cpu-shares`, `CpuShares`, `cpu.weight` in `lab/`, `src/`, `tests/`, `docs/`: only these two `0` readings. |
| Block-IO **weight** | `BlkioWeight: 0` on every owned container | same two `docker inspect` readings | No relative disk fairness between containers at all. |
| Block-IO bandwidth | `DeviceReadBps: null`, `DeviceWriteBps: null` | same two readings | No per-container read or write byte ceiling. One container doing large sequential I/O competes with PostgreSQL WAL writes on the same device. |
| Memory soft limit | `MemoryReservation: 0` | same two readings | No reservation, no priority under memory pressure. Grep for `memory-reservation`, `oom-kill-disable`, `oom_score`: not found in `lab/`, `src/`, `docs/`, `tests/`. |
| Per-environment ceilings | database `1024m` / `1` CPU, shared Storage `512m` / `0.5`, management Auth `256m` / `0.25`, each environment Auth `256m` / `0.25`, each environment REST `256m` / `0.25` | `lab/durable_runtime.py:188`, `:213`, `:356`, `:311` | Fixed for every environment. No tier, no per-environment override, no quota that spans one environment's containers. Two environments with different value get identical limits. |
| Environment count guard | at most 4 environments | `lab/durable_runtime.py:278` | A count, not a resource measurement. The lab runner allows 5 (`lab/run.py:170`), so the two guards disagree. |
| Installation memory ceiling | `MAX_MEMORY = 6*1024**3`, `MAX_CPUS = 6`, `RESERVE = 2*1024**3+512*MIB` | `lab/combined_admission.py:11-13` | Applies to combined source plus target start. The planned placement is 5888 MiB and 5.75 CPUs, so 256 MiB and 0.25 CPU of the ceiling remain. Nothing about a new component (Studio) can be added without exceeding it. |
| Combined name list | explicit literal list: source db, Storage, management Auth, `auth`+`rest` per environment in `source.values['environments']`, plus target `db`,`auth`,`rest`,`storage` | `lab/combined_admission.py:29-31` | Under-counts anything it is not told about (Studio and postgres-meta are not in it, as `docs/engineering/STUDIO-INTEGRATION.md:121-122` records), and over-counts containers that exist but are stopped (`docs/engineering/COMBINED-RUNTIME.md:9`). It also requires every listed container to exist, because it inspects each one (`:34`). |
| Combined headroom test | `memory > MAX_MEMORY or cpus > MAX_CPUS` then `available < memory+RESERVE` then `host_cpus < cpus+2` | `lab/combined_admission.py:16-22` | Refuses allocation, does not shape it. `host_cpus` is `os.cpu_count()` (`:52`), which reports 24 on this host (`/proc/cpuinfo` has 24 `processor` entries; `docker info` reports `NCPU 24`). |
| Disk-space admission | memory reserve 2 GiB plus 512 MiB for a new environment; 5 GiB disk reserve plus 1 GiB new-environment allowance on each of the database and object volumes; 10000 free inodes | `lab/resource_admission.py:10-14`, `:32-37` | A whole-filesystem free-space check, not a per-tenant allocation. No environment has a disk quota, and no environment's own size is measured. Btrfs reports no fixed inode pool, handled at `:53-56`. |
| Volume measurement | `df -Pk` and `df -Pi` executed inside the owned database and Storage containers at their real volume paths | `lab/resource_admission.py:48-58`, `:69-73` | Reads the filesystem the volume sits on. Both volumes are on `/dev/mapper/luks-d5d273a7-4705-47d4-9d9a-59d5fbc8f10c` (btrfs, VERIFIED via `findmnt` and `df -PT /var/lib/docker` this session), so the two numbers are one filesystem counted twice, which `docs/engineering/RESOURCE-ADMISSION.md:12` admits. |
| Container pressure admission | `cpu_some10 >= 50.0`, `io_full10 >= 20.0`, `memory_full10 >= 1.0` refused; containers `('sbarbase-durable-db','sbarbase-durable-storage')` only | `lab/pressure_admission.py:6-7`, `:36-48` | Admission-time only, one snapshot, before provisioning. Studio, meta, and every per-environment Auth/REST container are outside the tuple, so their stalls are invisible. Not a monitor and not a response. Last recorded snapshot was all zeros with `refusal: null` (`docs/evidence/pressure-snapshot.json`). |
| PostgreSQL connection budget | `SERVICE_LIMIT = 6`, `ENVIRONMENT_LIMIT = 3*6 = 18`, `SHARED_LIMIT = 12`, `OPERATIONS_RESERVE = 10`; fits when `18*envs + 12 + 10 <= max - superuser_reserved - reserved` | `lab/connection_budget.py:2-5`, `:8-12` | Connection counts only. `docs/engineering/CONNECTION-BUDGET.md:22` states this explicitly: not query CPU, memory, lock duration or disk I/O. |
| Service pools | Auth `GOTRUE_DB_MAX_POOL_SIZE = 3` (`lab/run.py:130`), PostgREST `PGRST_DB_POOL = 3` (`lab/run.py:137`), Storage metadata and tenant connections 3 each (`lab/durable_runtime.py:210`, `:321`) | as listed | Three connections per service is the real concurrency inside the engine. The gateway cap below is derived from it, but the gateway is not the only client. |
| Gateway environment admission | 8 requests per environment, 32 per process, no queue | `src/gateway/concurrency.ts:34`, `:46` | In-process only. `docs/engineering/GATEWAY-OVERLOAD.md:5` states it is not a distributed rate limiter and cannot terminate work that ignores cancellation. Two gateway processes double the real capacity. |
| Gateway service admission | per-environment per-service counter; REST publishes 3 from `PGRST_DB_POOL` | `src/gateway/handler.ts:116`, `src/gateway/concurrency.ts:43-50`, published at `lab/durable_runtime.py:333` | A concurrency cap, not a rate cap. 3 concurrent 8-second RPCs can still be 3 requests per 8 seconds or 1000 small ones. |
| Gateway body and time bounds | body limit 1 MiB, `content-length` pre-check, 10 s body read deadline, 15 s upstream fetch deadline, 30 s pre-header deadline, 30 s response deadline | `src/gateway/handler.ts:83-84`, `:92-94`, `:112`; `src/gateway/concurrency.ts:34`, `:74`, `:126` | Per request, not per tenant. Nothing bounds a tenant's request rate or total bytes over time. |
| Gateway pause lease | in-process exclusive lease, 503 with `retry-after: 1`, `waitForDrain(timeoutMs)` | `src/gateway/concurrency.ts:7-30`, `:39`; entry point `src/gateway/managed.ts:9` | `docs/engineering/GATEWAY-DRAIN.md:9`: state is in memory, disappears on restart, other gateway processes are not fenced, and gateway drain is not database quiescence. |
| SQL execution bounds | per-environment REST login: `statement_timeout = 8s`, `transaction_timeout = 12s`; anon 3 s and authenticated 8 s upstream defaults unchanged | `lab/durable_runtime.py:247`, `:254`; `docs/engineering/SQL-DEADLINES.md:7-8` | `docs/engineering/SQL-DEADLINES.md:30`: environments still share PostgreSQL CPU, memory and I/O; a SQL author can override `statement_timeout` (verified live in that document), and a function can change `transaction_timeout` itself. |
| Installer headroom plan | `PLANNED_MIB = 5888`, `RESERVE_MIB = 2560`, `PLANNED_CPUS = 5.75`, `CPU_SPARE = 2`, `MIN_FREE_BYTES = 12 GiB` | `lab/install_server.py:31-38`, arithmetic at `:122-130`, refusal at `:142-145` | The same 5888 and 5.75 exist again in `lab/combined_admission.py` as a sum of names, and again as prose in `docs/guides/server-deployment.md:24`. Three places, one number. |
| Raw headroom gates on start | hardcoded 6 GiB `MemAvailable` before starting the durable runtime, the target, or the lab | `lab/durable_runtime.py:183-184`, `lab/target_runtime.py:72-73`, `lab/run.py:149-151` | A fourth reserve constant, unrelated to the other three. Values are read in kB and compared to `6*1024*1024`, so the unit works, but the number is duplicated. |
| Footprint measurement | `docker stats` sampled into `docs/evidence/source-stage-footprint.json` (9 containers, 279 MiB total, database 94, Storage 117, management Auth 9, each environment Auth 8 to 9 and REST 9 to 10) | `lab/installation_runtime.py:16-38`, `:41-58`; file VERIFIED | Measured usage of the source stage only, sampled immediately before a combined check, and only on the moved-installation path. No tier, no pressure, no per-tenant attribution. |

### 1.1 Where the ceiling actually is

VERIFIED arithmetic from `lab/combined_admission.py:29-31` and the launch values
above: source = database 1024 MiB + Storage 512 + management Auth 256 + 4 x (Auth
256 + REST 256) = 3840 MiB and 3.75 CPUs. Target = database 1024 + Storage 512 +
Auth 256 + REST 256 = 2048 MiB and 2.0 CPUs. Combined 5888 MiB, 5.75 CPUs, which
is exactly `install_server.PLANNED_MIB` and `PLANNED_CPUS`.

Against `MAX_MEMORY` 6144 MiB and `MAX_CPUS` 6 there are 256 MiB and 0.25 CPU
left. That is why `docs/engineering/STUDIO-INTEGRATION.md:123-126` finds that even a 512 MiB
Studio plus a 128 MiB meta at four environments takes the plan to 8448 MiB and
8.75 CPUs and is refused, not started.

## 2. What is missing for fair distribution

1. Relative CPU weight. `CpuShares` is 0 everywhere (VERIFIED). There is no way
   to say "this environment matters more than that one".
2. Block-IO weight. `BlkioWeight` is 0 everywhere (VERIFIED). Two containers
   writing to the same device split it in arrival order.
3. Block-IO bandwidth. `DeviceReadBps` and `DeviceWriteBps` are null everywhere
   (VERIFIED). Nothing bounds bytes per second per container.
4. Per-environment quotas. There is no object that says what one environment is
   allowed in total across its containers. Auth and REST each have a 256 MiB
   ceiling; the sum is never checked, and adding Studio to an environment would
   add two more unquoted containers.
5. Priority classes. Production and experimental environments are identical
   (`lab/durable_runtime.py:311` takes no tier). `docs/decisions/README.md:15` says
   capacity is to be measured before admission, but no class exists to encode
   the result.
6. Continuous pressure response. `lab/pressure_admission.py` is called once from
   the provisioning path (`lab/durable_runtime.py:286`). `docs/engineering/PRESSURE-ADMISSION.md:16`
   states it plainly: an admission-time gate, not a continuous monitor or a
   remedy for an existing overloaded tenant. Its container tuple also omits
   every per-environment container. Section 5.2.1 records what has since been
   built: a repeated sampler, a summary and a level 1 response that refuses
   admissions and records crossings. What is still missing here is the arrival
   driven run and any response above level 1.
7. Per-tenant traffic shaping. Grep for `rate`, `bucket`, `tokens` in
   `src/gateway/`: not found. The gate counts concurrent requests, so a tenant
   with fast requests can consume the shared engine's CPU and connections at an
   unbounded rate while staying under the concurrency cap. This is the same
   shape of finding as `docs/engineering/SUSTAINED-OVERLOAD.md:22`, which observed accepted
   target work waiting long enough to time out and named pool fairness as
   unestablished.
8. Disk-space fairness. No per-environment quota, no per-environment size
   measurement. A tenant writing objects until the volume fills takes the whole
   installation down, and both volumes are the same btrfs filesystem
   (VERIFIED, section 1). `docs/engineering/RESOURCE-ADMISSION.md:12` already says shared
   filesystems are checked individually and not added together, which
   understates the risk: they are one filesystem.
9. Memory pressure priority. `MemoryReservation` is 0 (VERIFIED). Under host
   memory pressure the kernel chooses an OOM victim by its own heuristic; the
   installation cannot say that the shared database is the container to keep.
10. A derived plan. The number 5888 exists three times (section 1). Fixing the
    name list without fixing the duplication leaves the same defect one step
    later.

## 3. PROPOSED policy

Nothing here is implemented. Every value below is a policy constant to be
committed to one place, measured before it is trusted, and quoted with a date.

### 3.1 One table, one module

Create `lab/resource_policy.py` holding the tier table. `lab/durable_runtime.py`,
`lab/run.py`, `lab/combined_admission.py`, `lab/installation_runtime.py` and
`lab/install_server.py` all import from it, so a number exists once.
`install_server.PLANNED_MIB` becomes `sum(tier.memory for tier in plan)` and
stops being a literal.

Four classes, identified by a Docker label `io.sbarbase.tier` so that any query
can enumerate by class instead of by hardcoded name.

| Class label | Containers | `--cpu-shares` | `--blkio-weight` | `--cpus` | `--memory` | `--memory-swap` | `--pids-limit` |
|---|---|---:|---:|---:|---:|---|---:|
| `system` | shared database | 2048 | 800 | 1 | 1024m | equal to memory | 128 |
| `system` | shared Storage | 2048 | 800 | 0.5 | 512m | equal | 128 |
| `system` | management Auth | 1024 | 500 | 0.25 | 256m | equal | 128 |
| `production` | environment Auth, environment REST | 512 | 400 | 0.25 | 256m | equal | 128 |
| `experimental` | environment Auth, environment REST | 128 | 100 | 0.25 | 256m | equal | 128 |
| `operator` | Studio, postgres-meta, on demand | 1024 | 500 | 0.5 / 0.25 | 512m / 128m | equal | 128 |
| `maintenance` | recovery target db/auth/rest/storage, export helpers | 512 | 400 | unchanged from today | unchanged | equal | 128 / 64 |

Notes that matter:

- `--cpus` stays a hard quota in every class. It is the anti-runaway bound and
  the reason a single container cannot take the whole host. Adding a weight does
  not remove it.
- The weight is the new part. Docker maps `--cpu-shares` to the cgroup v2
  `cpu.weight` and `--blkio-weight` to `io.weight`; that mapping is INFERRED from
  Docker's documented cgroup v2 behaviour and MUST be verified on the created
  container before the numbers are trusted (step 3 of section 7 reads
  `cpu.weight` and `io.weight` inside the container and compares them to the
  table).
- Weights are relative among runnable siblings in the same parent cgroup. With
  one `production` environment and one `experimental` environment both
  saturated, the production environment's services receive approximately
  `512/(512+128)`, about four fifths, of the contended CPU that the two could
  together obtain, and the experimental environment about one fifth. This is
  INFERRED from CFS weight semantics and is exactly what the experiment in
  section 5 is designed to falsify.
- The shared database gets the highest weight, 2048, because it is the only
  container that serves every tenant and it should not be the thing that queues
  behind a tenant's own front ends. This does NOT partition CPU between tenant
  databases inside the engine. Section 6 states that limit.
- Equal `--memory-swap` keeps swap disabled, which is the current behaviour and
  should stay. It costs the ability to absorb a spike in swap and buys a
  predictable OOM instead of an unpredictable thrash.
- `MemoryReservation` is deliberately NOT set in this proposal. Docker maps it
  to `memory.low`, which protects a cgroup from reclaim, and setting it on the
  shared database would make every environment's page cache reclaim before the
  database's, which is the opposite of what the shared engine needs. Choosing a
  reservation needs a reclaim experiment, not a guess. This is a real decision
  deferred, not a gap overlooked.

### 3.1.1 The mapping reading, taken 2026-09-21: the weight column does not bind

The INFERRED note above, and the reading step 3 asks for, are both closed.
Measured on this host (kernel 7.1.12-200.fc44, Docker 29.7.2, cgroup v2,
disposable containers, raw readings in
`docs/evidence/resource-policy-cgroup-mapping.json`):

| `--cpu-shares` asked | kernel `cpu.weight` observed |
|---|---|
| 2 | 1 |
| 128 | 21 |
| 256 | 35 |
| 512 | 59 |
| 1024 | 100 |
| 2048 | 174 |
| 4096 | 303 |

Three consequences. The first two correct this design.

1. **The mapping is sublinear.** This section asks for weight 800 at
   `--cpu-shares 2048`; the kernel reports 174 for that request, and 303 for 4096.
   The shares column still separates classes, because a weight decides who wins
   when two containers compete, and the order this table asks for survives
   (128 below 1024 below 2048). But no shares value produces the weight the table
   states, so the weight column is a request, not a result, and must be read that
   way by anyone quoting it.
2. **`--blkio-weight` isolates nothing here.** Every request tried (100, 400, 500,
   800) left `io.weight` at its default, so a neighbour that writes hard competes
   as an equal on this host. The flag stays in the launch flags: it matters on a
   cgroup v1 host and it costs nothing. No claim of block IO separation may rest
   on it.
3. **Per device bandwidth does bind.** `--device-write-bps <device>:8mb` produced
   `io.max` with `wbps=8388608`, and a request against a device path that does not
   exist is refused by the daemon. So block IO separation has to move to
   `--device-read-bps`, `--device-write-bps` and their IOPS siblings, with the
   device derived from the host rather than hardcoded. That is item 11 of
   section 7, and section 3.1.2 records it as built.

Unchanged by this reading: the memory column, the pids column and the shares
column. Those are ceilings and a relative CPU order, and both mechanisms were
observed to bind.

### 3.1.2 The block IO limits, built 2026-09-21

Item 11 is built. `lab/resource_policy.py` holds one block IO row per tier (read
bandwidth, write bandwidth, read IOPS, write IOPS) and turns it into the four
device flags. The device is resolved from the host rather than written down: the
source of the mount behind `/var/lib/docker`, with a btrfs subvolume bracket
stripped, and the result has to exist. When it cannot be resolved to one
existing block device the launch refuses with `io_device_unavailable` before it
creates anything, because the daemon rejects a device path it cannot find and a
container started without these flags has no block IO separation at all.

Both paths that create containers carry the flags: `launch()` in
`lab/durable_runtime.py` for every component, and `labels()` for the recovery
target sites, which create their own containers.

Live reading, disposable container removed afterwards: the derived device is
`/dev/mapper/luks-d5d273a7-4705-47d4-9d9a-59d5fbc8f10c`, the production row is
64mb / 32mb / 2000 / 1000, and `io.max` inside the container reads
`252:0 rbps=67108864 wbps=33554432 riops=2000 wiops=1000`.

The rows separate the tiers. They are not calibrated: nothing here has measured
what a tier actually receives under load, so no value in that table may be
quoted as a guarantee, and the test that would make it one is still section 5.1.

### 3.2 The exact flags to add, per launch site

`lab/durable_runtime.py:133-135` becomes (values shown for a `production`
environment's Auth):

```
run -d --name <name>
  --label io.sbarbase.owner=<OWNER>
  --label io.sbarbase.tier=production
  --network <NETWORK>
  --memory 256m --memory-swap 256m --cpus 0.25
  --cpu-shares 512 --blkio-weight 400
  --pids-limit 128
  --log-opt max-size=5m --log-opt max-file=2
  --env-file <path>
```

`lab/run.py:56-59` gets the same three additions with the class of its own
services, so the stock component lab and the durable runtime do not diverge.

Device bandwidth flags are NOT added to the always-on set. Reason, and it is the
honest part: `--device-read-bps` / `--device-write-bps` map to `io.max` on one
block device, and every owned container uses the same device
(`/dev/mapper/luks-d5d273a7-4705-47d4-9d9a-59d5fbc8f10c`, VERIFIED in section 1).
Capping the database container's writes would cap its WAL fsync and checkpoint
path, which is a correctness and latency risk far larger than the fairness it
buys. Capping Auth and REST gains almost nothing, since their own I/O is small
(measured idle block I/O per container is 0 B to a few MB, `docs/evidence/idle-snapshot.json`).
The two places a bandwidth cap does help are Storage during large uploads and
Studio/meta during a large query or CSV export, so PROPOSED:

```
# operator class, on demand only
  --device-read-bps  /dev/mapper/luks-d5d273a7-4705-47d4-9d9a-59d5fbc8f10c:64mb
  --device-write-bps /dev/mapper/luks-d5d273a7-4705-47d4-9d9a-59d5fbc8f10c:64mb
# experimental environments, objects volume device, only if the experiment in
# section 5 shows it is needed and only after confirming Storage still passes
# its own checks; 64mb is a starting point to be measured, not a measured value.
```

The device path must be read at start time (`findmnt -no SOURCE /`), not
hardcoded, because it is host-specific.

### 3.3 Per-environment quota

A quota is a derived object, not a new container. PROPOSED values:

| Quota | production environment | experimental environment |
|---|---:|---:|
| Container memory, all containers of one environment | 512 MiB (Auth 256 + REST 256) | 512 MiB |
| Container CPU quota, all containers of one environment | 0.5 (`--cpus` sum) | 0.5 |
| Gateway concurrent requests, environment | 8 (today's default, unchanged) | 4 |
| Gateway concurrent requests, REST service | 3 (today's published value) | 1 |
| Gateway request rate, all services | 20 requests/second sustained, burst 40 | 5 requests/second sustained, burst 10 |
| Gateway concurrent response bytes | 8 MiB | 4 MiB |
| PostgreSQL connections for the environment | 18, as today | 18, as today |
| Disk, soft accounting cap | 10 GiB measured, warning then refusal of new writes is NOT enforceable, see section 6 | 2 GiB |
| Studio and meta | counted against this quota when running | not available |

The rate figures are PROPOSED starting points. `docs/engineering/SUSTAINED-OVERLOAD.md:3`
offers 20 RPCs/second at the target and 2/second at the neighbour as an existing
arrival profile; 20 and 5 are placed around it so the existing probe can measure
both. They are not calibrated.

### 3.4 What happens to the existing fixed ceilings

| Constant | Today | After |
|---|---|---|
| `MAX_MEMORY` `lab/combined_admission.py:11` | `6*1024**3` literal | derived: `sum(plan memory from the tier table) + OPERATOR_HEADROOM_MIB`, with `OPERATOR_HEADROOM_MIB = 512` so one Studio can run on demand. Derived value with the current plan: 6400 MiB. |
| `MAX_CPUS` `lab/combined_admission.py:12` | `6` literal | derived: `sum(plan CPUs) + OPERATOR_CPU_HEADROOM`, with 0.5. Derived value: 6.25. |
| `RESERVE` `lab/combined_admission.py:13` | `2*1024**3+512*MIB` = 2560 MiB | unchanged in value, moved into `lab/resource_policy.py` so `lab/resource_admission.py:10` (2 GiB) and `lab/install_server.py:32` (2560 MiB) can be reconciled against one name |
| `PLANNED_MIB`, `PLANNED_CPUS` `lab/install_server.py:31,33` | `5888`, `5.75` literals | computed from the tier table and the environment inventory |
| `MIN_FREE_BYTES` `lab/install_server.py:38` | `12 GiB` | unchanged value, but must be compared against the database and objects volumes separately rather than `/` and `stat -f` on the root (`lab/install_server.py:146` uses `shutil.disk_usage('/')`) |
| 6 GiB raw `MemAvailable` gates `lab/durable_runtime.py:183`, `lab/target_runtime.py:72`, `lab/run.py:150` | three copies | one function in `lab/resource_policy.py`, called by all three |
| Count guard | 4 (`lab/durable_runtime.py:278`) and 5 (`lab/run.py:170`) | one constant, plus the resource check. The count is a backstop, not the policy. |
| Studio and meta always-on | refused, because the plan would exceed the ceiling | PROPOSED: refused by default and started on demand, per `docs/engineering/STUDIO-INTEGRATION.md:131-135` option 1. Raising the ceiling instead (option 2 in that document) is an operator capacity decision and is NOT made here. |

### 3.5 Admission arithmetic change, including the container name list

The current list (`lab/combined_admission.py:29-31`) is a literal. Every future
component has to be remembered into it, which is how Studio would silently exceed
the ceiling. PROPOSED replacement, in one function
`lab/resource_policy.py:expected_inventory()`:

1. Build the expected set from state, not from prose:
   - source: `durable_runtime.DB`, `PREFIX+'-storage'`, `PREFIX+'-management-auth'`, and `PREFIX+'-'+e+'-'+kind` for `kind in ('auth','rest')` for each `e` in `source.values['environments']` (this part already exists);
   - target: `target.prefix+'-'+kind` for `kind in ('db','auth','rest','storage')` (already exists);
   - operator, when a runtime operator-services record exists: `PREFIX+'-'+e+'-studio'` and `PREFIX+'-'+e+'-meta'` for the recorded environment. This is the new part and it is why the on-demand Studio is still counted while it runs.
2. Query the daemon instead of trusting the list:
   `docker ps -a --filter label=io.sbarbase.owner=<owner> --format '{{.Names}}'`
   and require the running-prefix subset to equal the expected subset. An owned
   container that is not expected is a refusal (today this check exists at
   `lab/combined_admission.py:48-50` for two owners only).
3. For each expected container, inspect and require, in addition to today's
   `Memory > 0` and `NanoCpus > 0` (`:46`):
   - `Memory == tier.memory`
   - `MemorySwap == Memory`
   - `NanoCpus == tier.cpus * 1e9`
   - `CpuShares == tier.shares`
   - `BlkioWeight == tier.weight`
   - `PidsLimit == tier.pids`
   - `Config.Labels['io.sbarbase.tier'] == tier.name`
   Any mismatch is `policy_drift` and refuses. This turns the tier table into an
   enforced contract instead of documentation.
4. Sum `Memory` and `NanoCpus` as today (`:47`), then require
   `sum(Memory) == sum(tier.memory for expected)` so the plan and the placement
   can never disagree.
5. Keep the rest of the shape: `memory > MAX_MEMORY or cpus > MAX_CPUS` refuses
   `installation_ceiling`; `available < memory + RESERVE` refuses
   `host_memory_headroom`; `host_cpus < cpus + 2` refuses `host_cpu_headroom`
   (`:16-22`).
6. Add one refusal the current code cannot express: per-environment quota. For
   each `e`, `sum(Memory of e's containers) <= quota[e].memory` and
   `sum(NanoCpus of e's containers) <= quota[e].cpus`. Today nothing stops a
   future four-container environment from being created inside the ceiling.
7. Extend the pressure gate's container tuple
   (`lab/pressure_admission.py:7`) from the two shared containers to every
   expected container, so a stalling environment's Auth and REST are visible.
   Thresholds per class: `system` keeps 50 / 20 / 1.0; `production` refuses at
   70 / 40 / 2.0; `experimental` refuses at 50 / 20 / 1.0 (stricter, which is the
   point of the class). Values PROPOSED, to be calibrated by section 5.

### 3.6 The retained placement is grandfathered, and the database cannot be recreated

The tier flags and the class label are written at container creation, so the
placement retained on the development host, created before this policy, carries
`CpuShares: 0`, `BlkioWeight: 0`, no `io.sbarbase.tier` label and no block IO
limits. The admission refuses that placement by contract, with
`Counted container carries no policy tier`, and the refusal is deliberate:
accepting a container nobody can account for is the defect this change removes.

This section said "one recreation of the retained placement" until the constraint
was read properly, and that was wrong. Recreating the retained **database**
container has no supported path. `docs/engineering/CONTAINER-GENERATION-MIGRATION.md` records
why: the HBA authority registry and its tombstones live in that container's own
filesystem, so a new container starts with a different identity and the retained
generation pin refuses it. The document's own words are that the only supported
answers today are to keep the original container or to adopt a retained one that
still exists, and that recreation has no path. Its migration design is a deferred
checkpoint, not something to run at the end of a policy change.

The stateless services are a different case and can be recreated, which
`reconcile_mail` already does for one environment's Auth container. Recreating
them would give those containers their tiers and their block IO limits. It would
not make the database accountable, so the placement as a whole still refuses, and
that is the honest state: the database is the component whose accounting cannot
be fixed by recreation.

What that leaves, and it is not a workaround but the supported position:

1. The tier contract applies to placements created after it. That path works and
   is checked: `lab/fresh-worker-check.py` provisions a fresh real placement
   through the worker and passed 76 checks with the tiers and the block IO limits
   in place.
2. A capacity or fairness measurement of the tiers must therefore run on a fresh
   disposable placement, not on the retained one. Measuring the retained placement
   measures containers that predate the policy.
3. Migrating the retained database to a licensed generation is the deferred
   operation described in that document, and it needs its own implementation and
   crash testing before anyone runs it.

## 4. Keeping the placement math honest

After this change, three artifacts must carry the new numbers, and one class of
existing evidence becomes stale. Naming them is part of the change, not a
follow-up.

### 4.1 The plan

`lab/resource_policy.py` is the plan. It must record, in the module, dated
comments naming the measurement each number came from, in the shape
`docs/engineering/PRESSURE-ADMISSION.md:13` already uses ("experimental conservative policy
constants, not calibrated service objectives"). `lab/install_server.py`'s
`capacity()` (`:131-147`) must print the composition it was already taught to
print at `:125`, now including the tier breakdown and the operator headroom, so a
refusal names each term.

### 4.2 The evidence file

`docs/evidence/combined-runtime-admission.json` is written by
`lab/installation_runtime.py:92`. Its recorded shape today is the return of
`CombinedAdmission.check_current()`: `planned_memory_mib`, `planned_cpus`,
`available_memory_mib`, `host_pressure`, plus `target_started`. After the change
it must additionally record:

- `policy_revision`: a short id of the `lab/resource_policy.py` revision, so an
  old snapshot can never be read against a new table.
- `per_class`: `{class: {containers, memory_mib, cpus, cpu_shares, blkio_weight}}`.
- `per_environment_quota`: `{environment: {memory_mib, cpus, rest_budget}}`.
- `operator_services`: which of Studio and meta were counted in this run, or
  `none`, because a run that did not count them must not be read as if it did.
- `derived_constants`: the computed `MAX_MEMORY`, `MAX_CPUS`, `RESERVE` and
  `PLANNED_MIB` for this revision, since they are now derived and a reader
  cannot reproduce them from the file otherwise.
- `tier_labels_verified`: the list of containers whose `io.sbarbase.tier` label
  was checked, so a container outside the list is visibly unaudited.

`docs/evidence/source-stage-footprint.json`
(`lab/installation_runtime.py:41-58`) must add the same `policy_revision` and one
entry per container's actual `cpu.weight`, `io.weight`, `memory.current` and
`memory.peak` read from inside the container, so the footprint stops being memory
only. `docs/engineering/STUDIO-INTEGRATION.md:527-536` already prescribes reading
`/sys/fs/cgroup/memory.current`, `memory.peak` and `cpu.stat`; this makes the
tier weights part of the same reading.

### 4.3 The readiness matrix

`docs/reference/deployment-readiness.md` has a row for "Combined runtime admission on this
host" (`:18`) that quotes `5888 MiB of container limits and 5.75 CPUs`. That row
must be rewritten to quote the derived constants and to add one new row:
"Fair distribution and priority classes", with status and its own evidence link.
`docs/reference/deployment-readiness.md:41` ("Capacity at 10 or 100 projects | not
claimed") stays as it is: this change does not make a capacity claim.

`PROJECT.md` (repository root) carries the same numbers in its "Admission checkpoint"
(`:59-73`) and "Continue safely" sections, including "the upstream Storage probe
uses 2560 MiB/2.5 CPUs" and "The durable upstream experiment uses 2816 MiB/2.75
CPUs at two environments". Those sentences must be re-derived or deleted, not
left to drift.

### 4.4 Evidence the change invalidates

These recorded files describe a placement whose container flags no longer exist
once the tier flags land. They must be marked superseded at the top of their
`docs/` page, kept unchanged as history, and regenerated by a new dated run:

| Evidence file | Invalidated because |
|---|---|
| `docs/evidence/combined-runtime-admission.json` | records `planned_memory_mib`/`planned_cpus` for a placement created without tier flags and without `policy_revision` |
| `docs/evidence/source-stage-footprint.json` | 279 MiB across 9 containers, measured on containers with `CpuShares: 0` and `BlkioWeight: 0` |
| `docs/evidence/idle-snapshot.json` | per-container `BlockIO`, `CPUPerc` and `PIDs` under the old limits |
| `docs/evidence/pressure-snapshot.json` | two-container tuple, now wider, and thresholds now per class |
| `docs/evidence/noisy-neighbor-sql.json` | asserts database limits at `lab/noisy-neighbor-check.py:52-54` (`NanoCpus == 1e9`, `Memory == 1024**3`) and measures a neighbour with no weights in play |
| `docs/evidence/sdk-load-checks.json` and `docs/evidence/sdk-policy-regression.json` | timings recorded while REST admission was 3 and no tier existed; `docs/engineering/SDK-LOAD.md:33` already says the differences are not a controlled comparison, so these stay historical rather than being re-read as a baseline |
| `docs/evidence/gateway-sustained-checks.json`, `gateway-sustained-first-pass.json`, `gateway-sustained-failure.json` | arrival profile at REST budget 3; if the experimental REST budget becomes 1 these runs no longer describe the configured policy |
| `docs/evidence/admission-checks.json` | the four-environment count guard checks; unchanged in count, but the resource terms it refused on are now derived |

Not invalidated: `docs/evidence/connection-limit-checks.json`,
`docs/evidence/gateway-cancellation-checks.json`,
`docs/evidence/sql-deadline-checks.json` and
`docs/evidence/gateway-http-checks.json`, because this change does not touch
connection limits, cancellation retention, SQL deadlines or HTTP handling.

## 5. Measurement method to prove it works

The claim to test is narrow and falsifiable: under CPU and I/O contention, the
tier weights, the per-environment quotas and the pressure response change what a
neighbour observes, and the numbers are stable enough to state worst, typical and
best.

Extend three existing probes. Do not invent new ones: the neighbour probe already
confirms the heavy query is active before sampling
(`lab/noisy-neighbor-check.py:58-66`), refuses unexpected database limits
(`:52-54`), validates every result rather than only SQL success (`:37-39`), and
terminates only its own sessions by application name (`:81`). That discipline is
what the new runs need.

### 5.0 What can be measured today, and what is blocked

Two of the three experiments in this section cannot run on the retained
placement, and the reason is structural rather than a stale file:

- `lab/durable-check.ts` is the only thing that writes `.lab/upstream/probe.json`,
  the fixture both `lab/gateway-overload-check.ts` and `lab/sdk-load-check.ts`
  read, and it is disabled on purpose: line 8 throws "Container recreation probe
  is disabled pending HBA generation migration" and tells the reader to use the
  isolated fresh worker check instead. The `probe.json` on this host is therefore
  a leftover, and its first environment resolves to the retired environment
  `e_60332245e3a0426dd242492f`, which is why the overload vehicle dies on its
  first SQL command (recorded in `docs/engineering/plans/2026-09-21-execution-plan.md`).
- `docs/engineering/CONTAINER-GENERATION-MIGRATION.md` records that recreating a retained
  database container has no supported path, and that document is a deferred
  checkpoint rather than an implemented operation.

So the arrival driven measurement of section 5.2 and the mixed SDK load of
section 5.3 are blocked behind that migration, not merely unrun. What DOES run
today is the supported disposable path: `lab/fresh-worker-check.py` provisions a
fresh real placement through the worker in an isolated namespace and runs the
real SDK against the composed gateway through `lab/fresh-worker-sdk.ts`. It
passed 76 checks when the parent ran it on 2026-09-21, which exercises the SDK
path functionally at one environment and with no load. It is not a capacity
measurement and must not be quoted as one.

### 5.1 Experiment A: CPU and IO fairness between two environments

Extend `lab/noisy-neighbor-check.py` with a `--tiers` mode.

- Keep fixed: the two selected environments, the database container limits
  (`NanoCpus == 1e9`, `Memory == 1024**3`), the neighbour query text (50
  read-only `sum(i) FROM generate_series(1,10000)` with 100 ms pacing), the
  12-second statement timeout, the 5-second window for confirming the heavy
  query is active, and the pressure sampling points.
- Vary: (a) both environments `production`, (b) both `experimental`, (c) one
  production, one experimental, and in each case run the heavy workload from the
  production side and then from the experimental side, so the asymmetry is
  measured in both directions.
- Record per phase: median, p95, maximum, and all 50 raw values, exactly as
  today (`:40-42`), plus the inspected `CpuShares`/`BlkioWeight` of both
  environments, plus a `cpu.weight` and `io.weight` reading taken inside each
  container, plus `cpu_some10`, `io_full10` and `memory_full10` before, during and
  after.
- Report worst, typical, best: for each ordered pair and each direction, the
  worst (single highest sample), the typical (median of 50) and the best (single
  lowest sample), with the raw array retained.
- Also run one phase with three environments under load at once, two production
  and one experimental, because the two-environment case cannot separate "tier
  works" from "one heavy neighbour is simply cheap".

Failure conditions, stated before the run:

- The heavy query ends before it is observed active (`lab/noisy-neighbor-check.py:63`
  already refuses this): the run is void, not a pass.
- Any neighbour result is not exactly `50005000` (`:38`): correctness failure.
- With weights in place, the experimental environment's services show the same
  p95 (within the recorded spread of the production run) as the production
  environment's when both are saturated: the weight had no effect. That
  falsifies the fairness claim and must be written down as a failed claim, not
  smoothed into prose.
- The measured `cpu.weight` or `io.weight` does not match the table: the Docker
  mapping assumption in section 3.1 is wrong and the policy is void until fixed.

### 5.1.1 The neighbour run, taken 2026-09-21

`lab/noisy-neighbor-check.py` was run five times against the retained runtime on
this host and the three preserved runs are in
`docs/evidence/noisy-neighbor-sql-runs.json`. Each run samples 50 read only
aggregate queries at 100 ms pacing in three phases: alone, while a second
environment saturates the shared engine with an eight second CPU query, and
afterwards. The overlap was confirmed active in every run.

The result, and it is not the result the first run suggested:

| Run | Baseline median / max | During the neighbour's load, median / max |
|---|---|---|
| a | 0.876 / 1.399 ms | 0.855 / 1.230 ms |
| b | 0.869 / 1.316 ms | 0.794 / 1.231 ms |
| c | 0.845 / 1.288 ms | 0.854 / 1.348 ms |

One earlier run reported a maximum of 5.156 ms against its own baseline of
1.338 ms, with the median and p95 flat in every phase. Four runs out of five show
no effect at all, so that reading is recorded as an outlier rather than as an
effect, and the single number must not be quoted as the neighbour penalty. What
the repeats establish is the absence of a repeatable effect under this load, not
the presence of one.

Two limits on that, both important. The load is generated inside the one shared
PostgreSQL container, so no per environment cgroup weight, block IO limit or
memory ceiling takes part: this measures the shared engine and says nothing about
whether the tiers separate anything. And a bounded query shape at 100 ms pacing
is not a capacity test, so nothing here supports a statement about ten or a
hundred environments.

### 5.2 Experiment B: pressure response, continuous

Extend `lab/pressure_admission.py` so it can sample repeatedly, and drive it from
the existing sustained arrival generator.

- Extend `lab/gateway-overload-check.ts --sustained`, which already issues 20
  target arrivals/second and 2 neighbour arrivals/second for 30 seconds and
  already refuses to certify unless the published REST budget equals the lab pool
  (`lab/gateway-overload-check.ts:41`). Add a `--pressure` mode that samples
  `lab/pressure_admission.py` every 5 seconds for the whole run instead of once,
  and records the sample series.
- Hold fixed: the 600 target and 60 neighbour arrival counts, the two-second
  target RPC, the distinct returned integers, the generator's 100 ms jitter
  bound and 128 pending cap (`docs/engineering/SUSTAINED-OVERLOAD.md:5`), and the neighbour
  acceptance rule that the neighbour must be correct AND fast.
- Record: the sample series with timestamps, every gate response status, the
  count of target successes and rejections, the neighbour p95, and the exact
  moment the responder would have acted (given the proposed thresholds) versus
  the moment it did act.
- Failure conditions: any unexpected target status (neither a correct 200 nor a
  retryable 429 with `retry-after: 1`); any neighbour failure; any knee where the
  responder acts after the host pressure has already returned below threshold
  (an action that arrives late is a failure of the experiment, not a pass).

### 5.2.1 The sampler and the response, built 2026-09-21

The sampler and the response exist now. The arrival-driven experiment of section
5.2 does not, and the paragraph after the table below says why.

`lab/pressure_admission.py` takes a bounded series (`series()`, default one
reading every 5 seconds over a 30-second window, at most 120 readings over ten
minutes), reduces it (`summarise()`: the count, the same three avg10 values per
container as the mean, the maximum and the most recent reading, and every
crossing with the readings over it, the span between the first and the last, and
the peak), and returns one decision (`response()`). The durations come from the
measured instants each reading started rather than from a count of intervals, and
a series reports the elapsed span next to the requested window because a reading
itself costs four Docker calls. The admission-time `snapshot()` and `refusal()`
are unchanged; `combined_admission.py`, `durable_runtime.provision` and `notify`
call the same functions with the same vocabulary.

Level 1 of section 7 step 7 is implemented and nothing above it:

| Level | Design asks | State |
|---|---|---|
| 1 | refuse new provisioning with the existing safe capacity reason, and record the event | built: `response()` refuses while the most recent reading is at or over a threshold and appends each crossing to `.lab/pressure-crossings.jsonl`, one JSON object per line, flushed and fsynced |
| 2 | call the gateway pause lease for each experimental environment over threshold for three consecutive samples | not built: no per-environment class exists in the runtime state, so "experimental" cannot be selected, and the lease is in-process, is lost on restart, and does not prove a paused environment's SQL stopped (`docs/engineering/GATEWAY-DRAIN.md`) |
| 3 | pause every class except `system` and refuse all starts | not built: same reasons, and it stops or restricts work that is already running, which this increment was explicitly not to do |

Every decision returns the list of what is not implemented next to the action, so
a caller cannot present level 1 as a continuous remedy.

The measurement is `lab/pressure-response-check.py`, evidence in
`docs/evidence/pressure-response-checks.json`. It creates one disposable
container (private cgroup namespace, 0.25 CPU quota, 128 MiB ceiling, no network,
the pinned database image) holding sixteen internal busy loops, takes the series,
stops the load inside the same container, and takes a second series. Run
2026-09-21: `cpu_some10` crossed in four of the five load readings, peaking at
77.72; the response refused with `pressure_cpu_some10` and appended one ledger
line; after the load stopped, `cpu_some10` fell from 32.26 to 6.80 and the
response admitted with no crossing. The retained runtime is not started or
touched, and the container is removed.

What this does not replace, and the arrival-driven form is still owed: the
driver does not run the sustained arrival generator, so the 600 target and 60
neighbour arrivals, the two-second target RPC, the neighbour acceptance rule and
the failure condition "the responder acts after host pressure has already
returned below threshold" are all untested here. That failure condition is
partially covered: the response decides from the most recent reading and stops
refusing within one reading of the recovery, which the second series shows. But
`lab/gateway-overload-check.ts --sustained --pressure` is not built, because it
needs the retained durable runtime running, and section 5.1.1 already measured
that this arrival profile produces no repeatable neighbour effect on this host.
So Experiment B is one third done: the tool and the response exist and are
measured on their own; the arrival-driven run is not.

### 5.3 Experiment C: SDK mix under tiers and quotas

Extend `lab/sdk-load-check.ts`, which already runs one then four workers per
environment over five operation types for two ten-second phases
(`docs/engineering/SDK-LOAD.md:7`) and already samples pressure mid-phase
(`lab/sdk-load-check.ts:38`).

- Reuse the same workload and pacing so the new numbers are comparable to
  `docs/evidence/sdk-policy-regression.json`, and record the tier of each
  environment in the artifact.
- Add a third phase where one environment is `experimental` and rate-limited to
  its proposed quota while the other is `production`, and assert that the
  production environment's p95 stays inside the production run's spread.
- Failure conditions: any failed operation; any production p95 more than the
  recorded spread above its own policy-regression value under the experimental
  neighbour; any unexplained 429 in the production environment.

### 5.4 What none of the three can prove

All three run on one workstation that also runs the generator, and the runtime's
CPU quota is a fraction of a 24-CPU host. They cannot establish capacity for 10
or 100 environments, cannot establish a production SLO, and cannot prove
allocation inside one shared PostgreSQL engine. `docs/engineering/NOISY-NEIGHBOR.md:11`,
`docs/engineering/SDK-LOAD.md:27` and `docs/engineering/reviews/capacity-method.md:16` all already say a
generator on the same machine is not a capacity measurement; that stays true
after this change.

## 6. The honest limits

### 6.1 What one shared engine can never guarantee

1. CPU inside PostgreSQL. All environment databases live in one postmaster. A
   tenant's query is a backend process of that container. `--cpu-shares` on the
   container shapes how the engine competes with other containers, not how two
   backends compete with each other. `docs/engineering/CONNECTION-BUDGET.md:22` and
   `docs/engineering/SQL-DEADLINES.md:30` both already refuse to claim this; this policy does
   not change that.
2. Memory inside PostgreSQL. The database container's ceiling is one pool.
   `shared_buffers`, `work_mem` per sort and per hash, and the page cache are
   shared. A tenant with many concurrent sorts can consume the engine's memory
   and cause a neighbour's query to spill or the container to be OOM killed.
   Per-database memory accounting does not exist in PostgreSQL. `docs/engineering/reviews/capacity-method.md:10`
   states the work_mem multiplication; the policy cannot bound it.
3. Disk I/O inside PostgreSQL. `io.weight` on the container sets one weight for
   all of the engine's I/O. The WAL writer, the checkpointer and every tenant's
   reads are in that one cgroup. Weighting the container up protects the engine
   from other containers; it does nothing between tenants.
4. A hard per-tenant disk quota. There is no per-database quota in PostgreSQL and
   no per-prefix quota in the file-backed Storage backend. The volume is one
   btrfs filesystem (VERIFIED, section 1), so filling it is an installation-wide
   event. The soft accounting cap in section 3.3 can warn and can refuse new
   provisioning, and the gateway can refuse new uploads for a tenant, but it
   cannot stop a database INSERT from growing. A btrfs qgroup per subvolume would
   be a hard cap at the filesystem layer and is not designed here; it is recorded
   as `open` in `docs/engineering/RESOURCE-ADMISSION.md:20` and stays open.
5. Isolation from a compromised service credential. `docs/engineering/reviews/supabase-feasibility.md:32`
   and `:36`: shared canonical roles mean `ALTER ROLE authenticated` affects the
   cluster, and a compromised cluster administrator reaches every database. The
   `service_role` bypass is scoped to its database connection, but the storage
   process can reach every tenant's configuration. A resource policy does not
   change any of this.
6. Any guarantee about the host itself. `docs/engineering/COMBINED-RUNTIME.md:9` records that
   unrelated workloads remain outside ownership and are accounted for only
   indirectly through host headroom and pressure. A policy inside the
   installation cannot reserve anything against a process outside it.
7. Sustained behaviour. `docs/engineering/PRESSURE-ADMISSION.md:16` refuses to claim continuous
   response today, and this design only proposes it. Until Experiments B and C
   have run and their artifacts are committed, every statement about continuous
   response is design.

### 6.2 The escape hatch that already exists

This is the strongest honest answer, and it is already implemented as far as
placement goes.

- Placement is a per-runtime record, not a property of the host:
  `runtime_routing` in `src/control/catalog.ts:52` stores `maintenance` and a
  validated `placement` (`:301-306`), changed only by trusted operator code
  through `changeRuntimeRouting` (`:309-325`) with a monotonic revision that
  refuses stale writes.
- `src/gateway/managed.ts:26` composes the configured route with the recorded
  placement on every request, so moving an environment is a catalog write plus a
  routing revision, not a redeployment.
- The moved-target path already runs an environment with its own database, its
  own Auth, its own REST and its own Storage containers:
  `lab/target_runtime.py:21` fixes the prefix shape `sbarbase-restore-<hex12>`, and
  `:30-37` inspects `db`, `auth`, `rest`, `storage` for that prefix.
  `docs/evidence/target-placement-rehearsal.json` records that placement starting,
  serving and stopping.
- Ownership is independent of placement by decision: `docs/decisions/README.md:8`
  ("Hierarchy | Installation > organization > project > environment; ownership
  independent of placement"), restated in `docs/engineering/reviews/supabase-feasibility.md:9`
  as the second deployment profile, one independent PostgreSQL cluster per
  environment, kept because it "preserves independent canonical roles, permits
  stronger operational separation".
- Therefore the escape hatch for a tenant that genuinely needs isolation is:
  move that environment to its own cluster (its own containers, its own
  `pgdata`, its own object volume, its own weights), or to another server. Every
  guarantee in section 3 applies per installation, so a moved environment gets
  the full set to itself.

Raising the per-environment quotas is not the escape hatch. If an environment
needs more than its class allows, the honest choices are: give it the
`production` class, or move it.

### 6.3 What a fork or a rewrite would NOT fix

A fork of Auth, PostgREST or Storage, and a rewrite of the gateway, do not fix
any of the following, and none of them should be proposed as if they would:

1. CPU, memory and I/O inside one PostgreSQL postmaster. PostgreSQL has no
   per-database resource governor. Partitioning them requires separate clusters,
   which is placement, not a fork.
2. Disk quotas on one btrfs filesystem shared by several tenants. That is a
   filesystem-layer problem; a fork of the file-backed Storage backend can
   implement an accounting check but cannot make the kernel refuse a write from
   another process on the same filesystem.
3. Host-level fairness against processes outside the installation.
4. The shared canonical role identities (`docs/engineering/reviews/supabase-feasibility.md:16`:
   roles are cluster-global). Forking Auth does not create per-database role
   namespaces in PostgreSQL.
5. The page cache. It is not accounted to a cgroup in a way that a fork of a
   user-space service can partition.

What a fork would make worse: `docs/decisions/README.md:7` keeps Supabase for API and
SDK compatibility, `:13` refuses a home-grown administration surface for drift
reasons, and `docs/engineering/reviews/supabase-feasibility.md:7` refuses to rewrite
authentication. A fork of the components would trade a resource problem that
placement already solves for a compatibility problem that placement does not.

## 7. Implementation task list

Ordered so each step is independently landable, testable, and does not depend on
the next. Paths are exact. All Python runs use `/usr/bin/python3` per
`/home/sbarah/AGENTS.md` and `docs/reference/deployment-readiness.md` (`python3` alone is
the Hermes venv). JavaScript runs use `bun`, never npm.

### Step 1: one policy module, no behaviour change

Create `lab/resource_policy.py` with the tier table of section 3.1, the quota
table of section 3.3, `RESERVE`, `OPERATOR_HEADROOM_MIB`, `OPERATOR_CPU_HEADROOM`,
the single 6 GiB headroom helper, and `expected_inventory()` returning the
section 3.5 set. Add `lab/test_resource_policy.py` asserting that
`sum(tier.memory for the current plan) == 5888` and
`sum(tier.cpus) == 5.75`, that every class in the table is used by at least one
container, and that `expected_inventory()` returns the same set the current
literal list at `lab/combined_admission.py:29-31` returns for the four retained
environments and the retained target.

Verify:

```
/usr/bin/python3 -m unittest discover -s lab -p 'test_resource_policy.py'
/usr/bin/python3 -m unittest discover -s lab -p 'test_combined_admission.py'
/usr/bin/python3 -m unittest discover -s lab -p 'test_combined_headroom.py'
```

Independent because nothing imports the new module yet.

### Step 2: derive the constants

Change `lab/combined_admission.py:11-13` and `lab/install_server.py:31-38` to
import from `lab/resource_policy.py`. Keep the refusal vocabulary
(`installation_ceiling`, `host_memory_headroom`, `host_cpu_headroom`,
`measurement_unavailable`, `unbounded_limits`) unchanged, because
`lab/test_combined_admission.py` and the worker protocol depend on it.

Verify:

```
/usr/bin/python3 -m unittest discover -s lab -p 'test_combined_admission.py'
/usr/bin/python3 -m unittest discover -s lab -p 'test_combined_headroom.py'
/usr/bin/python3 -m unittest discover -s lab -p 'test_resource_admission.py'
/usr/bin/python3 lab/install_server.py check
```

The last command must print its existing composition line with the derived
numbers and, on this host right now, still refuse or pass for the same reason it
does today.

### Step 3: add the launch flags behind a capability check

Add `cpu_shares`, `blkio_weight` and `tier` parameters to
`lab/durable_runtime.py:114 launch()` and emit them at `:133-135`. Set
`io.sbarbase.tier` as a label. Do the same in `lab/run.py:49` and `:56-59`. Do
not change which containers exist and do not change any `--memory`, `--cpus` or
`--pids-limit` value in this step.

Add to `lab/test_runtime_reuse.py` a case asserting that a retained container
whose `CpuShares` differs from the requested tier is refused, mirroring the
existing image and environment drift checks at `lab/durable_runtime.py:120-126`.
`lab/run.py:65 validate_existing` needs the same addition, and
`lab/README.md:38-40` already records that resource-limit drift "still needs
checks"; this step adds exactly that check.

Verify:

```
/usr/bin/python3 -m unittest discover -s lab -p 'test_runtime_reuse.py'
/usr/bin/python3 -m unittest discover -s lab -p 'test_native_stages.py'
/usr/bin/python3 lab/durable_runtime.py up
docker inspect --format '{{.HostConfig.CpuShares}} {{.HostConfig.BlkioWeight}} {{.Config.Labels}}' sbarbase-durable-db
docker exec sbarbase-durable-db cat /sys/fs/cgroup/cpu.weight /sys/fs/cgroup/io.weight
/usr/bin/python3 lab/durable_runtime.py stop
```

The two `docker exec` reads are the VERIFIED step that closes the INFERRED
mapping noted in section 3.1. If `cpu.weight` is not approximately
`1 + ((2048-2)*9999)/262142` (about 78) they do not match the table and the
mapping must be recorded as measured, then the table updated to what the kernel
actually reports.

### Step 4: admission from the inventory and the tier contract

Replace `lab/combined_admission.py:29-31` with `expected_inventory()`, add the
`docker ps -a --filter label=io.sbarbase.owner=...` cross-check, the per-field
tier assertions and the per-environment quota sum from section 3.5, and extend
the pressure tuple at `lab/pressure_admission.py:7` to the expected set with the
per-class thresholds of section 3.5 item 7.

Verify:

```
/usr/bin/python3 -m unittest discover -s lab -p 'test_combined_admission.py'
/usr/bin/python3 -m unittest discover -s lab -p 'test_pressure_admission.py'
/usr/bin/python3 lab/pressure_admission.py
/usr/bin/python3 lab/combined-supervisor-check.py
```

The last command is the real combined check and it must still refuse for the same
reason it refuses today (`docs/engineering/COMBINED-RUNTIME.md:3`: source preflight refuses
because the retained legacy source has not been adopted). A refusal for a new
reason is a defect in this step.

### Step 5: record the policy in the evidence

Add the fields of section 4.2 to the artifact written at
`lab/installation_runtime.py:92` and to `record_source_stage_usage()` at
`:41-58`. Add a `lab/test_admission_evidence.py` (or extend
`lab/test_combined_headroom.py`) asserting that a snapshot without
`policy_revision` is rejected by any reader that consumes these files, so an old
snapshot cannot be read against a new table.

Verify:

```
/usr/bin/python3 -m unittest discover -s lab -p 'test_combined_headroom.py'
docker inspect --format '{{json .HostConfig}}' sbarbase-durable-db
/usr/bin/python3 lab/installation_runtime.py up
python3 -m json.tool docs/evidence/combined-runtime-admission.json
```

Note: the third command starts owned services. It must only be run when the host
has the headroom the preflight asks for, and stopped afterwards.

### Step 6: the shaped gateway, quotas and rate

Add a per-environment token bucket and a concurrent-response-bytes budget to
`src/gateway/concurrency.ts` next to the existing counters (`:32-33`), keyed the
same way so an API key change cannot reset them (`docs/engineering/GATEWAY-OVERLOAD.md:21`).
Read the per-class values from the route, not from a constant, so
`src/gateway/managed.ts:28` can publish them. Keep 429 for a rate refusal and 503
for a process-wide refusal so existing callers keep their vocabulary.

Verify:

```
bun test tests/concurrency.test.ts tests/drain.test.ts
bun test
bun lab/gateway-overload-check.ts
bun lab/gateway-overload-check.ts --sustained
```

The existing sustained check refuses to pass unless the published REST budget
equals the lab pool (`lab/gateway-overload-check.ts:41`), which is the regression
guard for this step.

### Step 7: the continuous responder

Partly built, 2026-09-21. Level 1 now exists inside `lab/pressure_admission.py`
as `series()`, `summarise()` and `response()` rather than as a separate
`lab/pressure_responder.py`, because one module already owns the measurement and
its thresholds, and the response is decided from a summary of that measurement
rather than from a new signal. Section 5.2.1 records what that covers, what it
excludes, and the measurement that exercises it. What remains of this step: no
process calls the response on a schedule, no per-class threshold can be
evaluated because no per-environment class exists in the runtime state, and
levels 2 and 3 are unbuilt, so nothing here stops or restricts running work.

The rest of this step is the design as written:

Add `lab/pressure_responder.py`: sample `pressure_admission.snapshot()` every 5
seconds, evaluate the per-class thresholds, and act in graduated steps, all of
which reuse existing machinery:

- Level 1, any class over threshold: refuse new provisioning by returning the
  existing safe capacity reason, and record the event. No change to running work.
- Level 2, `experimental` over threshold for three consecutive samples: call the
  pause lease for each experimental environment. The lease already exists at
  `src/gateway/concurrency.ts:7-30` and is reached through
  `src/gateway/managed.ts:9`; `docs/engineering/GATEWAY-DRAIN.md:3` records that it is
  trusted in-process operator code and not an HTTP endpoint.
- Level 3, `system` over threshold or `MemAvailable` below `RESERVE`: pause every
  class except `system`, and refuse all new starts.

Honesty requirements inside the responder itself: record that the pause is
in-process only and disappears on restart (`docs/engineering/GATEWAY-DRAIN.md:9`), record
that a paused environment's gateway does not prove its SQL stopped
(`docs/engineering/GATEWAY-DRAIN.md:9`, `docs/engineering/REST-CANCELLATION.md:21`), and never resume
automatically without an explicit operator action or a recorded dwell time.

Verify:

```
/usr/bin/python3 -m unittest discover -s lab -p 'test_pressure_admission.py'
bun lab/gateway-overload-check.ts --sustained --pressure
bun test
```

### Step 8: the measurements

Extend `lab/noisy-neighbor-check.py`, `lab/pressure_admission.py` and
`lab/sdk-load-check.ts` per section 5, run Experiments A, B and C, and commit the
artifacts. Only after the artifacts exist may the constants in
`lab/resource_policy.py` be quoted anywhere as more than proposals.

Verify:

```
/usr/bin/python3 lab/noisy-neighbor-check.py --tiers
bun lab/gateway-overload-check.ts --sustained --pressure
bun lab/sdk-load-check.ts --policy-regression --tiers
python3 -m json.tool docs/evidence/noisy-neighbor-sql.json
```

### Step 9: the documents

Update in one change: `docs/reference/deployment-readiness.md` (row `:18` and a new
fairness row), `PROJECT.md` (the admission checkpoint numbers), a new
`docs/engineering/RESOURCE-POLICY.md` carrying the tier table, the quota table, the derived
constants and the measurement status of each, and superseding notes at the top of
each file listed in section 4.4.

Verify:

```
/usr/bin/python3 -m unittest discover -s lab -p 'test_doc_references.py'
/usr/bin/python3 lab/install_server.py check
```

`lab/test_doc_references.py` exists (`lab/` listing) and is the guard that
document references still resolve; it must be run because step 9 edits many
cross references.

### Step 10: the capability and limit check

`docs/reference/deployment-readiness.md` `:34` records that `deploy/server-acceptance.sh`
is the one command that proves the whole path. Before any statement about
fairness is repeated outside the repository, run it on a real server, because
every number in this document was produced on one workstation that also runs the
generator, the browser and the user's other projects.

```
sudo deploy/server-acceptance.sh --rehearse --install-unit --service-user <account> --home <home> --bun-dir <bun bin> --docker-host unix:///var/run/docker.sock
```

## 8. Questions with no answer in the code

- No per-tenant memory, CPU or I/O accounting exists inside PostgreSQL or
  Storage. Searched: `lab/`, `src/`, `tests/`, `docs/` for `work_mem`, `pg_stat_statements`,
  `io.max`, `cpu.max`, `memory-reservation`, `oom`, `quota`. Only
  `docs/engineering/reviews/capacity-method.md:10-11` discusses work_mem as a risk, and
  `docs/engineering/RESOURCE-ADMISSION.md:24` lists tenant disk quotas as still required. Not found.
- No rate limiter of any kind exists in the gateway. Searched `src/gateway/` and
  `src/http/` for `rate`, `bucket`, `tokens`. Not found.
- No continuous pressure monitor exists; `lab/pressure_admission.py` is called
  once per provisioning attempt (`lab/durable_runtime.py:286`). VERIFIED absent
  by that single call site.
- No measured Studio or postgres-meta footprint exists anywhere in
  `docs/evidence/`; `docs/engineering/STUDIO-INTEGRATION.md:506-507` states that upstream
  publishes no figure and that nothing was started. Not found.
- No verified cgroup v2 reading of the effective `cpu.weight` or `io.weight` for
  any owned container exists in the evidence. The two containers inspected for
  this document are stopped and report `CpuShares: 0` and `BlkioWeight: 0`.
  Step 3 of section 7 is the first time this gets read.
- The repository contains no decision row selecting a tier policy. The closest is
  `docs/decisions/README.md:15` ("Capacity | Measure peak workloads and reserve recovery
  headroom before admission"), which is the mandate for this document, not an
  answer inside it.