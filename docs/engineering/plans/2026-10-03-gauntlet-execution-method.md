# Linux Docker platform execution and Gauntlet method

Date: 2026-10-03. Status: proposed execution contract, not runtime acceptance.
Assessed checkout: `b88e2f1343e16a0210e3e4bcbd3429e3e8c563c3`, with planning documents uncommitted. No runtime, benchmark, clean-host installation or recovery success is claimed by this document.

This method extends the [product plan](2026-10-03-product-and-portability-plan.md). Where that plan proposes operating-system-specific runtime adapters, this contract supersedes that proposal: the product and its operational tools run in Linux containers. Linux Docker Engine is the production target. Windows and macOS may run the same Linux images through a compatible Docker environment after independent evaluation. They do not get native runtime implementations. Containerization is a packaging choice, not proof of support for every kernel, architecture or filesystem.

## 1. Product invariants and completion scope

The complete goal remains an efficient, customizable open-source Supabase operations platform: original Supabase services, understandable installation and administration, application and developer-workflow compatibility, complete recovery, safe updates, security, measured capacity, dedicated placement, eventual multiple servers and tested availability. Finishing this document or one slice does not finish that platform goal.

Distributed runtime, build instructions and examples must work from a public clean clone. No dependency may resolve to a maintainer's home directory, another local project, private desktop service, local skill, local context graph or untracked helper. Development agents may use personal tooling to investigate; distributed artifacts and acceptance may not require it. Public upstream repositories, documented service credentials supplied by the operator and portable repository-relative fixtures are allowed. Dependency URLs, image digests, licenses and build inputs must be inspectable. Do not introduce a hosted control plane as a mandatory condition for local operation.

Every runtime component, including orchestration, diagnostics, migration and recovery helpers, belongs in the Linux container release. Host responsibilities are documented Docker/Compose, supported persistent storage, network access and operator-provided configuration. Any host privilege, socket access, cgroup inspection or host mount is an explicit capability requirement, checked before mutation. Arbitrary checkout locations and Docker data roots cannot be assumed to match the author's machine. A standard Linux path can be a documented default only if validated and configurable where needed.

Maintain Supabase application contracts before claiming cloud-like workflow. Self-hosted functionality, Sbarbase orchestration and cloud-managed services have separate capability statuses. Complete customization means versioned, validated, exportable settings with safe upgrade behavior; it does not permit bypassing tenant isolation or publishing service credentials.

## 2. Phase 0: freeze a real comparison bar

Three candidates were considered: upstream Supabase Compose for service contracts, Pigsty's Supabase operations documentation for database recovery ideas, and a Sbarbase multi-project specification for behavior that upstream single-project Compose does not implement. Use the upstream release as the primary real bar; use a predeclared spec for novel behavior. Pigsty is only an operations supplement, not a required runtime dependency or a container portability baseline.

