# Cgroup pressure admission

New environment allocation now measures the owned database and shared Storage container's cgroup v2 pressure after memory/filesystem checks and before connection budgeting or credential persistence. Missing PSI files, an unsupported cgroup namespace, malformed values or failed Docker commands prevent allocation. A measured pressure threshold returns the existing safe capacity refusal.

Local thresholds use ten-second averages:

| Resource | Signal | Refuse at |
|---|---|---|
| CPU | `some avg10` | 50% |
| I/O | `full avg10` | 20% |
| Memory | `full avg10` | 1% |

These are experimental conservative policy constants, not calibrated service objectives. The Linux kernel defines `some` as time when some tasks stall and `full` as time when all non-idle tasks stall. This is stall time, not CPU utilization or disk throughput. See [Linux PSI documentation](https://docs.kernel.org/accounting/psi.html).

[Live snapshot](../evidence/pressure-snapshot.json) confirms readable metrics in both private cgroup namespaces. Five tests verify parsing, boundary refusals, incomplete metrics and refusal before allocating credentials. Sixteen more cover the repeated series, the summary, the response and the unchanged snapshot contract, for 21 tests in `lab/test_pressure_admission.py`.

This is an admission-time gate, not a continuous monitor or a remedy for an existing overloaded tenant. Short spikes may be smoothed out by the average. Existing environments can reconcile despite admission pressure. Cgroup v1, non-private cgroup namespaces and remote host resource admission remain unsupported. No deliberate I/O exhaustion was performed.

## Repeated sampling and the response

`lab/pressure_admission.series()` takes a bounded series of readings (default one every 5 seconds over 30, and at most 120 readings over ten minutes) and `summarise()` reduces it to the count, the same three avg10 values per container as the mean, the maximum and the most recent reading, and every threshold crossing with the readings over it, the span between the first and the last, and the peak. A reading is validated by the same `refusal()` that guards admission, so an incomplete or out-of-range reading cannot enter a summary. The durations are built from the measured instants each reading started, not from a count of intervals, and the elapsed span of a series is reported next to the requested window because a reading itself costs four Docker calls.

The response the design supports today is one level, in `pressure_admission.response()`: refuse new admissions while the most recent reading is at or over a threshold, and append every crossing to the durable ledger `.lab/pressure-crossings.jsonl`, one JSON object per line, each flushed and fsynced before the next. The refusal reason is the measured metric name, which is the same reason the admission-time gate already produces. A crossing that has ended is recorded and does not refuse: the ledger is history, the decision is the present reading.

Nothing else is implemented, and every decision carries that list: no running container is killed, paused, throttled or reconfigured, and no per-class threshold exists, because no per-environment class exists in the runtime state. `docs/engineering/RESOURCE-POLICY.md` section 7 step 7 names levels 2 and 3; they rest on the gateway pause lease, which [gateway drain](GATEWAY-DRAIN.md) records as in-process, lost on restart, and no proof that a paused environment's SQL stopped. Section 2 item 6 of the resource policy is where the rest of the gap is written down.

`lab/pressure-response-check.py` measures the response on disposable containers only, and writes [pressure-response-checks.json](../evidence/pressure-response-checks.json). Run 2026-09-21: one container with a private cgroup namespace, a 0.25 CPU quota and a 128 MiB ceiling holding sixteen internal busy loops crossed `cpu_some10` in four of five readings, peaking at 77.72, and the response refused admissions with `pressure_cpu_some10`, appending one ledger line. With the load stopped inside the same container, `cpu_some10` fell from 32.26 to 6.80 across the second series and the response admitted. The probe costs the host its 0.25 CPU quota for the duration, never touches the retained runtime, and removes the container it creates.

Limits of that measurement, stated rather than smoothed over: the crossing is the pressure of one disposable container, which is not the pressure of the shared database and Storage containers the gate reads at admission, and it says nothing about a tenant inside the PostgreSQL engine. The host-wide PSI readings recorded in the same evidence include the probe's own cgroup, because PSI aggregates from descendants, so they are not an estimate of load from outside the probe. A refusal at admission is still not sustained isolation.
