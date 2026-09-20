# Provisioning effect guardian

Worker effects now run through a parent-bound guardian. The guardian installs flag-only signal handlers before publishing a receipt or spawning a command, then creates its own session. The effect runs in another owned session. Worker SIGKILL triggers the guardian's parent-death signal; normal worker shutdown explicitly signals the active guardian.

The local effect deadline defaults to 180 seconds and cannot be increased through the helper. Shorter deadlines are supported for bounded fixtures. On stop or deadline, the guardian sends TERM and then KILL to the owned child group through the existing non-reaping cleanup helper. The leader's PID remains reserved until group signalling is complete. The guardian retains the worker lock throughout and waits for the direct leader. The separate guardian session prevents worker-group escalation from interrupting that cleanup.

A stop during process creation still cleans the returned child. Timeout or interruption leaves the durable receipt pending even if the child handles termination and returns zero. Ordinary successful completion and classified admission refusal are published only after cleanup and only without an observed stop or expired deadline.

## Verified scope

- A real Bun worker is killed after its Python effect becomes ready. Cancellation begins, the flock remains held while the effect's termination handler runs, and the receipt remains pending afterward. This test failed before the change.
- Deadline and successful-leader fixtures include a grandchild that ignores TERM. Tests observe both recorded child identities no longer running after cleanup; no unrelated process is signalled.
- A deterministic stop injected during Popen verifies cleanup of the returned child and no successful receipt publication.
- All 74 Python tests, 65 Bun tests with 354 assertions and strict worker types pass. The real combined supervisor's 11 known-refusal receipt checks pass again. No extra runtime was allocated; all owned runtimes stopped afterward.
- Independent reviewer parent_death_review found no must-fix in this scope.

## Limits and next step

This is a local process-group termination protocol, not Docker rollback. Cleanup signals the group but waits only for its direct leader; the implementation does not generally reap or prove disappearance of every grandchild. Processes that escape the group, uninterruptible kernel work, guardian SIGKILL, daemon failure and power loss are outside this containment guarantee. Kernel waits can exceed the requested cancellation interval, in which case ownership is intentionally retained.

Pending receipts continue to block replay and normal startup. Next implement explicit inspection and reconciliation of the actual Docker/database outcome before resolving such receipts. Do not remove a receipt merely because local processes stopped.
