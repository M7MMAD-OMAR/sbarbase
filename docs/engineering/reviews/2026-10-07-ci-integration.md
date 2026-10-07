# CI integration checkpoint, 2026-10-07

CI startup and restart now use the same Linux Docker admission route as operator deployment. Live management probes use genuine native MFA sessions through a private run descriptor. Ordinary probes reuse a verified session to respect production authentication limits. Bootstrap retains a separate native identity and private descriptor. Offsite cleanup binds exact run names, container IDs, owner labels and immutable image IDs, with bounded graceful cleanup.

The combined workflow and probe changes passed an independent source review. Control type checks include both new TypeScript authentication helpers. The stale catalog checksum in the capability registry was refreshed without changing any of the 105 requirements or their unproven acceptance states.

Current verification passed 914 Bun tests and 2074 Python tests, with no failures or skips, plus control and UI type checks, UI build and CI shell syntax. The Python suite used an unreachable Docker endpoint. Results and relevant source hashes are in [the unit-check record](../../evidence/ci-integration-unit-checks-2026-10-07.json).

One earlier full Python run had an intermittent password-witness fixture error. It did not recur in 1900 focused repetitions or the final full run. Its cause remains unconfirmed, and the file-binding protection was preserved. Earlier failed observations remain recorded rather than being treated as successful runs.

These results establish unit, type and build checks. Current GitHub CI, original native services, Cron, a complete restore and isolated fresh OS deployment still require actual integration evidence. The user's own Linux computer is the intended isolated server test host; the product has no dependency on a personal workstation path. Production readiness remains unproven.
