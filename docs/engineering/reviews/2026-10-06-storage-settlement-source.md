# Storage settlement source preparation

SS1 and SS2 are source preparation requirements. SS3 through SS5, original
native settlement and complete product acceptance remain required and pending.
No consumer effect gains native authority from these files.

The patch adds an exact pinned writer source map, a durable private-disk fixture
journal, strict Python and TypeScript context and inventory checks, and an
original SDK probe source plus a bounded native case plan. The Python protocol
always emits fixture evidence. TypeScript comparisons always return
`nativeAdmitted: false`; the native entry points refuse. No production consumer
imports the new verifier. The existing native-dedicated route remains closed.

The eight scoped additions are listed in the adjacent evidence inventory. Existing
Catalog, HTTP, gateway, lifecycle, backup, restore, installer, native verifier,
private SQL and HBA files were not modified. The verified coordinating baseline
is separate commit `e626baf710886b69138e246b251069841a39862d`; deliver only the later
settlement delta, never that baseline commit.

## Identity and checks

The checkpoint scope hash is
`13db04977316d05d6c74fb20eb5690fb7f36df26144b1b4b68da9737bbffc12c`.
The plan hash is
`2a414dc7dacee28995eb660744455122e21822901ef97b6d63cf2e56f3c92786`.
The public source identity is `sbarbase-public-source-v1`, 543 files,
`5c6e3059ca261ab9edd7fe2829b275d0082ec0d00a4d6d80ae8263ac7744625f`.
Documentation and these evidence copies are outside that algorithm's source
inventory. The scoped file hash and public source hash have different purposes.

Python 3.14.7 ran 71 focused tests, with zero failures, errors or skips. Bun 1.3.14
ran 78 focused tests, with zero failures or skips. Python includes actual private
temporary disk interruption, permissions, symlink/hardlink, locking and restart
checks, a source-only SDK syntax build, and a real Python to TypeScript fixture
receipt roundtrip. Bun tests strict receipt and inventory parsing, detached
immutable inputs and mandatory native denial. These checks establish source
behavior only. No TypeScript compiler typecheck was run.

The final specification review independently passed SS1/SS2, verified the pinned
commit, six Git blobs and 86 source spans, matched 24 cross-language digest cases,
and executed 244 additional control validation checks. The final operational review passed the same preparation scope and ran six
additional adversarial probes. These include actual fixture controller process
exits and lock contention, foreign context refusal, incomplete-log preservation
and twelve cross-language inventory cases. These process probes remain source
fixtures, not original Storage observations. Both reviewers independently refreshed the unchanged source and plan identity,
with no invented behavioral rerun. The checkpoint engine accepted SS1/SS2 at
revision 31. Its run remains active; the native behavior and both required native
review gates are pending. The final export and all engine artifacts are retained.

Public reproduction needs the normal project Python and Bun tools:

```sh
python3 -m unittest discover -s lab -p 'test*storage*settlement*.py' -v
bun test tests/storage-settlement-contract.test.ts
```

The optional JSON wrapper takes an explicit user-owned evidence destination:

```sh
python3 lab/disposable-storage-settlement-drill.py --source-control-check --evidence /absolute/owned/bun.log
```

No hidden workstation path is needed for these commands. Retained original
reports and logs contain the historical workstation paths at which observations
were made; they are provenance, not portable execution prerequisites.

## Retained negative evidence and fixes

The first source checks exposed JavaScript numeric-key ordering and unsupported
fractional inventory numbers. The final codec follows JSON.stringify insertion
order, including integer-index keys, UTF-8 strings and safe integer bounds;
manifest and journal checksums retain their separate sorted canonical codec.

The initial lost-fence-acknowledgement fixture expectation requested replay. The
protocol now recovers the exact live token through observation and never replays
an ambiguously acknowledged fence effect. Consumer effects also persist pending
before execution and refuse automatic retries after an ambiguous outcome.

An intermediate transition regression failed three tests when journal JSON
serialization sorted inventory properties. Serialization now preserves ordered
resource bytes. The durable checksum remains sorted and validates the record.
The SDK syntax probe also initially failed because Bun did not accept the stdin
entrypoint argument used by the probe; it now builds a real private temporary
entrypoint. The first checkpoint Bun wrapper failed its JSON parser because raw
stderr and structured counts shared stdout. The public wrapper now saves raw
output separately and emits only structured actual counts and its log digest.

Both initial independent critics rejected the source protocol. Their actual
reproductions are retained. Python dictionary equality treated `True` and `1.0`
as equal to integer identity fields. Cached mutable observation dictionaries also
hid queue inventory changes during reconciliation. Malformed unhashable launch
and neighbor entries escaped as TypeError. The repaired protocol compares typed
canonical values, retains detached validated observations and reconciliation,
detaches caller receipts before observer calls, and validates entry types before
uniqueness checks. Discriminating regressions reject all reproduced cases.

The second operational reviewer correctly invalidated its own review after a
coordination inventory exposed an earlier verdict. Its completed checks are
retained but contribute no independent passing review. A fresh operational
reviewer received no previous verdict or implementer conversation.

The engine initially refused proof because its current gate conservatively kept
the invalid reviewer alongside the two passing reviewers. Its supported revise
command reinitialized the exact same plan bytes, source scope, requirements and
gates; no criterion was lowered and no historical report or journal event was
edited. Fresh 71-test Python and 78-test Bun checks were executed with new tokens.
Both unexposed reviewers independently refreshed the current source and plan
identity and explicitly distinguish that check from their original executed
reviews. The pre-recovery export and final engine events are retained so the
invalid review and proof refusal remain visible.

The retained initial and intermediate failures are observations against earlier
source bytes. They do not describe the final source. The final evidence inventory
binds every retained artifact by path, bytes, mode and SHA-256; original reports
are copied byte-for-byte. The specification import representation changes only
artifact path representation to satisfy the checkpoint engine schema, while
retaining the original report and its digest.

## Required next work

SS3 needs actual pinned original Storage SDK, file, SQL and process observations
for all enabled writer profiles, interruptions, lost acknowledgements and exact
owned cleanup. Installed-image compiled-source provenance is not established by
a release tag or source map. The prepared SDK probes cover ordinary and signed
upload/download, overwrite and deletion. The remaining case harnesses are not
implemented by the list of 29 required case IDs.

SS4 needs an installed durable authority over ingress, launch, restart and
publication, exact source generation and fence binding, and real transfer,
purge, backup and restore integrations. Revalidate after awaits and within each
final mutation transaction. Backup requires an installation-wide fence and exact
original snapshot-holder/container/database/OID/backend-PID/start/transaction
and owned transport bindings. NativePlacement currently does not identify
Storage process or volume authority.

SS5 needs implemented complete all-tenant shared migration, neighbor and original
configuration/key/bytes/version/xattr/nanosecond-mtime preservation, enabled
queue/Redis/remote-backend quarantine and reconciliation, new working grants and
stale original signed access denial, coherent recovery and full merged runtime
acceptance. S3 protocol exposure is distinct from a remote S3 persistence backend.
The remote provider authority is absent, so that enabled profile refuses pending
its genuine adapter and runtime evidence.

No Docker build/pull/run, database/service/VM start or retained resource operation
was performed. The coordinator has not assigned a bounded native runtime role.
`--execute` refuses before native work. The pending native checkpoint's command
is a planning placeholder; the next phase must implement the real authority and
case runners and revise its source scope and gates before it can be accepted.
