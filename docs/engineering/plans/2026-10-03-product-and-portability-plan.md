# Supabase operations, compatibility and portability plan

Date: 2026-10-03. Status: research-backed roadmap with scoped implementation evidence, not release acceptance.
Initial research source: `b88e2f1343e16a0210e3e4bcbd3429e3e8c563c3`. Later immutable core restore, catalog placement and original-image probes are recorded separately in the execution ledger. None establishes full current-tree runtime or release acceptance.
Owner brief: keep original Supabase as the foundation, expose its capabilities and customization, approach the cloud workflow, and make operation understandable to someone without server administration experience.

Owner clarification: Linux containers through Docker are the primary runtime. No native Windows/macOS implementation or workstation-specific dependency is requested. The [durable goal](../../../PROJECT_GOAL.md) and [execution method](2026-10-03-gauntlet-execution-method.md) govern this plan. Host distribution coverage tests the same container system; it does not create a separate implementation for each OS.

This extends the [September roadmap](2026-09-23-roadmap.md). Existing milestones remain historical evidence. This proposal supplies the longer product direction and acceptance gates; it does not declare old blockers closed.

## 1. Product decision

Build a Supabase operations product for developers and small agencies managing several applications: one place to create environments, configure services, deploy changes, recover data and understand capacity. Keep upstream Supabase services and application contracts. Sbarbase owns orchestration, guidance and evidence that these services work together.

The initial promise should be: **manage several Supabase environments on a tested Linux server, with understandable setup and verified recovery**. The eventual promise adds several servers and stronger isolation. Neither a universal Docker claim nor complete cloud parity is justified today.

The first customer is an agency or independent developer with multiple small client applications. A second customer wants data ownership or restricted-network deployment. Regulated enterprises and public hosting for mutually hostile tenants are later segments, requiring stronger isolation, support and legal review.

The leading value hypothesis is operational confidence: a user can see which recovery point was actually restored, what a release will change, and how to move an environment when its server is full. This is an inference from the evidence, not a validated market size or willingness-to-pay claim.

## 2. What already exists and what remains uncertain

