# Worker effect ownership

The worker's exclusive flock now survives in its direct provisioning process. `worker.py` exports its actual descriptor; `worker-effect.ts` maps the same open-file description to child FD 3. Before executing any effect, `worker_lock_exec.py` checks that descriptor's device/inode against the expected worker.lock, confirms the lock and retains it while supervising the direct child. Failed inheritance or identity validation prevents the effect from running.

Why: an actual Bun/Python regression showed that killing the worker previously released its lock while its child remained alive. A replacement worker could acquire ownership too early. With the inherited descriptor, replacement is refused until that direct effect exits. This intentionally prioritizes serialization over automatic takeover.

The raw-descriptor mapping follows the [Bun spawn API](https://bun.sh/reference/bun/spawn). Local executable tests verify the installed Bun behavior rather than relying only on documentation.

## Evidence

`lab/test_worker_effect.py` uses temporary files and harmless real subprocesses. The ownership regression failed before the fix and passes afterward. It checks lock refusal after worker SIGKILL, release after effect completion, missing executable cleanup and wrong-lock rejection. At this checkpoint: 68 Python tests; 60 Bun tests, 314 assertions. Strict typing of the worker dependency chain passes. Independent reviewer parent_death_review found no must-fix within this scope.

No extra runtime was allocated or catalog job rewritten. All owned Docker runtimes remained stopped during this test.

## Remaining boundary

This guarantees ownership only while the direct provisioning process remains alive. Killing that process can still leave a Docker CLI or daemon-side operation running after lock release. The later [durable receipt gate](PROVISIONING-RECEIPTS.md) blocks automatic replay and startup after an uncertain outcome. No deadline, group containment, Docker cancellation or complete active-provisioning crash recovery is claimed. Next work must contain local descendants and reconcile actual Docker/database state before retrying; killing a Docker client does not prove a daemon-side effect was rolled back.
