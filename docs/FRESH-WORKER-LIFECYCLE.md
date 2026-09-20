# Fresh worker lifecycle after SQL fencing

Run `/usr/bin/python3 lab/fresh-worker-check.py` from the project root. This is a bounded, single-environment integration test, not a capacity benchmark or production certification.

## What runs

A fresh private source snapshot starts pinned original Supabase PostgreSQL, management Auth and shared Storage. A real catalog queues one environment. The actual worker wrapper, guardian, inherited ownership descriptors, durable receipt, native provisioning entry point and settlement logic run without mocked ownership or admission. Native SQL is guarded and both control and target tokens are revoked before services.

The probe verifies the exact successful claim from provision_effect_results, its native witness, publication stage and consumed receipt. It then uses Supabase SDK through a loopback gateway for signup, an RLS-protected insert/read and a private Storage upload/download. A mismatched-owner insert is denied, a second authenticated user sees no owner row and cannot download the file, and the owner can still download afterward.

The initial verification incorrectly read claim from the successful job, which clears that field. The probe now uses the durable result record. This was a fixture assertion bug; the worker had completed successfully.

## Isolation and cleanup

The snapshot lives under ignored `.lab/` in a new mode-0700 directory. Only tracked regular source/manifests are copied. No retained runtime state or credentials are imported. The installed node_modules tree is linked read-only by convention; the test does not install or build dependencies.

Only literal Docker owner/name constants are replaced in the snapshot's durable runtime, resource admission and pressure admission modules. Exact replacement counts and transitive worker helper checks guard against retained namespace references. Production source execution logic is unchanged. The test leaves private diagnostic state for inspection; it is not part of the handoff ZIP.

The measured fixture has five containers with combined configured ceilings of 2304 MiB and 2.25 CPUs. It requires at least 6 GiB available host memory before launch, preserves native admission checks, uses an internal Docker network and publishes no container ports. Only the SDK gateway temporarily listens on a dynamically selected loopback port.

Owned child groups are terminated before reaping. Before resource teardown, fresh snapshot worker/effect/operation locks exclude detached guardians. If ownership remains busy, teardown refuses and preserves private resources for diagnosis. Cleanup verifies unique owner/name and captured IDs, removes only this fixture's containers, volumes and network, then verifies absence. Existing source and recovery targets are not used.

## Evidence and remaining work

[57 live checks](evidence/fresh-worker-checks.json) pass, including actual SDK isolation checks and complete Docker cleanup. Strict TypeScript checking of the SDK probe passes. The prior full Python checkpoint remains 115 tests; the unchanged recorded Bun checkpoint is 73 tests/408 assertions. Independent review found no remaining must-fix in the fixture scope.

This closes the fresh worker-driven service integration gate for the new SQL guard. It does not cover interrupted HBA writes, interrupted service migrations, daemon/power failure, multi-host movement or 10/100-project capacity. Unknown later-stage outcomes still block replay. Next define bounded recovery for shared configuration and service effects, preserving this successful lifecycle as a regression.


Latest regression: all 57 checks passed after removing the duplicate startup HBA write following `management()`. The full Python suite now passes 203 tests. The separate HBA authority prototype has live completion evidence but is still not used by this worker lifecycle; these results must not be described as its runtime integration.


## Source HBA integration and parent-bound restart

The current probe passes 76 checks. It additionally validates real guardian hbaProtocol1, exact startup/worker HBA archives and claim binding, registry tombstones, missing-pin refusal without new outcomes, same-generation restart through dev.run_stage with an inherited supervisor worker descriptor, and three archived operations after restart. SDK checks run after restart. The snapshot now includes all source HBA helpers and verifies they do not target retained resource names. Full suites: 217 Python and 73 Bun tests/408 assertions. See [integration limits](SOURCE-HBA-INTEGRATION.md); actual worker interruption and legacy adoption remain open.