| Area | Repository evidence | Planning consequence |
|---|---|---|
| Multiple projects | `src/control/catalog.ts:154`, local SQLite, organizations, roles, projects and environments | Extend current management; do not rebuild it as a new product |
| Original services | `lab/durable_runtime.py:366`, pinned containers, resources and owned volumes | Preserve upstream components and test the integration contract |
| Shared engine and Storage | [architecture](../../explain/architecture.md) | Efficient placement also creates a shared failure boundary |
| Docker deployment | `compose.yaml:16`, host networking; `compose.yaml:45`, host socket, data root and checkout mounts | Current design depends on native Linux host behavior |
| Application features | `.github/workflows/ci.yml:120`, Studio, Auth settings, Realtime, Functions, SQL, import and key rotation probes | These features exist; latest execution was not verified here |
| Recovery | `lab/backup.py:231`; [backup guide](../../guides/backup-and-restore.md) | Improve recovery completeness and verification rather than add a second backup subsystem |
| Capacity | [status](../../reference/status.md#resources), four-environment lab guard | No 10-project or 100-project capacity promise without measurement |
| Distribution | `package.json` says 0.2.0; status still names 0.1.0 | Reconcile source, signed release, package and documentation identities |

Specific risks to investigate before expanding the product:

1. **Database and files capture:** `lab/backup.py:243` captures an exported database snapshot before `lab/backup.py:248` archives objects. Concurrent overwrite or deletion could invalidate metadata-to-object consistency. Reproduce deterministically on disposable fixtures before calling this a confirmed defect. Include multipart, TUS and object versions when supported.
2. **Incomplete installation recovery:** the current new-host route intentionally changes memberships, API keys, signing keys and enabled feature state. Feature-role restoration and whole-server cold restore have documented coverage limits. Recovery must describe deliberate credential rotation separately from missing recoverable state.
3. **Administrative authentication and ownership changes:** current status lists missing management MFA and login rate limits; project transfer does not rotate every credential. Close these before expanding public administration.
4. **Deletion:** `src/control/catalog.ts:1124` records deletion without reclaiming runtime resources. Implement retention and an explicit purge lifecycle; otherwise removed environments still consume capacity.
5. **Documentation contradictions:** deployment readiness calls Realtime and Functions unimplemented; Docker guidance calls update-channel evidence unit-only; upstream policy calls Studio unpinned despite its lock file. Replace contradictory status statements with a generated capability report.
6. **Restore write boundary:** the earlier live restore path has been replaced by staged restore, a durable operation journal, a retained PostgreSQL session and an OID-bound connection fence. Immutable core snapshot `514e267628b2487b5664c2f33e1285a1f161ad00daa2813fd42ee9a2a815ecbe` passed the 19-case environment/shared matrix, 14 actual SIGKILL interruptions, archive/file prerequisites and matching offline gate, with a fresh independent CORE-ONLY verdict. Earlier checkpoint and setup failures remain retained. Original Supabase workers, real API/pool readiness, independent-host recovery and full native feature continuity remain open. Core acceptance cannot establish these contracts.

## 3. Research evidence and limits

All external sources below were reviewed on 2026-10-03. Community discussions demonstrate needs expressed by their participants, not population prevalence. Competitor documentation establishes advertised scope, not independent reliability or benchmark results.

| ID | Primary source | What it establishes | Product implication |
|---|---|---|---|
| S1 | [Official self-hosting overview](https://supabase.com/docs/guides/self-hosting) | Single-project upstream management and responsibilities retained by an operator | Multi-project operations remain a relevant product layer |
| S2 | [Docker deployment](https://supabase.com/docs/guides/self-hosting/docker) | Current official deployment reference and versioned installation | Use an exact upstream release as the conformance baseline |
| S3 | [Updating deployments](https://supabase.com/docs/guides/self-hosting/updating) | Upstream already provides version-aware configuration merging; its backup is configuration-only | Add application checks and data recovery, and avoid competing with an obsolete installer description |
| S4 | [Database backups](https://supabase.com/docs/guides/platform/backups) | Database recovery does not recover Storage object contents | A recoverable application needs objects and metadata together |
| S5 | [Cloud-to-self-host database restore](https://supabase.com/docs/guides/self-hosting/restore-from-platform) | Database procedure excludes object transfer and Functions deployment | Import should inventory all services and produce a completeness report |
| S6 | [Self-hosted MCP](https://supabase.com/docs/guides/self-hosting/enable-mcp) | Upstream already documents an agent interface | Add scoped management and diagnostics; do not claim MCP is absent |
| S7 | [Multi-project discussion #4907](https://github.com/orgs/supabase/discussions/4907) | Direct user requests for multiple projects | Validate current agency workflows and costs |
| S8 | [Multi-project discussion #38048](https://github.com/orgs/supabase/discussions/38048) | Continued multi-project questions | A second signal, not a demand estimate |
| S9 | [Self-hosting discussion #39820](https://github.com/orgs/supabase/discussions/39820) | Operational feedback and upstream maintainer updates | Refresh assumptions as upstream improves |
| S10 | [Operations project discussion #49360](https://github.com/orgs/supabase/discussions/49360) | Competing operational tooling and corrections to outdated comparisons | Differentiate by measured recovery and multi-project workflow |
| S11 | [Envoy transition announcement](https://supabase.com/changelog/48048-self-hosted-supabase-envoy-becomes-the-default-api-gateway-b) | July 17 announcement of gateway, TLS and key-routing changes | Test normalized paths, headers and key translation against the selected release |
| S12 | [September PostgreSQL update](https://supabase.com/changelog/postgres-15-19-17-11-breaking-changes) | September 25 maintenance notice affecting some indexes, ciphers and operator recreation | Inspect actual installed version and data usage before choosing remediation |
| S13 | [S3 configuration](https://supabase.com/docs/guides/self-hosting/self-hosted-s3) | Current upstream storage deployment guidance | Review storage fixture maintenance and backend compatibility |
| S14 | [Storage issue #789](https://github.com/supabase/storage/issues/789), opened 2025-10-28 | A user reports recovery mismatches between past database metadata and present objects | Validate coherent application checkpoints, not just successful archive creation |
| S15 | [Modern self-hosted keys](https://supabase.com/docs/guides/self-hosting/self-hosted-auth-keys) | Actual upstream key/signing configuration and its limits | Test the key contract against the selected bundle |
| S16 | [Custom OAuth/OIDC](https://supabase.com/docs/guides/self-hosting/self-hosted-custom-oauth-providers) | A current upstream configuration route for custom providers | Build supported administration rather than a parallel Auth implementation |
| S17 | [WAL growth report #43643](https://github.com/supabase/supabase/issues/43643), opened 2026-03-11 | Participant reports WAL growth with a lagging analytics consumer; cause not reproduced here | Add disk/replication diagnosis and bounded retention tests |
| S18 | [Self-hosting Analytics](https://supabase.com/docs/reference/self-hosting-analytics) and [logs opt-in announcement](https://supabase.com/changelog/46084-self-hosted-supabase-making-analytics-and-vector-opt-in) | Optional observability, access protection and separate database recommendation | Offer a measured logs profile; retain a database-independent diagnostic path |
| S19 | [CLI workflow discussion #35616](https://github.com/orgs/supabase/discussions/35616), opened 2025-05-12 | Participant wants Docker configuration with familiar CLI workflow | Publish a versioned command-by-command support contract |
| S20 | [CLI grant-drift report #4902](https://github.com/supabase/cli/issues/4902), opened 2026-02-26 | Local Studio user reports repeated generated grants; stale closure is not proof of repair | Include Studio adoption and privilege preservation fixtures |
| S21 | [Declarative database schemas](https://supabase.com/docs/guides/local-development/declarative-database-schemas) | Current workflow depends on selected diff engine and compares declarative files with migration history | Pin CLI and engine; separate live database adoption from ongoing declarative edits |
| S22 | [Studio role transition](https://supabase.com/changelog/46081-self-hosted-supabase-switching-studio-from-supabase-admin-to-postgres-breaking-change), published 2026-05-18 | Default role changes to postgres; supplied ownership migration covers public only | Inventory actual roles and custom-schema ownership before upgrade |

The current pin file uses Supabase Postgres `17.6.1.166`. S12 warrants a priority applicability check, not an assertion that this installation is exploitable or that a particular fix can be safely substituted. Compare image contents, upstream patched image availability, extensions and actual usage. Do not change pins from a planning document.

Some upstream documentation can lag its release manifest. Where prose and deployed image versions disagree, record the discrepancy and inspect the selected release and runtime before acting.

Existing alternatives should inform reuse. [Coolify's Supabase guide](https://coolify.io/docs/services/supabase) and [Dokploy's template](https://dokploy.com/templates/supabase) cover deployment management. [Pigsty's integration](https://pigsty.io/docs/app/supabase/) is a candidate PostgreSQL operations foundation. [supabase-multitenant](https://github.com/GustavoMartins123/supabase-multitenant) and [supafleet](https://github.com/arunrajiah/supafleet) overlap multi-project composition. Consult the [earlier comparison](../reviews/alternatives-product.md) for historical findings, and recheck exact revisions and licenses before reuse. No competitor was installed during this task.

### Research refresh tied to recovery acceptance

The current [upstream update guide](https://supabase.com/docs/guides/self-hosting/updating) already preserves configuration edits through version-aware merging. Its configuration backup excludes Postgres and Storage data, and older deployments need a recorded baseline. Product value therefore requires an inventory of the actual installed bundle, an understandable upgrade preview, a tested data recovery point and post-update application checks.

The [database backup guide](https://supabase.com/docs/guides/platform/backups) explicitly excludes Storage object bytes. A successful database restore alone cannot establish application recovery. Test authenticated access and exact recovered object bytes as well as metadata, and preserve new writes after reopening.

Participants in [discussion 39820](https://github.com/orgs/supabase/discussions/39820) report upgrade compatibility, operational documentation, management access and combined database/object availability as pain points. These reports support the existing priorities; they do not establish market size. Upstream progress also invalidates old claims that installation or MCP tooling is absent. Review the selected release before describing a gap.

The additional six-source community review adds disk growth, existing Studio database adoption and role ownership checks. These are acceptance scenarios proposed from participant reports and official constraints, not confirmed Sbarbase defects. Analytics prose still mentions Kong and its opt-in announcement has an apparent rollout-year discrepancy. Resolve effective topology and dates from the selected versioned release rather than copy routing instructions or infer affected installations from a notice alone.

## 4. Priority problems and user outcomes

### First-beta boundary and expansion rule

Begin with one documented Linux x86_64 host profile, a local Docker Engine/Compose deployment, one versioned original Supabase bundle and one admitted placement profile. Record the exact distribution, kernel, engine, filesystem and resource minimums after independent clean-host evidence; no profile is supported merely by naming it here. Use native dedicated placement first when enabled features require it. Support multiple fresh environments under a measured workload limit, with SQL/Data API, Auth and Storage first. Realtime, Functions and Studio enter the supported set only after their own application/configuration/recovery gates pass. Show every unavailable capability explicitly rather than claim a complete stack from partial startup proof.

The first user journey must complete install, connect an application, preview an update and recover on a clean host from an offsite recovery set. General cloud import, shared-engine optimization, ARM, Kubernetes, public hostile tenancy, billing and HA do not block learning from this bounded beta. They remain required future milestones where applicable to the full goal; this boundary does not redefine full completion. Do not impose host Python, Bun, systemd or private workstation dependencies on the primary container journey.


Priorities are proposed judgments based on impact, existing gaps and dependencies, not fabricated scoring data.

| Priority | User problem | Proposed outcome | Proof required |
|---|---|---|---|
| P0 | I do not know whether my server can run this | Pre-install report with supported host capabilities, missing requirements and suggested action | Clean supported VM installs and unsupported-host refusals before mutation |
| P0 | I have backups but cannot recover my application | Complete encrypted recovery set, offsite copy, isolated restore drill and readable report | New-host restore including files, enabled services and secure credential policy |
| P0 | I fear upgrades and broken customization | Plan, maintenance window, compatible bundle, canary, recovery route and post-update SDK checks | Failed upgrades, failed migrations, configuration conflicts and crash injection |
| P0 | A wrong account or key can reach production | Management MFA/rate limits, least privilege, explicit production context, transfer rotation | Cross-environment and stale-credential denial tests |
| P0 | My disk fills and I do not know why | Explain table, object, WAL, slot, log and backup consumption; show headroom and reviewed remedies | Stalled-consumer and near-capacity fixtures; diagnostics remain available during database failure |
| P1 | Domains, HTTPS and email are confusing | Wizard checks DNS, certificate, public URL, OAuth redirect and SMTP delivery | Public DNS/TLS rehearsal plus OAuth and browser checks |
| P1 | Local, staging and production drift | Migration plan, type generation, seeds and Functions deployment through a documented workflow | CI promotion on a clean clone without cloud-only endpoints |
| P1 | One busy client hurts every application | Per-environment budgets, queue limits, measured capacity and dedicated-placement option | Mixed workloads with slow SQL, uploads, Functions and reconnect storms |
| P1 | Moving data loses features and secrets | Read-only import inventory, explicit exclusions, staged copy, validation and cutover plan | Versioned cloud-shaped fixtures and authorized real-cloud pilot |
| P2 | Growth means learning another platform | Guided move to a second tested server and predictable capacity | Fenced migration with a single writer and verified cutover |
| P3 | A server outage must not stop the app | Tested HA profile with explicit recovery objectives | Multi-node failure drills, including partition and split-brain prevention |

## 5. Honest cloud compatibility

There are three separate contracts: application API compatibility, developer workflow compatibility, and managed-service equivalence. Publishing one percentage for all three would conceal important gaps.

| Capability | Upstream basis | Current Sbarbase assessment | Planned contract |
|---|---|---|---|
| SQL, REST, RPC, RLS, Auth, standard Storage | Original upstream services | Implemented integration; scoped historical probes | SDK and SQL conformance on a supported version bundle |
| Realtime, Functions, Studio | Original upstream services | Optional implementations and CI wiring | Isolated feature lifecycle, configuration and recovery |
| Modern API keys and signing rotation | Self-host upstream supports modern key infrastructure | Key and signing probes exist; equivalence not established | Role restrictions, revoked keys, JWKS, session refresh and rotation contracts |
| OAuth, email, Auth hooks, MFA, passkeys, SSO | Upstream Auth capabilities vary with version/configuration | Settings implemented; complete feature coverage not established | Enable supported features individually; test callback and secret handling |
| GraphQL, pgvector, extensions, Vault, Cron, Queues, webhooks | Database capabilities and upstream extensions | Not a blanket supported feature set; cron and Vault import gaps documented | Availability and version inspection, scoped UI, migration and restore tests |
| Connection pooling and direct SQL | PostgreSQL and pooler integration | Restricted direct access exists; pooler absent | Session and transaction modes, prepared-statement caveats, migrations and IPv4/IPv6 tests |
| Large/resumable uploads, transformations and S3 protocol | Upstream Storage capabilities | Standard upload limit supported; TUS routing absent | Protocol-specific tests, memory/backpressure and object recovery |
| Git preview environments and branching | Cloud orchestration feature | Not equivalent to existing named environments | Build preview lifecycle with schema/seed isolation and secret separation |
| Managed backups/PITR and replicas | Cloud operations around PostgreSQL | Logical backup/recovery exists; PITR absent | Locally operated equivalents with measured limits |
| Management API and advanced metrics | Cloud management services | Sbarbase has its own management and observation APIs | Explicit subset adapter if justified; never advertise full compatibility |
| Analytics/vector buckets and ETL | Managed platform offerings excluded from standard self-host stack | No established equivalent | State unavailable until a separate upstream-backed implementation passes its gates |

The managed-only feature exclusions come from S1. S15 and S16 establish the specific key and custom-provider integration routes. Database `pgvector` and vector buckets are different capabilities. Ordinary Functions on one server do not establish worldwide edge execution.

For each capability store: upstream release and image digest, Sbarbase version, host profile, application contract, configuration schema, test identifiers, evidence revision/date, known limitations and status (`unsupported`, `experimental`, `tested`, `supported`). Generate documentation and console availability from this registry.

## 6. Architecture that can grow

Keep the existing catalog, jobs, receipts and recovery machinery as the starting point. Introduce a Docker runtime boundary for inspecting capabilities, planning placement, reconciling a desired environment, exporting, restoring and reporting health. Use the same Linux containers on supported Docker hosts. Dedicated-stack placement should use the ordinary upstream topology when needed for native feature compatibility and stronger isolation. Do not add OS-specific implementations or integrate private workstation projects.

Expose two placement choices when both are tested:

- **Shared engine:** lower per-environment overhead, shared PostgreSQL and Storage failure boundary, measured workload budgets. Logical separation is not independent infrastructure isolation.
- **Dedicated engine/stack:** greater resource use, clearer failure boundaries for sensitive or busy projects. Host administrators remain trusted; container separation is not a VM guarantee.

Placement must satisfy the enabled feature set before optimizing resource use. The [official pg_net contract](https://github.com/supabase/pg_net#installation) supports one database per PostgreSQL cluster, and pg_net provides Database Webhooks. A shared cluster with separate project databases cannot advertise complete native Webhook support merely because the extension can be created in each database. Provide tested native dedicated-engine placement for capabilities that require it. Display effective capability, estimated resource cost and any migration impact; do not ask a nonexpert operator to invent worker settings. This local placement prerequisite belongs before full feature acceptance, independently of the later second-host milestone. Validate original Supabase SQL/API behavior in the resulting placement and preserve explicit unsupported status until that proof exists.

Actual original-image evidence reinforces this prerequisite. On the admitted Supabase Postgres `17.6.1.166` image, native pg_cron `1.6.4` rejects installation in an ordinary differently named stage under unchanged `cron.database_name=postgres`. The application connection fence leaves existing cron/net workers attached until explicit termination. Fourteen bounded attached-worker samples become empty after exact termination, and reopening the same OID produces new PIDs. Independent review accepts these connection decisions only. Both overall probe invocations fail the unresolved original startup diagnostic gate; no cron job writes or HTTP deliveries were tested. A staged replacement original engine retaining application name `postgres` and a distinct maintenance database is therefore the next topology candidate, not an accepted design. It must prove original bootstrap-compatible replay, roles and extension ownership, Vault key continuity, paired Storage contents, native worker effects and crash-safe routing before admission. See the [placement contract](2026-10-03-native-placement-identity.md) and [evidence ledger](../gauntlet-ledger.json).

A later independently reviewed effects packet at immutable source `44755e02e24133afd50ae1cb560bc09d3ddf65d5811b1f8a9fa71781c5b658e5` proves four owned cron writes and five matching native HTTP deliveries across controlled disable/drain/fence/reopen/resume, plus normal exact cleanup and a matching six-stage offline gate. This advances the effects prerequisite only. Original startup diagnostics still fail, interruption cleanup is unrun, and replacement-engine replay, key/role/object continuity and actual full-stack API behavior remain mandatory. Native restore admission is unchanged until these broader contracts pass.

For a later multi-server installation, use a narrowly scoped host agent with authenticated encrypted transport, enrollment identity, operation receipts, fencing tokens, idempotent reconciliation and a desired-state model. Do not expose a raw Docker socket over the network. Define one writer for placements and operations. The local SQLite catalog cannot simply be copied to several active controllers; choose single-controller recovery first, then a transactional coordination store if HA management is warranted. Preserve atomic ownership and lease semantics.

Separate scaling from availability. Replicating REST or Auth does not make PostgreSQL redundant. Read replicas do not spread writes. Realtime needs its own connection/reconnect limits, Storage needs consistent object availability, and Functions need per-environment concurrency and egress controls. Multi-node HA requires database failover, object recovery, stable routing and coordination together.

PITR in a shared PostgreSQL cluster is especially important: physical WAL recovery is cluster-wide. Restore the cluster to an isolated target and extract the affected environment rather than rewind neighboring production environments. Storage versions or a coordinated object checkpoint are also needed for an application-level recovery claim. Evaluate existing PostgreSQL tools before building WAL or failover machinery.

Use typed configuration layered as tested defaults, installation settings and environment overrides. Show the effective setting, source, validation result and restart impact. Export a redacted version for Git. Treat credentials as references to a protected store. Do not offer arbitrary overrides of isolation identities or service credentials as ordinary customization. Preserve advanced configuration through upgrades and report conflicts explicitly.

## 7. Environment support contract

These are targets, not declarations that installations already passed.

| Environment | Target order | Required proof |
|---|---|---|
| Linux Docker Engine and Compose, x86_64 | First production candidate | Same container bundle on independent clean Linux hosts, full install/reboot/restore/TLS gates |
| Linux arm64 | Next candidate | Every service image available, native crypto and IO/resource probes, SDK and recovery suite |
| Other supported Linux distributions | After capabilities stabilize | Kernel/cgroup, security policy, filesystem and Docker data-root coverage |
| Windows/WSL2 and macOS/Docker Desktop | Optional developer evaluation | Same Linux containers, explicit Docker host networking, paths, volumes and resource accounting; no native OS adapter |
| Rootless Docker and remote Docker daemons | Experimental until separately proven | Endpoint privilege, cgroups, network, ownership and restart semantics in the same Docker runtime |
| Podman and other engines | Deferred outside initial Docker contract | A separate evaluated scope if real demand justifies it |
| NAS, low-memory devices and restricted networks | Explicit profiles only | Architecture, filesystem, registry access, offline bundle and minimum measured resources |
| Kubernetes | Later adapter | RBAC, persistent volumes, backups, controller crash behavior, upgrades and ingress |
| Serverless platforms | Different service role | Suitable app/Function workloads do not imply support for the complete persistent Supabase stack |

Publish the support window and exact tested kernel/engine/architecture/filesystem and host distribution. Docker image availability alone does not satisfy the contract. Host Python, Bun and systemd must not be required for the primary container workflow. Existing native installation scripts are legacy paths until independently maintained; they cannot dictate container portability. Cloud providers can reuse a proven host profile, but networking, disks, DNS, mail and certificate behavior still need provider-specific checks.

Host maintenance is a separate support contract from SB-03 database security maintenance. Publish supported OS/kernel/Docker/Compose versions and lifecycle dates, who monitors upstream security advisories, who authorizes patches, and what happens when a profile leaves support. Test planned update/reboot, application reconnection and recovery availability before promoting a profile. Show pending host maintenance and its consequences to the operator; do not claim containerization removes host administration. Docker daemon access belongs to trusted management, with an explicit privilege boundary. [Docker Engine security](https://docs.docker.com/engine/security/) and [security announcements](https://docs.docker.com/security/security-announcements/) inform this contract. Rootless operation remains separately experimental until its resource/network/ownership contracts pass. No automatic host patching or reboot is part of this research task.

## 8. User workflow and support

The console should lead through: inspect server, create operator, enable management protection, configure domain/HTTPS, create environment, configure email/Auth, connect app, set offsite recovery, complete first restore drill. A local evaluation can skip public DNS, but its status must remain distinct from production readiness.

For domain setup show the exact records to create, verify public resolution including IPv6, detect proxy/CDN behavior, check inbound ports and certificate renewal, and test WebSockets, OAuth callbacks and uploads. Retain a private administration endpoint where possible. Domain changes must preview redirects, cookies, signed URLs, client configuration and downtime effects.

For development keep the usual Supabase project layout. Support migrations via the documented direct database path, generated types, seeds and function source deployment. Present a promotion preview: schema diff, destructive changes, secret requirements and rollback limits. Preview environments receive synthetic or explicitly anonymized data; production credentials must not be copied by default.

Existing Studio-created databases need an adoption path: inventory objects and privileges, capture a baseline, review it, replay on a clean fixture and verify a subsequent no-change diff. Record the exact CLI version and diff engine, including unsupported command behavior. Current declarative workflows compare schema files with migration history; they do not capture later live Studio edits. Do not silently switch between engines. Preserve owners, ACLs, RLS, security-invoker views, publications and custom schemas; route bucket rows and other data changes through explicit migrations or seeds. Before the Studio role transition, preview actual owners and review custom schemas that the upstream public-only migration leaves untouched.

Capacity guidance must remain available when the application database is unhealthy. Separate database, Storage, WAL/replication-slot retention, logs and retained recovery-set usage. Offer optional log aggregation with documented access protection, redaction, retention and measured cost. A missing Logs Explorer should explain what evidence is unavailable. No generic cleanup action may delete WAL or replication slots; expansion or retention changes require a reviewed plan with application and recovery consequences.

Management disaster recovery needs a separate documented journey. With the original host unavailable, retrieve independently held offsite archives and encryption material, recover the controller/catalog/configuration and the declared application set, apply the published credential policy, reconnect a client and verify object access and new writes. Specify audited offline management recovery for lost MFA or an unavailable controller, revoke stale access and distinguish management recovery from application credential rotation. If encryption material has no independent surviving copy, report recovery unavailable; never silently weaken encryption. Domain-provider access loss needs an explicit fallback or refusal rather than an assumption that DNS can be changed.

Restore drills must suppress production email, callbacks and webhooks by default. Before reopening production, display a policy for pending, retried and overdue cron/queue work, external credentials and reconciliation. Record already delivered effects and known duplicate or lost-work consequences. Database/object consistency does not establish exactly-once external delivery. Add a separate adversarial future fixture for these side effects; this requirement does not widen the currently bounded native effects slice.

For incidents show: affected environments, observed symptom, evidence, recommended action and its consequence. Notify on meaningful state changes with deduplication and delivery tracking. Export a support bundle that redacts credentials and personal data, with user review before sharing. Separate an upstream bug, unsupported configuration and Sbarbase orchestration failure in issue triage.

Agent/MCP integration should begin read-only, scoped to selected environments and management roles. Mutation requires a concrete operation plan, audit record and policy appropriate to the impact. Agent text cannot override permissions. Suggested diagnostics should cite observations and runbooks; uncertain recommendations must not execute themselves.

## 9. Delivery phases and acceptance gates

The following timeboxes are planning estimates for a focused small team. They are not commitments or measured effort. Advance by gates, and adjust after discovery.

| Phase | Indicative timebox | Deliverables | Exit gate |
|---|---|---|---|
| A: establish truth | First 1 to 2 weeks | Capability/evidence registry, reconcile status/release docs, actual upstream security applicability, support preflight, native placement contract | Every claimed capability has a version and scoped evidence; unsupported hosts fail before changes; placement prerequisites explicitly block incompatible feature admission |
| B: trusted single server | Following 2 to 4 weeks | Native dedicated placement, complete recovery set and clean-host drill, backup race fix if reproduced, management protection, deletion/transfer lifecycle | Enabled native workers and application flows pass on admitted placement; failed operation and recovery drills preserve ownership and neighbor data; public Linux pilot passes |
| C: ordinary developer workflow | Following 3 to 5 weeks | DNS/TLS/SMTP guidance, migration/type/function workflow, pooler, feature inventory and import completeness | A new user completes install, app connection, promotion and restore without an undocumented admin intervention |
| D: measurable beta | Following 2 to 4 weeks | ARM candidate, workload benchmarks, support diagnostics, secure customization, preview environments | Published support matrix and measured recovery/capacity report; pilot issues triaged and release gate passes |
| E: beyond one server | After beta evidence | Second-host migration of already admitted placement, cluster-wide PITR recovery design, then HA profile | Fenced cutover and failure drills satisfy chosen RPO/RTO without split brain |

The old real-server milestone remains open. Real infrastructure, domains or paid services are not purchased or deployed by this research task. When that pilot is available, repeat clean install, public certificate renewal, reboot, application flow, offsite loss recovery and a seven-day realistic soak. Existing VM evidence does not substitute for it.

Concrete first backlog:

| ID | Work | Dependency | Acceptance |
|---|---|---|---|
| SB-01 | Versioned capability and evidence manifest | None | Reject stale/mismatched proof and generate one truthful status view |
| SB-02 | Host support preflight | SB-01 | Unsupported data root, cgroups, architecture or security profile diagnosed without mutation |
| SB-03 | PostgreSQL/security release applicability | SB-01 | Selected patch route documents affected objects and before/after application checks |
| SB-04 | Concurrent Storage backup adversarial fixture | None | Deterministic overwrite/delete races either restore a coherent checkpoint or fail honestly |
| SB-05 | Complete encrypted recovery contract | SB-04, SB-13a for required native features | Fresh-host recovery with enabled services, configuration, secrets and deliberate identity rotation policy |
| SB-06 | Management MFA/rate limiting and transfer rotation | SB-01 | Unauthorized and stale access denied, recovery-factor procedure tested |
| SB-07 | Soft delete, retention and purge | SB-05 | Auditable reclaim with neighbor safety and crash reconciliation |
| SB-08 | Guided domains/TLS/email | SB-02, SB-06 | Browser/Auth/WebSocket/renewal probes on public pilot |
| SB-09 | Pooler and SQL migration workflow | SB-01, SB-06 | Connection budgets and migration contract pass under concurrent workload |
| SB-10 | Import inventory and custom-schema support | SB-05, SB-09 | No silent exclusion; object checksums, policies and post-import SDK tests |
| SB-11 | Supabase feature configuration, Cron/Queues/Vault/TUS | SB-01, SB-05, SB-13a | Each feature separately enabled, secured, restored and versioned |
| SB-12 | Measured placement and developer previews | SB-05, SB-09 | Noisy-neighbor tests plus secret/data isolation in preview lifecycle |
| SB-13a | Native dedicated placement on one server | SB-01, bounded native identity/default-worker proof | Original image/bootstrap, app/maintenance identity, native effects, SDK flow and fenced recovery pass before feature admission |
| SB-13b | Transfer admitted placement to a second host | SB-05, SB-12, SB-13a | Single-writer transfer, route drain, verification and reversible failed cutover |
| SB-14 | Disk/WAL/log capacity and independent diagnostics | SB-02, SB-06 | Stalled consumers and near-capacity fixtures yield actionable diagnosis; approved remedy preserves application and recovery state |
| SB-15 | Adopt Studio schema and versioned CLI workflow | SB-03, SB-09, SB-10 | Clean replay plus no-change diff preserve owner/ACL/RLS/view behavior; custom schemas and DML have explicit coverage |

Future recovery/support fixture ownership must be assigned before implementation:

| ID | Maintainer role | Named future acceptance fixture | Gate |
|---|---|---|---|
| SB-16 | Runtime/security maintainer | management-disaster-recovery | Original host unavailable; independent recovery material, lost MFA/controller recovery, stale-access denial, and explicit unavailable-key refusal. Depends on SB-05/SB-06 |
| SB-17 | Supabase compatibility and recovery maintainers | recovery-external-effects | Restore drill sends no production effects; pending/retry/overdue cron/queue/webhook policy, observed duplicates/losses and reviewed reopening. Depends on SB-05/SB-11 |
| SB-18 | Runtime/security maintainer | host-maintenance-rehearsal | Supported OS/kernel/engine lifecycle, update/reboot/client reconnection/recovery access and clear unsupported-profile status. Depends on SB-02/SB-03 |

These are planned contracts, not existing accepted fixtures. The named maintainer roles must receive actual ownership before their workstream starts. SB-16 and the enabled-feature portion of SB-17 belong to first-beta recovery acceptance; broader feature coverage follows the capability registry. SB-18 belongs to the promoted host profile.

## 10. Validation with people and economic value

Conduct consented interviews and observed workflow tests with agencies, independent developers and existing self-host operators. This task did not contact anyone. Start with 10 interviews and 5 installation/recovery pilots as proposed discovery sample sizes, not as a statistical estimate of the community.

Ask about the last real upgrade or outage, what was lost, recovery time, projects per host, current hosting and operator costs, compliance needs, important Supabase features, and what prevents migration. Observe a fresh installation and a restore rather than ask whether someone likes the idea. Find out whether shared-engine efficiency or reduced operational effort motivates purchase. Reassess segment choice if the evidence favors dedicated projects.

Measure: time to first successful SDK call; undocumented manual interventions; completion of offsite backup and first restore; actual recovery duration and lost-data interval; successful upgrades and reconciliations; support requests per task; resource cost per measured workload; pilot retention. Do not set an availability SLA, project count or cost savings claim before collecting the relevant evidence.

Before observing pilots, publish the task definitions, assistance limits and go-or-narrow rules. Every safety or data-integrity failure blocks that workflow claim and requires repair. For the proposed five disposable-data pilot sessions, use four independent install/connect/restore completions as an initial maintainer decision target. Fewer completions means an operator-assisted beta while failed steps are repaired. Any undeclared maintainer intervention prevents that session counting as independent. This small-sample threshold is a proposed learning rule, not evidence about the community or an SLA. Set task-time targets from observed current workflows rather than invented savings.

Compare the same application tasks on the versioned upstream deployment and Sbarbase. Record configuration effort, all interventions, recovery completeness, duration and measured resources. If recurring multi-project burden is weak in interviews and observed tasks, revisit the customer segment and efficiency hypothesis before building shared-engine optimizations. Preserve recoverability work whose value is separately demonstrated. Participant outreach and public/paid pilot infrastructure require their own authorization; none occurred in this task.

Potential model: open-source core with optional paid assistance, maintained release bundles or hosted coordination. Validate willingness to pay before building billing. Clarify who owns data and responds to incidents. Avoid implying affiliation with Supabase, inherited compliance certifications or a legal guarantee from the choice of software.

## 11. Acceptance, ownership and limits

Assign a maintainer for each workstream before implementation: runtime/security, Supabase compatibility, recovery/operations and user workflow/docs. Use independent adversarial review for backup, migration, permissions and placement changes. Research sources inform decisions; test results determine supported claims.

The release gate must combine unit/build/type diagnostics, integration/isolation, native installation, browser/accessibility, security and dependency/license checks, recovery, performance and public-deployment observations. Every evidence item names source revision, bundle, platform and time. Missing evidence, warnings, skips of required cases, stale results and manual checks without observations block acceptance. [COMPLIANCE.md](../../../COMPLIANCE.md) records the current gaps.

The original planning task made no implementation or deployment changes. Subsequent execution has accepted only separately recorded immutable fragments. No paid deployment, complete runtime benchmark, full security audit or full original-stack acceptance follows from this refreshed roadmap. The product remains unaccepted for production, and the full goal stays active.
## Current native prerequisite status and historical attempts

Current authoritative status: original-image configured bootstrap is independently accepted only at immutable source 85791f0e1061259286822b9686afa817af61d26c6a735380e82d59085e5bcc69. The subsequent configured-effects window at source 042d6bd940c66bd0d762aa2f90f4d62624cc2168f0bc75fad7b237f648d528cd passed standalone preservation, the complete bootstrap prefix and six offline stages, but failed its first canonical ACL metadata query before cron/table/job/helper setup. Fresh actual review retains 804 evidence checks and 906 artifact hash records. Eleven/twenty/two root absences pass; never-created HTTP CID is UNRUN. The offline packet contains 1329 Python and 301 Bun tests. At that first window, a narrow ACL observer repair was declared; its subsequent review and actual outcome are recorded below. Configured effects, full recovery/services, cross-host support, security maintenance and release remain unaccepted. Later documentation does not inherit these historical source identities.

The subsequent ACL-only correction reached actual PostgreSQL successfully at immutable source c8b99a81a3428e7748576a7c991cfbd58ed4312bd3a4800624e6fbcdd7762d2b. It then refused the closed cron setup: seven routines have postgres EXECUTE WITH GRANT OPTION absent from the expected model. Fresh actual review verifies 634 evidence checks, the complete scoped bootstrap prefix, six offline stages and eleven/twenty/two root absences; HTTP CID remains UNRUN and no owned effects started. The final installed hook cause is unproven. Future work must establish that exact original versioned source cause before changing admission; no grant repair or arbitrary manifest widening is allowed. This advances the observer correction only and preserves the full roadmap gaps.

### Historical attempts, preserved losses and limited acceptances

The following entries describe successive earlier evidence windows. Their current-tense statements apply to those windows, not to the authoritative status above.

Configured original-image bootstrap remains NOT MET. The first three retained attempts distinguish verifier-format losses from the actual native initialization failure. Docker capability-prefix and BusyBox missing-file differences are now handled narrowly, but the third attempt's strict initial diagnostics show the pg_net worker using postgres before native migrations create that role. Successful offline source gates do not certify native bootstrap.

The tagged 17.6.1.166 Dockerfile places conf.d at /etc/postgresql/postgresql.conf.d and links the custom-config path to it. A temporary setting written through that link may stay in the seed's private layer. A separate actual metadata-only packet at immutable source 80f1b48feba6ee49f729febfd061218158a57fc812ee90d00052ac02918fa0e5 now proves that literal link and five original regular config files, including two empty customization templates. A fresh independent critic accepted eighteen evidence checks, matching six-stage offline verification and seven exact normal-cleanup observations. The private-layer write explanation remains an inference. The subsequent preservation fragment now proves those five original file bytes, modes and numeric owners in owned configuration volumes, and proves temporary-file visibility in an independent container after seed removal. PostgreSQL was not started in that accepted fragment. The next step is a separately declared bootstrap-only volume ownership handoff. Preserve the original symlink, entrypoint, Cmd, migrations, eleven preloads and key paths. Unknown layout or preservation mismatch refuses startup.

After an independently reviewed ownership handoff, repeat configured bootstrap with strict initial diagnostics, native worker identity restoration, warm restart and real remote positive/negative authentication. Then prove configured-profile job and HTTP effects before replacement-engine archive replay. Key/role/object continuity, complete services, pool routing, cross-host portability and release gates stay open. Evidence and independent losses are recorded in the ledger's native_configured_bootstrap and native_config_metadata sections; advisor evidence is .lab/native-config-path-advisor-20261003/report.md.

The next preservation gate is now declared: three sequential diagnostic helpers, two owned named volumes, no database startup, and UID100:GID101 with all capabilities dropped. It acquires bounded public-image baseline bytes for only the five reviewed files, verifies Docker population and original ownership, exclusively writes a 33-byte temporary override, removes the seed and requires an independent read-only container to observe identical preserved bytes and override identity. Eleven native and two offline exact cleanup observations and separate design/artifact reviews are mandatory. The first independent design review rejected a binary framing ambiguity before runtime. A length-aware cursor correction now passes a fresh separate design review with 88 independent synthetic checks. A frozen actual packet at source 367f3916e496c970b59578cebdb0fe2d52f2d593a1c018f48f1857c884612199 now passes 29 independent artifact checks for original-file preservation, combined volume population and independent visibility after seed removal, with 32 raw commands, a matching six-stage offline gate and thirteen exact cleanup observations. This acceptance is limited to those configuration contracts. Configured original-image startup, effective PostgreSQL settings, authentication, warm restart, keys and full recovery remain unaccepted. A separately declared bootstrap volume handoff is next.

The bootstrap ownership bridge is now implemented, but actual configured startup is still unaccepted. Two independent design reviews retained reproducible losses in cleanup/result admission rather than authorizing a retry. The first review's complete-mount and shell-framing blockers were independently confirmed repaired by the second review. Root status retention now passes fourteen synthetic adverse cases, including failed volume removal stopping subsequent deletions. A separate specialist is completing closed Python cleanup and negative-authentication diagnostic admission; another independent design review is required before any new native packet. The original standalone preservation, configured bootstrap and matching offline packets must share one frozen baked source identity, followed by eleven, nineteen and two exact cleanup checks and separate actual-artifact review. These implementation and pure checks do not expand the accepted runtime scope or replace the remaining product roadmap.

The ownership bridge subsequently passed a fresh independent design review and a same-source actual preservation regression. The configured packet then reached original final PID1 but failed while observing a public original script: its embedded error/warning message literals were treated as runtime diagnostics despite exit zero and empty stderr. The independent actual critic verifies this exact cause, accepts scoped preparation/preservation observations and finds no severity match in the captured startup logs. The formal initial-diagnostic gate, SQL settings, callback, warm restart and remote authentication were not completed. All observed normal-cleanup checks passed; two never-created authentication CIDs remain UNRUN. The next repair is confined to command-specific opaque data admission for the already declared six public artifact paths, retaining all other diagnostics and requiring another independent design review and frozen actual window. This is verifier correction, not configured bootstrap acceptance.

A subsequent same-source packet completed strict original startup diagnostics and observed the temporary pg_net admin setting, then restored a new postgres worker at the same application OID with preserved original config bytes and no temporary override. It still failed complete bootstrap: its pre-install identity baseline was compared after native pg_net installation, which added the single supabase_functions_admin role. Independent raw review confirms unchanged previous role fields, memberships, database identity and preload values; it does not establish a separate post-install/pre-transition baseline. The tagged original Supabase schema describes this setup role creation. The next declared correction retains the original inventory, verifies only the exact pinned installation delta, then preserves a complete post-install baseline through transition, warm restart and authentication. It does not filter roles or waive identity differences. Warm restart, authentication, configured-profile jobs, native keys and full restore remain unaccepted.

The latest configured original-image bootstrap fragment is independently SPEC MET at baked source85791f0e1061259286822b9686afa817af61d26c6a735380e82d59085e5bcc69. Original startup/migrations/preloads, temporary configuration preservation/removal, native worker transition at the same database OID, warm restart and remote positive/negative authentication have retained actual evidence. Full post-install role inventory is unchanged through transition, warm restart and authentication. Fresh actual review verifies151 commands,302 stream hashes,26 SQL stdin artifacts,110 config frames and32 exact normal cleanup observations; matching six-stage offline gate passes1328Python301Bun. Earlier failed packets remain losses, not retroactively accepted. The next declared fragment should exercise owned cron writes and tracked HTTP delivery after this original bootstrap, with no duplicate pg_net installation or relaxed diagnostics. This acceptance does not advance full native restore, keys, fenced continuity, full services/API/pools, cross-host portability, security maintenance,G0 or release.
