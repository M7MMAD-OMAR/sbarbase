# Independent specification review, v4

Reviewer: `native-source-spec-v4`. Axis: `specification`. Verdict: pass for the frozen source checkpoint. No unresolved source specification finding was identified. This report does not accept original native behavior, installed authority, shared migration, production, or the complete product goal.

## Input and independence

Input: `/home/sbarah/.codex/sbarbase-settlement/20261006/native-source-v2/spec-packet-v4.json`.
Project: `/home/sbarah/.codex/worktrees/09c0/sbarbase`.

I reviewed in a fresh independent agent context without implementer conversation, earlier candidate reviewer reports, verdict artifacts, evidence notes, task inventories, workflow status/export, or journal reads. I made no implementation, profile, or graft changes. Existing graft cards were read for navigation before inspecting the corresponding actual source. The authorized scoped plans were architectural input, while their historical acceptance statements were treated as historical context only.

Rules successfully read: `/home/sbarah/.codex/AGENTS.md`. Its imported `~/AGENTS.md` resolves to `/home/sbarah/AGENTS.md`, which does not exist. I checked applicable ancestor and scoped directories; no additional AGENTS.md file was found there. The supplied user instructions and the available `.codex/AGENTS.md` both require no long dashes in authored material.

Skills and role instructions read:

- `/home/sbarah/.codex/skills/checkpoint-workflow/SKILL.md`
- `/home/sbarah/.codex/skills/checkpoint-workflow/references/roles.md`
- `/home/sbarah/.codex/skills/checkpoint-workflow/references/host-codex.md`
- `/home/sbarah/.codex/skills/checkpoint-workflow/references/cli.md`
- `/home/sbarah/.codex/worktrees/09c0/sbarbase/.agents/skills/graft/SKILL.md`
- `/home/sbarah/.codex/skills/verification-before-completion/SKILL.md`

## Requirement assessment

NS1: `lab/disposable-storage-settlement-drill.py` retains all 29 IDs in `CASES` and produces one matrix entry per ID. Fourteen entries identify actual helper stages: six SDK stages, one large-stream stage, two TUS stages, and five concrete local observer/controller stages. Fifteen entries retain explicit pending implementations. Implementation availability is separate from runtime prerequisites and disabled features. Signed cases require both standard and signed ingress. Lost-stop acknowledgement delivery and installed generation mutation remain explicitly pending stages even though recovery/refusal helpers exist. `runtime-unrun` and structural completeness never become native acceptance. I inspected the helper source and the corresponding tests, including positive and negative private protocol units. These helpers are not 14 completed native cases.

NS2: `lab/storage_native_authority.py` validates exact dedicated local manifests, immutable CIDs, process start ticks and namespaces, effective configuration hashes, original image-config and registry-digest association, owner/runtime/operation labels, exact mount identity, complete observed container inventory and protected neighbor fingerprints. The database has its own mandatory image-config association, effective configuration, start identity and init-process observation. Shared, fixture, remote and ambiguous process shutdown configurations refuse. Image association is explicitly distinct from compiled installed-source provenance.

The private owner ledger requires owned directory mode 0700 and singly linked regular files mode 0600, serializes ownership, checks record hashes and legal transitions, and fsyncs intent before the stop effect. Lost acknowledgement recovery observes the exact stopped set without replaying the command. Direct reconciliation requires a completed observed stop for the exact spec, detached validated stopped observations, and matching observations before and after capture. Direct SQL capture also requires stopped writers and unchanged writer/database observations around the query. These obligations are covered by actual source tests for missing/foreign stop ownership, running writers, changed observations, cached-observer mutation, database identity drift and reversed multi-writer enrollment order.

File capture uses descriptor-relative traversal, rejects symlinks, hardlinks and unlisted mounts, bounds file count and bytes, reads strong hashes, native nanosecond mtime and xattrs, and detects observed file/directory changes. Row reconciliation uses exact database OID, sessions/prepared absence, version-resolved paths, row size and unknown-file classification. Enabled TUS, multipart, queue, Redis and remote barriers remain unresolved. Same-size or same-mtime material does not establish original native completion. Both reconciliation and inventory results explicitly deny native admission. `_installed_launch_guard` refuses before Docker, SQL, stop or original file capture through the authority. The refusal test verifies no subprocess call and no ledger allocation. The Python lifecycle verifier and TypeScript native admission seam also refuse independently of supplied receipt labels or callbacks.

NS3: The frozen bounded controls and three exact checks were inspected and executed. The plan's proposed Runtime integration matches the current `lab/durable_runtime.py` source at `Runtime.launch` lines 378 through 426, `Runtime.start` lines 448 through 490, and `Runtime.activate_services` lines 589 through 650. The concrete proposal covers launch/remove/start/create intent, installation-wide maintenance before shared start, original tenant POST/PATCH, revalidation after external operations and final endpoint publication. Source inspection corroborated the direct launch callers and indirect service activation paths named in the proposal. Existing Runtime code has not installed this new guard. The plan correctly retains installer, systemd, worker, external Catalog publication, backup holders and complete all-tenant migration as further coordinated obligations. This independent report supplies the specification critic only. The separate operational critic and engine proof/acceptance remain coordinator gates and are not asserted here.

