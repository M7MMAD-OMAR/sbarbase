# Capacity review

Reviewed 2026-09-20. This is a measurement plan, not a benchmark result.

## Corrections to previous advice

- 10 projects with 500 daily visits each means 5,000 daily visits, not concurrent users. Assuming 20 backend requests per visit gives 100,000 requests per day, about 1.16 requests per second on average. Assuming 20% arrives within 600 seconds gives 33.33 requests per second. Neither request count nor peak distribution has been measured.
- Proposed 4 vCPU / 8 GB and 8 vCPU / 16 GB machines are comparison candidates, not sizing conclusions. The official Supabase Docker guide lists requirements for its stack, not for this proposed multi-project architecture [1]. Do not multiply its minimum memory by project count or assume shared services consume no incremental resources.
- Counting containers is not measuring resource usage. A single process may contain per-project pools, workers, caches, sockets and replication slots. Containers have no resource limits by default [2].
- Separate databases in a cluster do not have independent memory or CPU budgets. PostgreSQL work_mem is per query operation, and concurrent operations can multiply consumption [3].
- Per-database SQL execution time is not a direct measurement of per-project CPU consumption. Use database statistics and statement statistics to find workload costs, and system metrics for host saturation [4].
- Latency under 300 ms and server errors below 0.5% were suggested examples. They are not accepted product commitments or existing performance results.

## Reproducible comparison

Compare the smallest viable configurations using the same machine class, dataset, client location, TLS, versions, indexes and request mix. Include the monitoring overhead. Generate load from a separate machine and monitor that generator too.

Record costs at 0, 1 and 10 environments, both idle and active. Measure RSS/container accounting, host available memory, CPU, disk latency and throughput, connections, network bytes, WAL, slot lag and service startup times. Do not simply sum process RSS because shared pages can be counted repeatedly.

Use representative API reads with RLS, writes, RPC, password login, session refresh, realtime connections and message fanout, functions and scheduled jobs. Keep static asset traffic separate. Test warm caches and a working set larger than cache. Test normal data volumes and projected growth.

Use arrival-rate scenarios to avoid hiding overload when a slowing server causes the generator to issue fewer requests. Session-based concurrency tests remain useful for user journeys. Record dropped iterations and offered, completed and rejected traffic separately [5]. Report expected rate-limit responses independently from server errors; do not hide degraded user experience by excluding them from all objectives.

Test normal load, spikes, sustained load, one busy environment with nine quiet ones, and recovery after process/database restart. Test backup and maintenance activity during traffic. Calculate usable capacity from meeting agreed latency/error objectives with headroom, not from the highest achieved throughput.

## Storage and recovery

Budget application rows, indexes, growth, WAL retention, logs, temporary work, maintenance and restore workspace. Keep backup copies outside the primary host and test recovery independently. Report recovery time and the age of the newest recoverable data. A replica and a backup solve different failure modes.

## Decision output

Publish a versioned benchmark report with hardware, storage, OS, image versions, scripts, workload mix, dataset, runtime topology, measurements, failure observations and remaining limitations. No server recommendation is final until those results exist.

## Sources

1. [Supabase Docker requirements](https://supabase.com/docs/guides/self-hosting/docker)
2. [Docker resource constraints](https://docs.docker.com/engine/containers/resource_constraints/)
3. [PostgreSQL resource consumption](https://www.postgresql.org/docs/current/runtime-config-resource.html)
4. [PostgreSQL pg_stat_statements](https://www.postgresql.org/docs/current/pgstatstatements.html)
5. [k6 open and closed workload models](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/open-vs-closed/)
