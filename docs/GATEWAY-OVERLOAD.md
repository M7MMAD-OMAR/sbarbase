# Application gateway overload and streaming

The local gateway admits at most 8 requests per environment and 32 application requests across one process, without a queue. Excess work receives 429 (environment) or 503 (aggregate) with Retry-After. Keys and service routes share the environment budget. Trusted route configuration may additionally cap each service; service, environment and process counts are acquired atomically and released together. The durable lab publishes a REST cap of 3 from the same PGRST_DB_POOL launch configuration. Invalid requests fail before allocation. Management authentication has a separate budget; these limits do not cover every process or socket.

Slots remain occupied through response streaming and release once on completion, error, cancellation or the 30-second response deadline. Request bodies retain their size and time limits. A separate 30-second pre-header deadline releases admission even when an injected transport ignores cancellation, returns 504, and cancels a response body that arrives late. Client cancellation returns 408. Native upstream fetch also has its own timeout. The gate cannot forcibly terminate arbitrary underlying work that ignores cancellation, so this is not proof of upstream resource containment. This is not a distributed rate limiter or DDoS protection.

The loopback HTTP adapter explicitly observes stream failures, respects backpressure and destroys the response connection on a stream error. A local Bun 1.3.14 reproduction motivated this adapter: partial streamed responses through the previous path could appear successful at deadline. Compression is forwarded without implicit decompression so headers match wire bytes. See [Bun fetch options](https://bun.com/reference/globals/BunFetchRequestInit).

## Evidence

- 8 supervisor smoke checks passed after the adapter change, including idle worker restart and graceful shutdown.
- 49 unit tests, 261 assertions passed, including 13 concurrency cases.
- [24 live HTTP checks](evidence/gateway-http-checks.json): rejection before forwarding, neighbor access, drain, gzip, disconnect, deadline failure and closure with a paused TCP reader, and pre-header timeout/recovery against a transport that never settles.
- [8 real Supabase overload checks](evidence/gateway-overload-checks.json): eight simultaneous RPCs, ninth rejected, neighbor correctness, drain and recovery.
- [1,000 SDK regression operations](evidence/sdk-overload-regression.json): reads, inserts, identity checks, private uploads and downloads, with zero failures. Temporary fixtures removed and owned runtime stopped.

These are local, short, bounded tests. They do not prove production capacity, fairness among many environments, hostile project-owner isolation or resource containment inside shared PostgreSQL/Storage. Longer open-loop overload and recovery tests remain necessary.

## Service-aware admission

The shared gate keys service counters by environment and service, so changing API keys or recreating a managed handler cannot bypass them. A full REST budget does not itself occupy Auth or a neighboring environment's budget. Response consumption and cancellation retain the same lifetime rules.

Old trusted registries without service budgets retain only environment/process limits. Launch configuration is checked for drift, but effective PostgREST database-based overrides are not audited here. Multiple gateway processes and direct upstream access require separate enforcement. Reducing admitted REST work mitigated the observed queueing symptom; it does not establish the internal scheduling cause or prove SQL cancellation. See [the sustained comparison](SUSTAINED-OVERLOAD.md).