SS3, SS4 and SS5: open. No original native cases, installed launch/restart/publication integration, provider/queue runtime phase, shared all-tenant migration, or full merged/runtime/product acceptance was performed or inferred.

I inspected the scoped source/test modules, the common Python source journal and TypeScript receipt codec/tests, dependency pins in `package.json` and `bun.lock`, `lab/storage-image.lock.json`, the builder writer source map, `PROJECT_GOAL.md`, and the full scoped product/storage plans. Their portable public reproduction and native proof boundaries are consistent with the source-only checkpoint. The external supervisor's workstation paths are evidence-role configuration outside the product source, not an installed product dependency.

## Bounded execution

Inspected controls: `/home/sbarah/.codex/sbarbase-settlement/20261006/native-source-v2/profile.json`, `bounded-checks.py`, and `run-python.py` in the same directory.

The supervisor validates frozen byte bindings, locks a cumulative budget ledger, bounds each command to the lesser of 120 seconds and remaining 180 active seconds, caps cumulative captured output at 16 MiB, supervises aggregate owned process RSS at 768 MiB, and kills its owned process group on limit failure and after completion. Python has a 256 MiB soft address-space limit; the pure Python child also has a 256 MiB hard limit. The hybrid child retains an 8 TiB hard ceiling only for its fixed Bun child setup, with the Python soft limit unchanged. Bun has its separately declared 8 TiB address-space ceiling and aggregate RSS supervision. CPU and file-size limits are also applied. Locale, allocator, JIT and bytecode settings are explicit. These are local controls for fixed reviewed source commands, not a security sandbox against malicious descendants or a privileged coordinator.

Executed sequentially, with no additional dynamic probe, network request, Docker/service/VM/native allocation, or vendor service execution:

```text
env LC_ALL=C PYTHONUTF8=1 PYTHONMALLOC=malloc /home/sbarah/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 /home/sbarah/.codex/sbarbase-settlement/20261006/native-source-v2/bounded-checks.py --check python
env LC_ALL=C PYTHONUTF8=1 PYTHONMALLOC=malloc /home/sbarah/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 /home/sbarah/.codex/sbarbase-settlement/20261006/native-source-v2/bounded-checks.py --check hybrid
env LC_ALL=C PYTHONUTF8=1 PYTHONMALLOC=malloc /home/sbarah/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 /home/sbarah/.codex/sbarbase-settlement/20261006/native-source-v2/bounded-checks.py --check bun
```

| Check | Actual total | Failed | Skipped | Peak process-group RSS | Raw log | Raw SHA256 |
|---|---:|---:|---:|---:|---|---|
| python | 144 | 0 | 0 | 28917760 bytes | `check-python-32.log` | `795357ac3b79de9706bb5cf1ced801dd82438616ea9a8f3a336fcaf7f4d37ea0` |
| hybrid | 3 | 0 | 0 | 117940224 bytes | `check-hybrid-33.log` | `7fabbee16be4909acb0120c663024ada99f4b1a5b4c26f4533a97882e874abe4` |
| bun | 78 | 0 | 0 | 63586304 bytes | `check-bun-34.log` | `b688170549a36a65517c8e9d1c71560f162e64758dd837eede9b6ffca25d5856` |

Every command returned exit zero, `reason: null`, and `native_accepted: false`. After my final command the shared budget reported 6.699897596005366 cumulative active seconds and 362215 cumulative output bytes. Those shared counters include earlier executions and are not a claimed duration/output for this reviewer alone. The 3 hybrid tests include syntax-only SDK build, strict cross-language receipt comparison and 10 private mock protocol helper units. None supplies an original native observation.

## Final identity verification

Ran the checkpoint host doctor and then the permitted verdict-free command immediately before reporting:

```text
python3 /home/sbarah/.codex/skills/checkpoint-workflow/scripts/hosts.py review-state --run /home/sbarah/.codex/sbarbase-settlement/20261006/native-source-v2/run --checkpoint source
```

It returned revision 46, project `/home/sbarah/.codex/worktrees/09c0/sbarbase`, plan hash `9af5054961109ad1f0cc09655b44d97929bb7c33093d071dcf9569aaec552d03`, and source hash `2534de8a2ca99f276e0d5bac035f9c312c010c4d22fcc033974987564e80d98c`. Both hashes exactly match the input packet. The doctor establishes local prerequisites only; its declared capability fields are not evidence of reviewer identity authentication. This source pass preserves all native and product acceptance limits above.
