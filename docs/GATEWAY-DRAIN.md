# Gateway pause and drain

`pauseManagedEnvironment(runtime)` gives trusted in-process operator code an exclusive pause lease for the shared default managed gateway admission gate. It is not an HTTP endpoint.

New application requests for that environment receive 503 with Retry-After. Requests already admitted retain their slots through completion, bounded response draining or existing deadlines. Neighboring environments continue. `waitForDrain(timeoutMs)` returns true only while the lease is current and no gateway slots remain. Timeout returns false and leaves admission paused. `resume()` invalidates the lease; an old lease cannot resume a later pause. Resuming early causes pending drain waits to resolve false.

Five new tests cover streaming bodies, neighboring traffic, timeout behavior, early/obsolete leases, failed upstream work and two managed gateway factories sharing a pause. The managed test holds an old-source request, verifies refusal during pause, waits for drain, changes the resolver placement and verifies the next request targets the new endpoint. All 57 Bun tests and 294 assertions pass. Strict TypeScript checking passes for affected source and tests.

This is a cutover building block, not a complete migration controller. State is in memory and disappears on process restart. Custom gates need their own pause. Other gateway processes are not fenced. A gateway deadline can release a slot before an upstream SQL operation ends, so gateway drain is not database quiescence. A future cutover must persist maintenance state, coordinate all request entry points, fence source writes, validate target health and publish placement atomically. Existing runtime routing files have not been switched and retained source/target data has not changed.
