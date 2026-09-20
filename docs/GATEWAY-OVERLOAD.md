# Application gateway overload and streaming

The local gateway admits at most 8 requests per environment and 32 application requests across one process, without a queue. Excess work receives 429 (environment) or 503 (aggregate) with Retry-After. Keys and service routes share the environment budget. Invalid requests fail before allocation. Management authentication has a separate budget; these limits do not cover every process or socket.

Slots remain occupied through response streaming and release once on completion, error, cancellation or the 30-second response deadline. Request bodies retain their size and time limits. Native upstream fetch has a timeout; arbitrary injected transports must cooperate with cancellation. This is not a distributed rate limiter or DDoS protection.

The loopback HTTP adapter explicitly observes stream failures, respects backpressure and destroys the response connection on a stream error. A local Bun 1.3.14 reproduction motivated this adapter: partial streamed responses through the previous path could appear successful at deadline. Compression is forwarded without implicit decompression so headers match wire bytes. See [Bun fetch options](https://bun.com/reference/globals/BunFetchRequestInit).

## Evidence

- 8 supervisor smoke checks passed after the adapter change, including idle worker restart and graceful shutdown.
- 44 unit tests, 239 assertions passed, including 8 concurrency cases.
- [22 live HTTP checks](evidence/gateway-http-checks.json): rejection before forwarding, neighbor access, drain, gzip, disconnect, deadline failure and closure with a paused TCP reader.
- [8 real Supabase overload checks](evidence/gateway-overload-checks.json): eight simultaneous RPCs, ninth rejected, neighbor correctness, drain and recovery.
- [1,000 SDK regression operations](evidence/sdk-overload-regression.json): reads, inserts, identity checks, private uploads and downloads, with zero failures. Temporary fixtures removed and owned runtime stopped.

These are local, short, bounded tests. They do not prove production capacity, fairness among many environments, hostile project-owner isolation or resource containment inside shared PostgreSQL/Storage. Longer open-loop overload and recovery tests remain necessary.
