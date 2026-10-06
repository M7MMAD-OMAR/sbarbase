# Capability contract, version 1

This registry is the current capability contract for SB-01. Run the standard
library CLI from a public checkout with Python 3.9 or newer:

```sh
python3 deploy/verify/capability_registry.py validate
python3 deploy/verify/capability_registry.py status
python3 deploy/verify/capability_registry.py identity
python3 deploy/verify/capability_registry.py accept --capability SB-13a --placement native-dedicated
python3 deploy/check_plan.py
```

`validate` checks the registry, schema, source inventory, bindings and every
referenced proof. Honest unproven entries pass structural validation. `status`
returns the same derived rows as JSON, with independent implementation,
acceptance and production fields. Invalid supplied proof appears in `errors`;
missing proof leaves an unproven status. `accept` additionally refuses missing
current acceptance proof for the selected scope. Without a selection it requires
all capabilities and placements. That broad acceptance command intentionally
fails today. Production requires separate production proof and valid acceptance.

`--root` selects a relocated checkout for every command. No command starts
containers, downloads dependencies, executes the declared test commands or
contacts a service. These commands verify submitted evidence. They do not
perform its runtime tests.

## Current limits

All acceptance and production rows are unproven. Implementation states record
source availability, not runtime readiness. Existing legacy service integration
is partial at the complete enable/use/deny/restore/upgrade contract scope.
Individual advanced Auth capabilities, pooling, resumable uploads, PITR,
replicas and managed offerings require separate execution contracts. Empty
runner or command fields mean the execution contract remains to be frozen and
cannot be accepted. Existing unit runners are contextual source pointers,
never runtime acceptance. Commands must match the frozen argv exactly.

The upstream baseline is Supabase `self-hosted/v0.8.2`, resolved to
`564eab8ad7840b13324f68b1bfac074ef8d51c21`. The existing source record pins the
upstream Compose checksum. Its incomplete reference bundle and image resolution
remain open. Local lock files bind the actual candidate inputs, including
legacy `postgres:17-alpine`; they do not imply the candidate uses the original
Supabase PostgreSQL image or establish equality with the reference bundle.
A complete runtime comparison must freeze effective configurations, image
resolution and fixtures in its concrete execution contract before issuing proof.
The actual `supabase-v0.8.2.bundle.json` is separately checksummed by the
`reference-bundle` binding and included in source identity. It retains unresolved
image metadata. A foundation acceptance proof must also pass the existing
offline `reference_bundle.packet_errors` checks; claiming passed observations
cannot overrule an incomplete or inconsistent reference packet.

The native Cron/HTTP effects fragment has a separate capability and points into
the historical ledger. SB-13a also requires original startup, native identities,
SDK flow and fenced recovery. The fragment cannot satisfy those requirements.
The registry references ledger pointers without copying their statuses. Earlier
accepted fragments remain context until their complete proof is revalidated
against current source and exact scope. Neither historical acceptance nor a
nonempty ledger evidence string establishes current acceptance.

## Portable source identity

`identity` hashes a sorted inventory of repository-relative paths, SHA-256 file
bytes, byte lengths and numeric permission modes. Its fixed boundary includes
all files under `src`, `lab`, `deploy`, `ui`, `tests` and `.github`, plus the
explicit root configuration, version, dependency, license, goal, planning,
ledger and upstream source inputs declared in the verifier. Untracked files
inside that boundary are included. Symlinks are refused. Python bytecode and
`__pycache__` are excluded; they are generated artifacts, not executable source.
This is a portable scoped source identity, not a Git commit identity or a hash
of unrelated website/media working files. It includes the verifier, both
schemas, complete registry, required test inventory and configuration locks.
Copies with equal relative paths, bytes and modes have equal identities.

