# Cgroup pressure admission

New environment allocation now measures the owned database and shared Storage container's cgroup v2 pressure after memory/filesystem checks and before connection budgeting or credential persistence. Missing PSI files, an unsupported cgroup namespace, malformed values or failed Docker commands prevent allocation. A measured pressure threshold returns the existing safe capacity refusal.

Local thresholds use ten-second averages:

| Resource | Signal | Refuse at |
|---|---|---|
| CPU | `some avg10` | 50% |
| I/O | `full avg10` | 20% |
| Memory | `full avg10` | 1% |

These are experimental conservative policy constants, not calibrated service objectives. The Linux kernel defines `some` as time when some tasks stall and `full` as time when all non-idle tasks stall. This is stall time, not CPU utilization or disk throughput. See [Linux PSI documentation](https://docs.kernel.org/accounting/psi.html).

[Live snapshot](evidence/pressure-snapshot.json) confirms readable metrics in both private cgroup namespaces. Five tests verify parsing, boundary refusals, incomplete metrics and refusal before allocating credentials. The full Python suite contains 27 tests.

This is an admission-time gate, not a continuous monitor or a remedy for an existing overloaded tenant. Short spikes may be smoothed out by the average. Existing environments can reconcile despite admission pressure. Cgroup v1, non-private cgroup namespaces and remote host resource admission remain unsupported. No deliberate I/O exhaustion was performed.
