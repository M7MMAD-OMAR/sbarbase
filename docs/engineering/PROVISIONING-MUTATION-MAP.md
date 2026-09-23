# Provisioning mutation map and SQL integration gate

Source inspection: 2026-09-20, `lab/durable_runtime.py`, `lab/run.py`, `lab/provision.py`, `lab/effect_receipt.py`. This records the current code, not a completed integration.

| Boundary | Actual writes | Required authority before recovery |
|---|---|---|
| Credential reservation | Private runtime JSON, before database stage | Exact retained claim and credential identity; do not regenerate on retry |
| Control database | Auth/REST/Storage roles, database creation, CONNECT grants, membership, reopening, connection limits, REST timeouts and Auth search path | Exact token guard on every native batch, including helper calls |
| Target database | Auth/public permissions, extensions, Storage schema and default privileges | Target-generation-bound registration followed by same-backend guard |
| HBA configuration | Validated complete-file rename of shared pg_hba.conf, then SQL reload | Separate configuration protocol; SQL revocation cannot stop delayed file writes |
| Auth and REST startup | Docker creation/start and Auth migrations on independent connections | Service admission and cancellation protocol; readiness alone is not fencing |
| Storage registration | Tenant HTTP POST, shared storage_metadata and target initialization | Shared-service protocol with tenant identity; two-database revocation does not cover it |
| Publication | Atomic endpoint JSON replacement, then native completion witness | Exact operation identity and durable publication reconciliation |
| Routine startup | Published environments call provision without worker token | Explicit resume path; never silently bypass new provisioning guards |

## Material blockers found by independent review

1. A target created closed cannot accept target-local registration. Reopening, registration and dispatch require a specified sequence before any service launches.
2. An absent-target revocation creates no target tombstone. A newer claim can create that database, after which a delayed old lazy registration can establish old authority there. Drop/recreate has the same generation problem. The disposable pair probe deliberately reproduces this counterexample. The new register_target path now obtains a binding through the active control guard and pins it before target bootstrap. Live tests reject stale registration across replacement. The unpinned primitive remains unsafe and must not be used for target admission.
3. HBA writing now follows the durable services marker. A marker failure prevents shared-file writes. An acknowledged versioned writer or independent recovery protocol is still needed for those effects.
4. Guard helpers previously returned lock results unless the lock functions run through DO/PERFORM. This polluted exact scalar query results. A live failing regression reproduced it; the prototype now suppresses guard-generated rows. Production psql must also suppress command tags with quiet mode before a transparent executor can be adopted.

## Next implementation sequence

The experimental generation-bound registration now passes real absent-target and drop/recreate cases. Next build an explicitly scoped executor preserving query results and pinned backend identity, inventory every caller, and separate resume from creation. Finally handle HBA and service-driven effects before any database-stage replay. Do not clear unknown receipts on the strength of either existing SQL prototype.

No retained runtime mutation was performed for this source audit. Independent review identified the HBA boundary, service migration bypasses and new-generation resurrection risk. The generation race is addressed only by the new isolated admission path; runtime integration, HBA and service effects remain open.