Only `deploy/capabilities/proof/` is detached from the source digest. Evidence
references and claim states in the registry still participate in the digest.
First freeze the registry with its final proof paths and test commands, then
compute source identity and write the detached payload and logs. Updating any
registry field, source file, required inventory, schema, fixture or lock
invalidates earlier proof. File additions, deletions and mode changes within the
source boundary also invalidate it. Canonical JSON uses sorted keys, compact
separators and ASCII escaping; file bytes themselves are never normalized.

Bindings checksum the actual files. `catalog-schema` includes catalog schema
version 3 and `placement-config` includes the placement validation contract.
Host configuration is executable validation in `lab/docker_profile.py`, not an
invented standalone configuration schema. Version 1 registry and evidence JSON
schemas are closed contracts; unknown fields, duplicate JSON keys and unsupported
versions are refused. Changing a binding requires intentional registry review
and fresh evidence.

## Detached execution proof

A proof conforms to `evidence.schema.json`. It binds schema version, exact
capability/placement/gate/contract, host profile, current source identity,
canonical registry SHA-256, canonical bindings SHA-256 and the pinned reference.
It has UTC timestamps with explicit timezone, a past observation time, a future
expiry and a validity window no longer than 30 days. Production has its own gate.

The proof test inventory must equal the complete required inventory for its
gate, with no duplicates, omissions or extra tests. Each result matches its
required kind, contract, fixture/config binding IDs and full command argv.
Every case requires exit zero, at least one assertion and zero failures, errors
and skips. Unit evidence cannot accept a runtime capability. `passed: true`
alone is neither a contract nor accepted proof.

Every test references distinct nonempty raw logs under the detached proof
directory, each with SHA-256. One referenced log is a JSON raw observation. Its
contents must exactly equal the test's `id`, `kind`, `contract`, `fixtures`,
`outcome`, `exit_code`, `assertions`, `failures`, `errors`, `skipped` and `command`,
plus an `execution` object containing the proof's `schema_version`, `scope`,
`source`, `registry_sha256`, `bindings_sha256`, `reference`, `host_profile`,
`observed_at` and `expires_at`. Both the observation and its execution identity
use closed definitions in the evidence schema. All execution fields must match
the envelope exactly. An unchanged old observation cannot be attached to new
source, configuration, reference or host identity, or renewed timestamps by
editing only the envelope. The `observation` reference must be one of the
checksummed `logs`. Retain additional nonempty stdout or diagnostic streams in
`logs` to make actual outcomes reviewable. No skipped required case is waived.
Absolute paths, traversal, symlinks, unknown placements, missing files and
mismatched or tampered observations are refused.

This is integrity verification of self-attested execution records. A local
actor with write access can fabricate both payloads and logs, alter source and
issue a matching identity. Checksums do not prove execution, reviewer
independence or provenance. Promotion still requires the separate fresh critic
and actual observation process in the execution method. No cryptographic
anti-forgery or production trust claim is made by this verifier.

`deploy/check_plan.py` validates this contract and requires valid current
capability coverage before any ledger slice marked passed can pass planning
integrity. Open slices remain allowed. Coverage is taken from each capability's
`gauntlet_slices`; both local placements require acceptance. G0 additionally
requires the complete foundation/reference/distribution capability, including
immutable clean-clone Linux builds, private-dependency refusal, full reference
image packet, consistent identities, missing-check refusal and reproducible
distribution. Governance-only SB-01 proof cannot close G0.

G12 requires the separate public-release capability: complete claimed-profile
acceptance, current claimed slices, public pilot, seven-day soak, licenses,
contributor setup, vulnerability reporting and support windows. It also requires
current acceptance for every declared local slice capability. SB-03 security
applicability alone cannot close G12. Mandatory G0/G12 capability mappings and
complete test inventories are enforced by the verifier; callers cannot omit
them. Their execution runners remain unimplemented, so both full slices stay
unproven. The complete goal remains open while any required slice is open.

Focused verifier and planning regressions:

```sh
python3 -m unittest lab.test_capability_registry lab.test_plan_acceptance
```
