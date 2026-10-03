# Native placement identity contract

Status: implemented catalog-backed declaration validation and read-only consumers, with controlled verification. Native engine activation and complete original Supabase support remain unaccepted. This contract does not authorize migration or release. It follows [the project goal](../../../PROJECT_GOAL.md) and [the product plan](2026-10-03-product-and-portability-plan.md).

## Persisted authority

The existing catalog `runtime_routing.placement` is the sole placement declaration. `Catalog.runtimeRouting` reads it, and trusted in-process operators stage changes through `Catalog.changeRuntimeRouting` with an expected routing revision. No HTTP placement mutation API or second runtime inventory authority is introduced. Observed endpoints, database names and current Docker identities do not authorize adoption of a replacement engine.

Null placement and existing URL-only placement remain compatible. Their resolver profile is `legacy-shared`, application database is the runtime identifier and native generation is null. No-row routing revision zero means no persisted routing row; it does not establish native generation zero, database OIDs, engine identity or ownership. URL-only declarations retain the existing Auth/REST/optional Storage endpoint override behavior.

## Versioned native declaration

`NativePlacement` version 1 uses profile `native-dedicated`. Validation requires:

| Field | Meaning and constraint |
|---|---|
| runtime | Existing `e_` plus 24 hexadecimal characters; must equal the catalog runtime. |
| ownership | Stable operator-declared ownership reference, preserved across generations. |
| generation | Positive safe integer, independent of routing revision. |
| engine | Engine ID, full container ID, image sha256 digest, full network ID and owned volume reference. |
| application | Name `postgres` and engine-scoped PostgreSQL OID. |
| maintenance | Distinct database name and OID, separate from application. |
| credentials | Distinct application and maintenance credential references. No secret lookup occurs in this slice. |
| declaration | Operator declaration reference; it is not verified runtime evidence. |
| services | Auth/REST and optional Storage endpoint declarations, without URL credentials. |

Unknown versions/profiles, unknown fields, malformed identities and missing maintenance bindings fail validation. All native declarations resolve as `admission: unadmitted`. An optional supplied observation is compared to the entire canonical declaration and cannot modify catalog state. A foreign or stale comparison fails; legacy state requires explicit initialization instead of adopting an observed identity.

## Generation transitions

The expected-revision immediate catalog transaction verifies runtime readiness and transition state before changing the routing row or audit events. Staging is allowed only while maintenance is active.

- Initial native declaration is explicit operator staging at generation 1.
- An identical canonical declaration may be staged at the same generation. Any same-generation change is refused.
- Replacement requires exactly the next generation, with runtime and ownership preserved. Engine, OID and other declared bindings may change only through that explicit transition.
- Backward/skipped generations, foreign runtime/ownership and native-to-legacy downgrade are refused.
- Native resume is always refused by this implementation. A declaration, even with a receipt reference, cannot activate native routing.

These rules validate declared authority; they do not prove that an engine or resource belongs to the operator. Verified adoption, writer fencing, existing-session drain and generation publication require a later native acceptance contract. The current implementation cannot publish a native generation.

## Connected consumer behavior

Application service discovery resolves catalog placement inside the existing authenticated membership-authorized callback. Unavailable or unadmitted placement returns a sanitized 503 without application endpoint resolution. Unauthorized membership remains 403 and receives no placement details.

The managed public gateway uses the same API-key/keyless-route admission predicates as the ordinary gateway before inspecting placement. Missing required keys, invalid keys and keys from another runtime return 401 consistently for ready legacy, declared native and malformed placement states. Supported keyless Auth browser steps, public/signed Storage reads, Functions and preflight requests keep their existing admission rules. Unsupported native routing returns generic unavailable 503 for these keyless requests. A valid publishable key may receive the specific native nonadmission message. Installer endpoint resolution and application transport remain closed for native declarations. Maintenance continues to stop authorized application requests.

## Controlled verification and next bars

The focused tests exercise real Catalog, KeyStore, managed gateway and application handlers with synthetic endpoints and transport spies. They cover legacy behavior, persisted CAS/generation invalidation, unchanged audit events on refusal, distinct maintenance identity, foreign observations, unauthorized discovery, public keyless routes and unauthorized native-profile disclosure. Run the focused tests through Bun:

```bash
bun test tests/native-placement.test.ts tests/placement.test.ts tests/application.test.ts tests/key-http.test.ts tests/hierarchy.test.ts tests/gateway.test.ts tests/functions.test.ts tests/realtime.test.ts tests/drain.test.ts tests/concurrency.test.ts tests/lifecycle.test.ts
```

Controlled passes establish neither native engine behavior nor full source acceptance. The portable source gate remains `sh deploy/verify/run.sh`, followed by independent scoped review. Evidence must match the implemented source.

Mandatory follow-ups remain: untouched version-bound .166 inventory, verified native adoption and original service readiness; developer startup database mapping, session invalidation/drain and PostgreSQL CancelRequest routing to the issuing engine; backup/restore engine/application/maintenance journal identities; default library worker suspension/drain/resume and native interruption evidence; key/secret continuity and pending webhook policy; neighbor isolation, host portability and capacity proof. The official .136 comparison baseline remains separate from actual .166 native facts. Original native capabilities must be implemented and accepted, rather than permanently removed to pass refusal checks.
