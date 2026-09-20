# Active preflight supervisor crash

Verified locally on 2026-09-20 with `lab/active-preflight-crash-check.py`: 25 checks passed. Evidence: [recorded checks](evidence/active-preflight-crash-checks.json).

## What actually happened

The real combined supervisor started against retained source and target runtimes. The existing failed capacity fixture was explicitly retried. A temporary CPython profile hook paused its actual native provisioner with SIGSTOP immediately after the real fsynced preflight stage writer returned. There are no production fault-injection branches.

The test sent SIGKILL to the supervisor. Parent binding and guardian cleanup released worker, effect and operation ownership. The pending receipt and exact preflight evidence survived without a completion witness. The read-only inspector recommended fresh bounded recovery but did not authorize replay.

A fresh ordinary supervisor, without the hook, committed the exact recovery decision, requeued once, and dispatched one new attempt. Admission refused the attempt with capacity_exceeded. The native refusal witness and catalog outcome matched, and only then was its receipt consumed. Four existing environments and the console responded afterward. All owned runtimes were stopped after the rehearsal.

No container was allocated and the private credential allocation file remained unchanged. No new environment metadata was created.

## Containment and repeatability

Before launch, the test requires idle ownership locks and stopped owned containers. It requires the fixture to be a capacity failure, absent from credential allocations, with all four local allocation slots already retained. Capacity is checked again before explicit retry and recovery startup. It must have remaining automatic-requeue budget.

Cleanup uses a pidfd for the exact stopped native process. Global stop is permitted only after this test demonstrated supervisor ownership and independently acquires supervisor, worker and effect locks, excluding a successor. The runtime stop command takes the operation lock itself. Adversarial review found the missing ownership and pre-allocation checks; both were fixed before running.

This rehearsal consumed one of the fixture's two lifetime automatic-preflight requeues. Do not erase its recovery history to repeat a test. Recheck its remaining budget and state before a future run.

## Precise limits

This demonstrates a complete supervisor crash/restart path while a real provisioning attempt is active at preflight. It does not demonstrate recovery after database mutation, Docker daemon-side uncertainty, interrupted uploads, arbitrary crash timing or host power loss. The test deliberately stops before external provisioning effects and uses a fixture that must be refused after restart.

Stage-aware inspection shows only sanitized status, name and index. It does not settle historical recovery decisions or replace authoritative worker validation. Later-stage effects remain blocked without matching completion evidence.

Next: design isolated disposable fault fixtures for database/service stages and establish safe reconciliation there before claiming general active-provisioning recovery.
