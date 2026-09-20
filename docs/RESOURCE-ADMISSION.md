# Local resource admission

New durable environments must pass both the existing four-environment guard and a fresh resource snapshot while the runtime operation lock is held. A refused allocation does not persist new credentials or start database provisioning. Existing environments may reconcile even when new allocation is refused.

## Current policy

- Available host memory must cover 2 GiB of reserve plus 512 MiB for the new environment's Auth/REST container ceilings.
- Both database and object-storage filesystems must report at least 6 GiB available, a 5 GiB reserve plus a 1 GiB planning allowance.
- Filesystems with reported inode capacity must have at least 10,000 available inodes.
- Missing measurements fail closed with a generic runtime failure. A measured threshold refusal uses the existing safe capacity status. Neither exposes raw subprocess output.

These are conservative local policy constants, not benchmark-derived production requirements, preallocated resources or disk quotas. PostgreSQL/Storage memory growth and future data growth are not bounded by the per-environment allowance. Shared filesystems are checked individually, not added together as independent capacity.

## Measurement and evidence

`lab/resource_admission.py` checks the native Linux Docker daemon identity, reads host `MemAvailable`, and executes read-only filesystem checks inside the owned database and Storage containers at their actual volume paths. Remote daemons and Docker Desktop are unsupported by this host-memory policy. Each Docker measurement has a ten-second timeout.

[Saved live snapshot](evidence/resource-snapshot.json) was captured with four retained environments running. Its null refusal only means the measured resource thresholds passed at that instant. The independent count guard remains full, so it does not authorize a fifth runtime. Five new Python tests cover threshold cases, missing measurements, refusal before credential persistence and Btrfs inode reporting. No real memory or disk exhaustion was induced.

On this host, `df -i` reports zero inode totals on Btrfs. Recognized Btrfs volumes report inode availability as null rather than zero; other filesystems do not receive that exception. Filesystem recognition uses the [Linux BTRFS_SUPER_MAGIC definition](https://raw.githubusercontent.com/torvalds/linux/master/include/uapi/linux/magic.h). Btrfs allocation has additional metadata considerations and standard free-space tools are insufficient for a complete storage health guarantee, as described in the [Btrfs filesystem documentation](https://btrfs.readthedocs.io/en/latest/btrfs-filesystem.html). Btrfs metadata health and quotas remain open gates.

## Still required

CPU saturation, I/O pressure, PostgreSQL connection budgets, cgroup limits, persistent resource reservations, tenant disk quotas, backup-size-aware recovery reserve, monitoring after admission and workload benchmarks. This snapshot cannot prevent another process from consuming resources immediately afterward. It does not establish capacity for 10 or 100 projects.
