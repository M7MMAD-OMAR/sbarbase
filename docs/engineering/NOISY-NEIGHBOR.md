# Initial neighboring-environment SQL probe

This is one bounded local microbenchmark, not a capacity forecast. The database container's inspected limits were one CPU and 1 GiB RAM. Four environment runtimes were retained. One environment executed a read-only calculation loop for eight seconds; a different environment executed 50 small aggregate queries in each phase, paced by 100 ms sleeps. A 12-second PostgreSQL statement timeout bounded the heavy statement. The probe confirmed that the heavy query was active before loaded sampling and that its process remained live through that sample window. All 150 aggregate results were validated.

| Phase | Samples | Median, ms | p95, ms | Maximum, ms |
|---|---:|---:|---:|---:|
| Before load | 50 | 0.771 | 1.300 | 1.429 |
| With neighboring load | 50 | 0.816 | 1.348 | 1.445 |
| After load | 50 | 0.736 | 0.902 | 1.008 |

[Raw timings and pressure measurements](evidence/noisy-neighbor-sql.json) are saved for reproducibility. PostgreSQL client timing measures SQL execution plus the local connection round trip inside the database container, not end-user request latency. The small observed difference cannot establish tenant isolation under heavier or longer workloads. The pressure snapshot remained below admission thresholds; no live threshold crossing is claimed.

The script uses existing scoped service credentials through a private stdin pipe. It creates no application data, emits no credentials and terminates only sessions carrying its unique application name before stopping the owned runtime. Unrelated Docker services remain running.

Next measurements must cover HTTP/SDK request mixtures, writes and uploads, multiple busy environments, connection queuing, sustained load, disk pressure, repeated runs and recovery while loaded. A daily visitor count still cannot be converted to project capacity from this probe.
