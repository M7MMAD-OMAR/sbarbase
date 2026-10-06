# SB-01 capability registry verification

Date: 2026-10-06. Scope: executable source and evidence integrity only.
Baseline import: `022880dbff87cbd9da3f0ccfa547689f53072bcc`.

The [registry](../../../deploy/capabilities/registry.json) and
[CLI contract](../../../deploy/capabilities/README.md) contain 35 capabilities
and 105 placement rows. Implementation, acceptance and production are separate.
All submitted acceptance and production states remain unproven. The existing
`gauntlet-ledger.json` remains the goal authority; its historical fragments are
context rather than fresh proof.

## Observed verification

The focused command completed 37 tests with zero failures, errors or skips.
Registry and planning validation exited zero. The independent fresh-context
critic executed the focused suite and 34 actual CLI observations with expected
outcomes. Its verdict was `SPEC MET` for this source-only contract. Artifact
identity was visible; this was specification verification, not a blinded
comparison. Commands ran in isolated user scopes with MemoryMax 256 MiB and
CPUQuota 25%. No Docker, databases, network or full suite ran.

[Raw focused output](../../evidence/sb01-20261006/focused-tests.log) SHA-256:
`8bde2654b9d3fe0dcd51a4a2f4d04989c822a065fc0c083a90f5f8f32c1f8cd0`.

Portable source identity, `sbarbase-public-source-v1`, 506 files:
`1b447fe3ad525087ba8ac79836757d8e1fd46401493bf0bff0e68b80c03337e3`.

The fixed source inventory includes implementation, tests, schema, registry,
image locks, configuration, goal/plan, existing ledger and actual upstream
source/bundle records. It excludes this report and development evidence.
Copied public source with equal paths, bytes and modes must yield the same
identity. Any relevant edit invalidates its earlier proof.

## Preserved losses and repairs

The original baseline accepted thirteen passed goal slices with nonexistent,
nonempty evidence references. The planning verifier now requires current
complete capability coverage instead of trusting those strings.

The first critic returned `SPEC NOT MET`. Actual CLI counterexamples accepted
unchanged old logs after changing only the envelope source identity or renewing
expired timestamps. Governance-only proof also incorrectly closed G0. Those
results remain retained in development evidence. Repairs bind the complete
execution identity and timestamps inside each checksummed raw observation.
The closed schema and verifier compare that record with the outer proof.

G0 now separately requires complete foundation/reference/distribution cases.
G12 requires claimed-profile acceptance, current claimed slices, pilot, seven-day
soak, licenses, contributor setup, vulnerability reporting and support windows.
Mandatory mappings and test inventories cannot be omitted. Foundation proof
also passes the existing offline reference packet validator. Current unresolved
image metadata cannot be overridden by a successful observation record.

SB-13a requires original startup, native identity, Cron/HTTP effects, SDK flow and
fenced recovery independently. The separate native Cron fragment cannot accept
that complete capability. Unit evidence cannot accept a runtime contract.
Production has its own proof and cannot inherit ordinary acceptance.

## Reproduce from public source

Python 3.9 or newer and the standard library suffice for these source checks:

```sh
python3 -m unittest lab.test_capability_registry lab.test_plan_acceptance
python3 deploy/verify/capability_registry.py validate
python3 deploy/verify/capability_registry.py status
python3 deploy/verify/capability_registry.py identity
python3 deploy/check_plan.py
python3 deploy/verify/capability_registry.py accept --capability SB-13a --placement native-dedicated
```

The last command must exit 1 for the supplied unproven registry. Structural
validation must exit zero with all 105 acceptance/production rows unproven.
A clean public source export without Git metadata was also checked for matching
source identity, successful structural validation and this acceptance refusal.
No development exchange directory is a prerequisite.

## Limits

The upstream baseline is Supabase `self-hosted/v0.8.2`, commit
`564eab8ad7840b13324f68b1bfac074ef8d51c21`. Its actual reference packet remains
`metadata-collected-with-unresolved-proof`. Runtime commands remain unfrozen
where their execution contract is unimplemented. G0, G12, complete native
placement, SDK behavior, recovery, capacity and production remain unaccepted.
Earlier full-suite evidence was not copied or rebound to this source.

The verifier checks self-attested execution record consistency. An actor with
full write access can fabricate new matching records; checksums do not prove
execution provenance or reviewer independence. Actual acceptance still needs
the declared execution and independent review process. Integration with another
source revision requires deliberate binding updates and fresh verification.
