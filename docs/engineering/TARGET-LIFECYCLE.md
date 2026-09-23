# Retained target lifecycle

Run from the repository root:

- `/usr/bin/python3 lab/target_runtime.py up`
- `/usr/bin/python3 lab/target_runtime.py stop`

Both commands hold the installation operation lock. Startup validates the verified target/export relationship, recorded source fence, container ownership, pinned images, network and volume mappings. It persists maintenance before starting services, checks database/Auth/REST/Storage health, reads current addresses, stages them at the expected routing revision and resumes traffic. Failure attempts maintenance and shutdown instead of reopening source routing. Stop pauses routing before stopping every target service and the database, retaining volumes and recording incomplete cleanup honestly. Stop does not require decrypting the backup.

Ten live checks verify gateway availability after startup, refusal after stop, restart replacing an intentionally stale saved address, restoration of the Storage route and source remaining stopped. Three unit cases verify both directions of the staged resource guard and shutdown attempts despite a catalog failure. All 57 Python tests pass; routing operator and lifecycle probe strict types pass.

Standalone command mode is staged: target startup refuses any running source container, and normal source startup refuses running recovery targets. This preserves the current local experiment resource budget. The foreground supervisor now uses installation_runtime.py to admit and run the combined placement, with an explicit 6 GiB/6 CPU ceiling and host reserves. See COMBINED-RUNTIME.md. In-process graceful drain, controller SIGKILL recovery and continuous target health monitoring remain unfinished. Recorded source fencing is trusted local operator state, not protection against a hostile host administrator.
