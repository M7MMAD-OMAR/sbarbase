# Independent specification review of frozen native source v3

Reviewer: native-source-spec-v3. Axis: specification. Actual verdict: fail.

This review used spec-packet-v3.json as the input contract. It inspected actual scoped Python and TypeScript source, their tests, the image lock, package/SDK dependency bindings, the scoped writer source map, PROJECT_GOAL.md, the full scoped product plan and the storage settlement plan. It inspected the actual Runtime launch, shared start and endpoint publication spans for the read-only integration proposal. Historical plan statements were treated as context, not current architectural acceptance.

The applicable imported .codex/AGENTS.md was read. /home/sbarah/AGENTS.md and the checkout AGENTS.md were absent. The checkpoint-workflow skill, roles schema, Codex adapter and relevant CLI schema were read. Graft was used first by reading its scoped source cards. Its CLI was avoided because the supplied skill says queries refresh the graph, which was forbidden for this role. No graph refresh or implementation/profile edit occurred. No prior candidate reviewer reports, verdict artifacts, workflow status/export or journals were consulted.

## Frozen identity

Immediately before reporting, the authorized hosts.py review-state command returned revision 36, plan hash 9af5054961109ad1f0cc09655b44d97929bb7c33093d071dcf9569aaec552d03 and source hash 6ecf6f0539a4865fbd8db515d6facabc4e00c7befbfa70c48f37adcfc67876f5. Both hashes match spec-packet-v3.json exactly.

## Actual bounded execution

Only the three exact commands in profile.json were executed as tests, sequentially through bounded-checks.py. No additional dynamic probe was run.

| Phase | Executed | Failed | Skipped | Actual raw log | Raw SHA256 | Peak sampled group RSS |
|---|---:|---:|---:|---|---|---:|
| Python | 142 | 0 | 0 | check-python-22.log | 40f6c106c2a3d18f6a7717bd41645fc4096bc3ae8ca5b2b895ff75d0a481f9d9 | 29143040 bytes |
| Hybrid | 3 | 0 | 0 | check-hybrid-23.log | 645922ab3b773d6fd75cc7121499de55a82cad861aa71fc97cce05e51e8fee88 | 98435072 bytes |
| Bun | 78 | 0 | 0 | check-bun-24.log | 3f979164a4a417914efa1012aed9f0230bafab941ed47119d7148da26dc07985 | 63713280 bytes |

All three commands returned zero and reported native_accepted false. The shared cumulative ledger reported 4.360805029009834 active seconds and 228372 output bytes after this review's final check. These are shared cumulative values, not a claim that this reviewer ran every earlier check in that ledger. Only the three logs named above are this review's actual test results.

The profile, bounded-checks.py and run-python.py were inspected before execution. The supervisor freezes seven source/helper SHA256 bindings, locks the cumulative budget, caps each command at 120 seconds within the remaining shared 180-second allowance, and caps combined captured output at 16 MiB. Python's soft address-space limit is 256 MiB; pure Python children also receive that hard limit. Hybrid Python reserves an 8 TiB hard limit so its Bun-only child wrapper can set Bun's separate 8 TiB address-space limit before exec. The supervisor samples the owned process group's RSS against 768 MiB, applies CPU/file-size bounds, and kills that owned process group after completion or a bound violation. This is a reviewed source-check supervisor, not an OS security sandbox: RSS monitoring is sampled and process-group ownership does not prevent deliberately escaping descendants. No source test inspected here launches such a descendant or original service. This review observed no bound failure.

## Requirement assessment

NS1 is not met because of NS1-SIGNED-PREREQUISITE in the structured report. The matrix retains all 29 IDs and correctly distinguishes 14 helpers from 15 pending implementations, with explicit helper scope and missing installed authority. Lost-stop acknowledgement and changed generation retain their unimplemented fault-injection stages. Every row keeps native acceptance false. However, both signed SDK cases omit their signed-enabled prerequisite and disregard disabled_features=['signed']. The finding follows directly from the source branches; no unauthorized reproduction was executed. Passing existing tests does not cover this missing case.

NS2 source obligations were substantively inspected. validate_spec and decode_inventory bind source/image/config identities, exact writer CIDs, process start ticks/namespaces, mount/volume/resource identity, complete daemon owner inventory and protected neighbors. The mandatory original database incarnation includes config/image association and process/start/namespace identity. OwnerLedger persists fsynced stop intent before the command, retains ambiguous outcomes, and prevents automatic stop replay. LocalNativeAuthority.reconcile holds the ledger lock, requires completed exact observed-stop ownership, detaches validated stopped observations, rejects a changed observation before return, and requires stable writer/database observation across file and SQL capture. The filesystem parser reads exact bytes, xattrs and nanosecond mtime without rewriting native metadata. The row/file comparison checks OID, sessions, prepared transactions, reachable version paths and lengths, and retains unknown files plus enabled unresolved effect barriers. Current command and authority entry points call the absent installed guard before subprocess or original file/SQL effects. The inspected tests cover running-writer rejection, changed stopped observations, foreign ownership, detached cached observations and database identity drift. These are source fixtures, never proof of original native closure or recovery.

NS3's source proposal and frozen check controls are concrete. The integration proposal corresponds to the actual shared Runtime launch/start/activate_services code: launch can start or replace containers and create volumes, start launches shared multi-tenant Storage, and activate_services mutates tenant configuration and publishes endpoints. The proposal requires before-effect installed guards, exact reobservation and final publication authority, preserves all-tenant and neighbor obligations, and expressly leaves external restart/Catalog/consumer paths open. This role supplies one fresh independent specification critic only; it cannot attest that the other required critic has completed. Final two-critic gate evaluation belongs to the coordinator.

SS3, SS4 and SS5 remain open. No Docker command, service, database server, VM, network request, provider operation, installed launch/publication authority, actual native SDK observation, native interruption/cleanup, consumer integration, all-tenant migration or full product acceptance was performed or granted. Source comparisons and forged native labels still cannot mint admission.

The review is valid for the frozen hashes above and has one unresolved major specification finding. The fail verdict concerns source acceptance of NS1, not an observed native service failure.