The primary bar is **official Supabase self-hosted Compose release `self-hosted/v0.8.2`**. On the review date the official Docker installation guide names this tag. Public Git tag resolution confirms commit `564eab8ad7840b13324f68b1bfac074ef8d51c21`. The [source identity record](../benchmarks/supabase-v0.8.2.source.json) records the annotated tag, peeled commit and checksum of its public Compose file. This proves the candidate is named and publicly fetchable, not that it has been run in this task. [Official release](https://github.com/supabase/supabase/releases/tag/self-hosted%2Fv0.8.2), [official installation guide](https://supabase.com/docs/guides/self-hosting/docker).

Before a runtime slice begins, a reference custodian must resolve the tag to its complete commit, archive the `docker/` subtree and applicable tests, record file checksums, resolve all service images to platform-specific digests and retain the exact configuration after removing secrets. A release tag alone is insufficient to reproduce image contents. Fetch the reference using a pinned public checkout; never execute a floating installation script as the acceptance baseline. Run it on disposable infrastructure with generated credentials. Verify the scripts actually present in that frozen revision instead of assuming current documentation and historical files agree.

Phase 0 passes only when all three tests hold:

| Test | Required evidence |
|---|---|
| Named | Release tag, full commit, configuration checksum and image manifest stored in the comparison packet |
| Fetchable | Independent reviewer can obtain public inputs, validate hashes and start the isolated reference through documented commands |
| Comparable | Same fixture, SDK versions, service options, CPU/RAM/disk limits, network conditions and measured operations for reference and candidate |

Do not force shared-engine topology to resemble a single upstream stack. Compare externally observable contracts under matched budgets, and publish topology and failure-boundary differences. For multiple projects, compare against an equal number of dedicated upstream stacks; if that arrangement cannot fit a budget, report that fact and also compare an equal-workload viable configuration. Do not redefine workload merely to produce a victory.

A security issue may make the frozen reference unsafe outside isolation. Retain its identity for reproducibility, record the applicability assessment, and run only in a private disposable environment. If a patched bundle is required, formally amend the bar and rerun affected comparisons. Never use reference vulnerabilities as permission to weaken candidate security.

Pigsty's official integration documentation describes Supabase with PostgreSQL recovery and HA tooling and references pgBackRest and external backup repositories. It explicitly ties recovery claims to backup/WAL state and drills. This verifies relevance as an operations supplement; it does not verify its integration in Sbarbase or establish a Docker-only architecture. Freeze a public version and runnable drill before using it as a recovery comparison. [Pigsty integration and recovery discussion](https://pigsty.io/docs/app/supabase/).

## 3. Slice contracts and ordering

Each slice needs a builder specializing in that area, a separate fresh-context critic, a bar fragment, executable inspection route and measurable win condition declared before implementation. Parallelize only slices without shared mutable fixtures or dependencies. A dependent slice can be researched while prerequisites run, but cannot declare acceptance before those prerequisites pass.

| Slice | Bar fragment and minimum acceptance | Dependencies |
|---|---|---|
| G0: truth and distribution | Public clean clone builds immutable Linux images; no private/local external source links or runtime dependencies; release/capability/evidence identities agree; missing checks block acceptance | None |
| G1: portable installation | Same Linux image installs from a relocated checkout on clean supported Linux hosts; preflight rejects unsupported required capabilities before allocating runtime resources; interrupted install reconciles without adoption of never-started state | G0 |
| G2: SDK service contracts | Reference and candidate pass identical SQL/RPC/RLS, Auth session, Storage bytes, Realtime reconnect and Functions fixtures; standard client needs only declared URL/key configuration changes | G0, G1 |
| G3: complete recovery | Fresh-host restore matches database rows, policies, object checksums, feature state, configuration and declared credential policy; concurrent object writes/deletes produce a coherent recovery point or a clear refusal; corruption/wrong key fails closed | G1, G2 |
| G4: administrative security | Role/membership matrix, cross-environment API/key/session denial, management MFA/recovery and rate limiting pass; tenant operation never grants host administration; transfer revokes declared old credentials | G0, G2 |
| G5: lifecycle and updates | Create/pause/resume/retire/purge and upgrade reconcile after every declared crash point; neighbor data remains intact; incompatible migrations refuse or provide a tested restore route | G3, G4 |
| G6: ordinary developer workflow | Fresh user performs DNS/TLS/email setup, migration, type generation, Functions deployment, staged promotion and restore using published steps; zero undocumented interventions in five observed pilot runs | G2, G3, G4 |
| G7: upstream feature breadth | Each supported pooler, OAuth/MFA/hook, extension, Cron/Queues/Vault, TUS/S3 and Studio capability has enable/configure/use/deny/restore/upgrade cases; absent features remain explicitly unavailable | G2, G3, G5 |
| G8: efficiency and isolation | Matched workload satisfies the predeclared performance spec below and every cross-project denial; resource limits protect peers under noisy workload and reconnect storms | G2, G4, G7 |
| G9: support and customization | Effective configuration, restart impact and conflicts are visible; diagnostic bundles pass seeded secret/PII leak checks; each failure identifies affected scope, observed cause and safe action | G3, G5, G6 |
| G10: dedicated placement and migration | Move to a second host proves one writable owner, fenced failed cutover, route drain, checksummed data and declared lost-data/recovery-time bounds without disturbing neighbors | G3, G5, G8 |
| G11: HA and PITR | Partition, leader crash and object/backend loss drills satisfy frozen profile RPO/RTO and single-writer invariants; shared-cluster PITR restores to an isolated target before extracting one environment | G10 |
| G12: public release | All claimed profiles pass reproducible acceptance, public pilot and seven-day soak; licenses, contributor setup, vulnerability reporting and support windows are published | All claimed slices |

The feature inventory is open-ended upstream work, not a promise that a current cloud-only capability already exists. Managed-only features require a separate explicit implementation proposal and acceptance cases. Unsupported features cannot be silently counted as passing parity.

## 4. Spec bar for novel multi-project behavior

Version this initial owner acceptance proposal as `multi-project-v1` before builders begin. Its numeric limits are planning requirements, not measurements or universal capacity claims. Store any amendment with rationale, independent review and invalidated test identifiers; never weaken a failed criterion after inspecting the candidate simply to obtain a win.

Use a deterministic workload seed, four environments, 100,000 application rows and 1,000 objects totaling 1 GiB per environment. Exercise sustained REST/RPC, Auth refresh, Storage read/write, Realtime subscriptions and Functions on an 8-vCPU, 16-GiB RAM, 100-GiB SSD-class declared profile. These are test conditions, not installation minimums. Publish schema, distributions, concurrency, operation mix, offered rate and storage/network characteristics before the first measurement. Baseline throughput discovery happens only on the frozen reference, then freezes the offered rate at 60% of its sustainable throughput for both artifacts.

For the base contract all successful responses must satisfy reference semantics, all injected forbidden cross-environment requests must be denied, and no acknowledged committed record may disappear on ordinary container restart or host reboot. Repeat create/resume/reconcile with the same operation identity ten times: exactly one owned runtime and one committed state transition. Inject termination before and after every listed persistent transition: the next reconciliation must produce the documented stable state or a visible blocked receipt, never a second writer or success with incomplete work.

For the comparison run five alternating reference/candidate trials after a fixed warm-up, each lasting at least 30 minutes. Preserve all trial results. Correctness and safety are hard gates. At the frozen offered rate, candidate p95 latency must be no more than 10% above reference, error rate no more than 0.1%, and no higher than reference by more than 0.05 percentage points. A performance victory requires at least 20% lower aggregate steady-state memory for equal completed work, without increasing p99 latency above reference by more than 15%. Use measured working-set and peak-memory definitions, include controller/helper costs, and report confidence intervals. An inconclusive interval is a loss for the claim, not a win. The independent critic may choose the reference if the experience or maintenance tradeoff is worse despite numeric success.

A noisy-neighbor run applies an enforced environment budget to slow SQL, upload saturation and Functions concurrency. Peer p95 must stay within 20% of its candidate-alone baseline at identical offered rate, with zero ownership/isolation violations. If shared infrastructure cannot satisfy the claim, restrict its supported workload profile and provide a measured dedicated placement option; do not erase the requirement from the shared-profile comparison.

Freeze separate recovery targets before G3/G11 runs. Initial single-server fixture target: recover the above dataset on a blank equivalent host in at most 60 minutes from an available verified recovery set; restore all data included in that checkpoint, and report its age rather than claim zero live-backup RPO. HA targets remain undecided until topology, synchronous/asynchronous policy and fault model are selected. G11 cannot pass while those fields are undecided.

## 5. Deterministic Docker test layers

The published acceptance interface must require Docker and Compose, not host Bun, Python, a desktop service or agent tooling. The initial source-only layer now has a repository-owned command: `sh deploy/verify/run.sh`. See its [scope and limitations](../../../deploy/verify/README.md). It bakes source into a Linux test image, runs without network, bind mounts or a Docker socket, and retains all stage results. It must reject failed, skipped or empty test suites. This layer does not establish runtime or release acceptance.

The comprehensive interface remains proposed as `docker compose -f compose.acceptance.yaml run --rm acceptance --profile <profile> --evidence <relative-directory>`. That file and command are still a deliverable, not an existing verified entry point. The comprehensive runner must exit nonzero on errors, warnings, stale/missing evidence or incomplete required cases. Existing Bun/Python checks are brought into pinned test images, retaining their documented test discovery and semantics.

| Layer | Required execution and artifact |
|---|---|
| Static/distribution | Frozen dependency install, type/build diagnostics, license inventory, secrets scan, public dependency resolution, configuration validation and runtime mount inventory |
| Unit | Fixed seeds/clocks where suitable; deterministic fault simulation; permission, transition, checksum and redaction contracts; no mock-only claim about service interoperability |
| Image/build | Clean context, no ignored private prerequisites, reproducible application outputs/checksums where supported, image architecture/digest/SBOM record and compose validation |
| Integration | Actual pinned upstream containers, disposable named volumes, full SDK/SQL assertions, generated credentials and isolated public-facing route; no health-only substitutes |
| Browser/workflow | Containerized headless browser against actual release images, desktop/mobile sizes, accessibility, Auth redirects, uploads, Studio and understandable error/recovery flows |
| Fault/recovery | Kill controller/service at declared points, disk-full, denied permissions, unavailable registry, failed certificate/SMTP, network interruption, corrupt/missing objects and encrypted backup errors |
| Host durability | Blank independent VMs, daemon restart and real guest reboot, retained-volume restart, new-host restore, relocated checkout, non-default Docker data root and supported enforcement modes |
| Performance/soak | Matched comparison trials, noisy neighbors, connection storms, peak resource capture and seven-day realistic workload with integrity checks |

Service readiness polling is bounded and reports the final observed state. Retrying startup observation does not retry the test operation or overwrite its failure. Use unique Compose project names, allocated ports and ownership labels. Teardown deletes only disposable resources belonging to that run. Do not stop or prune unrelated host resources. Test evidence must prove which Docker context and daemon were used without recording sensitive connection credentials.

Native amd64 production profiles come first. Define exact Linux distribution, kernel, engine/Compose version, cgroup mode, security enforcement, architecture, filesystem and disk configuration for each supported profile. Add native arm64 only after every service image and native dependency passes the same applicable suite. Windows Docker Desktop/WSL2 and macOS are optional evaluation profiles using identical Linux images; unsupported path/socket/network capabilities must result in an honest preflight refusal. No Windows-specific or macOS-specific runtime adapter is planned. Rootless, remote Docker and other engines remain separate experimental profiles until actual requirements pass.

## 6. Builder, independent critic and repeat protocol

A reference custodian creates randomly assigned A/B endpoints and comparison packets with scrubbed product labels. The mapping is retained outside the critic's packet. Give the critic only the slice contract, frozen source ledger, anonymous executable artifacts, fixtures, raw observations and inspection instructions. Do not provide builder chat, difficulty, round count, preferred outcome or source diff as its primary inspection. A new critic session is created for every judgement. Infrastructure metadata that cannot be anonymized is recorded as a blinding limitation; never claim full blinding when identity is visible.

The critic executes or observes the artifact, rather than merely reviews source. For comparative slices it must pick A or B and name the single biggest gap. Decode the mapping after the pick: candidate selected plus all hard gates means WIN; reference selected, any hard-gate failure, ambiguity or uninspectable output means LOSS. For novel spec slices the binary verdict is SPEC MET or SPEC NOT MET, with executable observations for every mandatory assertion. Source review supplements runtime inspection for security; it does not replace it.

On LOSS send the largest gap to the builder, fix it and rerun affected gates against a new exact revision. Retain earlier failures. The next critic remains independent and fresh. After three losses with the same gap, review whether the slice is too broad or the bar incomparable, re-slice or repair the inspection method, and document the change. There is no fixed round cap. Report slice, binary verdict and gap after each round. The user can stop the process; exhaustion of a turn or temporary missing infrastructure does not mean platform completion.

## 7. Public source ledger and provenance

These public pages were reviewed on 2026-10-03. They establish research relevance only; no reference runtime was executed for this document. Store immutable source snapshots/checksums when Phase 0 is implemented, because documentation pages can change independently of a release.

| Source | Version/date and scope | Use and limit |
|---|---|---|
| [Supabase release](https://github.com/supabase/supabase/releases/tag/self-hosted%2Fv0.8.2) | Named `self-hosted/v0.8.2`, full commit recorded in the source identity file | Primary service reference, image bundle/runtime evidence still required |
| [Supabase Docker guide](https://supabase.com/docs/guides/self-hosting/docker) | Live documentation reviewed 2026-10-03 | Release setup and configuration context; freeze applicable content and resolve revision disagreements |
| [Supabase self-hosting overview](https://supabase.com/docs/guides/self-hosting) | Live documentation reviewed 2026-10-03 | Separate self-managed responsibility and feature availability from cloud equivalence |
| [Pigsty Supabase integration](https://pigsty.io/docs/app/supabase/) | Live documentation reviewed 2026-10-03; executable version not yet frozen | Database recovery/availability supplement only; not a required dependency or complete application restore guarantee |
| [Docker Compose application model](https://docs.docker.com/compose/intro/compose-application-model/) | Live documentation reviewed 2026-10-03 | Portable service/network/volume configuration model; does not certify arbitrary host compatibility |

Each run evidence manifest records: exact source commit and dirty-tree patch hash, release image/build digests, upstream full commit and images, test-image digest, fixture/spec version and hashes, effective non-secret configuration, profile, hardware/resource limits, start/end timestamps with timezone, command/exit code, test identifiers, expected/observed outcomes and artifact checksums. Untagged changes invalidate affected evidence. A green status without its raw observations is insufficient.

Keep operational secrets out of evidence. Record secret reference identifiers or hashes only where safe, never credential values, token-bearing URLs or entire environment dumps. Seed synthetic passwords, JWTs, signing material, email addresses and user content through logs, exception messages, command arguments and backup manifests. Redaction must remove each seed from exported bundles while retaining useful failure context; test nested structures and encoded representations. Private artifacts stay access-controlled with a stated retention policy. Public reports contain sanitized summaries and verifiable hashes, not customer data.

## 8. CI, release gate and continuous improvement

On each change run the containerized static/unit/build suite and affected integrations. A scheduled full suite catches dependency and host-profile drift. Release-candidate CI runs every supported-profile gate, reference comparison, recovery/fault suite, browser workflow and performance/soak gate on exact immutable images. Manual public DNS/TLS renewal, cloud import and novice pilot checks require signed observations bound to that release. Missing credentials or infrastructure means blocked evidence, not an automatic skip or pass.

The acceptance runner checks required test inventory against executed identifiers and rejects silent omissions. An exemption requires a factual non-applicability rationale and independent review, scoped to a profile and release; a known failure cannot become an exemption. Security findings need applicability, remediation and verification, not a hidden warning suppression. Evidence freshness follows source/dependency/configuration impact: any relevant change requires a rerun. Time-bound external sources also need refresh before an affected slice and each release.

Maintain one capability registry linking feature, upstream bundle, configuration schema, test IDs, support profile, limitations and evidence. Generate user-facing support documentation from it. Maintain `COMPLIANCE.md` with current blockers and distinguish scoped task completion from production release acceptance. Document contributor setup, security disclosure, dependency licenses, configuration migration, backup portability, upgrade support window and release compatibility policy. Audit public links and examples for workstation-only assumptions before merging documentation.

After each incident or failed drill, preserve reproduction and root cause, introduce the smallest meaningful regression fixture, update the runbook and rerun affected gates. Review user workflows through consented interviews and pilots; no external contact is implied by this method. Track first SDK success time, undocumented interventions, verified restore coverage/age/duration, upgrade outcomes, workload efficiency and support burden. Feed observed gaps into new independently judged slices.

Full-goal completion requires all owner requirements, every claimed feature/profile and every mandatory gate to have current direct evidence. Unknown, failed, stale and unrun remain incomplete. Until G0 through the release scope are proven, status stays in development and this method remains an execution contract rather than a claim of a problem-free platform.

## 9. Current execution ledger

The [durable ledger](../gauntlet-ledger.json) carries slice status and the next concrete action. It records evidence references without turning a document review into runtime acceptance. The first independent method comparison selected the public reference: the larger candidate contract had no implemented comprehensive acceptance runner. That LOSS remains open until its actual gates can be inspected. The source-only runner under `deploy/verify/` is an initial executable layer; it cannot close integration, recovery, performance or release slices by itself.
