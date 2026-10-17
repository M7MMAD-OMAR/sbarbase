# Plans and design records

Working plans, design drafts and their reviews. Implementation and acceptance are separate: each claim needs its named scope, source identity and evidence. The [October evidence ledger](../gauntlet-ledger.json) records scoped passes and losses, including local packets not distributed publicly; `docs/evidence/` retains historical live probes. Code on main alone does not establish acceptance.

| Date | File | Topic | Status |
|---|---|---|---|
| 2026-10-17 | [container-install-and-site-docs](2026-10-17-container-install-and-site-docs.md) | Docker route as the website's install, Ubuntu 26.04 rehearsal from bare packages, Fedora and SELinux decision, Podman as a gated profile, docs rendered on the website in both languages | proposal; nothing implemented |
| 2026-10-14 | [upstream-currency](2026-10-14-upstream-currency.md) | Bring every pinned component current one at a time, Select 2026 capabilities (asymmetric keys, per-environment OAuth server, app MCP servers, Connections viewer), weekly drift tracking, watch list for OrioleDB and Multigres | in progress; seven images staged, Auth, PostgREST and Storage running on the real server; open: S4, P3 part 4 acceptance, A7, B1, F1 to F8 |
| 2026-10-10 | [management-project-workflow](2026-10-10-management-project-workflow.md) | Optional MFA policy and session authority, automatic production creation, visible Supabase dashboard and bilingual docs | source implementation with local verification; deployment acceptance separate |
| 2026-10-03 | [gauntlet-execution-method](2026-10-03-gauntlet-execution-method.md) | Linux Docker-only execution, named Supabase reference, independent critics, reproducible tests and complete acceptance scope | execution contract; bounded source verifier implemented; full runtime and release gates remain open |
| 2026-10-03 | [native-placement-identity](2026-10-03-native-placement-identity.md) | Catalog authority, explicit legacy identity, versioned engine/application/maintenance declaration and protected read-only routing | implemented and independently accepted for read-only declarations/routing; native activation and remaining consumers unaccepted |
| 2026-10-03 | [product-and-portability-plan](2026-10-03-product-and-portability-plan.md) | current community evidence, cloud compatibility, supported environments, recovery, guided operations and staged multi-server growth | research-backed proposal; no new runtime or release acceptance |
| 2026-09-25 | [update-channel](2026-09-25-update-channel.md) | release channel, console update notice, one-click and opt-in automatic updates with a lossless way back | built ([UPDATE-CHANNEL](../UPDATE-CHANNEL.md)); CI cases passed on a clean machine, and the VM rehearsal through the console and automatic mode on 2026-09-26; no real server yet |
| 2026-09-23 | [roadmap](2026-09-23-roadmap.md) | milestones in order: a real server, backups as a feature, Studio per environment, upgrades, onboarding | historical milestone ordering; extended by the October product plan and execution contract |
| 2026-09-23 | [verification-and-migration-plan](2026-09-23-verification-and-migration-plan.md) | verification run, hierarchy follow-ups, competitors, `sbarbase import` design, ordered steps | proposal, partly done |
| 2026-09-21 | [RESOURCE-POLICY](../RESOURCE-POLICY.md) | tiers, weights, IO limits, derived placement arithmetic, measurement method | built, uncalibrated; two measurements blocked (sections 3.6, 5.0) |
| 2026-09-21 | [ENVIRONMENT-EMAIL](../ENVIRONMENT-EMAIL.md) | per-environment SMTP through original Auth, probe with Mailpit | built and probed; the provider decision is open |
| 2026-09-21 | [OPERATOR-NOTIFICATIONS](../OPERATOR-NOTIFICATIONS.md) | durable outbox, channels, dedupe, redaction | built; Telegram connected from the console, disk and stopped-service alerts, the drain rereads its configuration |
| 2026-09-21 | [CONTAINER-GENERATION-MIGRATION](../CONTAINER-GENERATION-MIGRATION.md) | journaled replacement of a managed database container | built and crash-tested; attended run on the retained database done 2026-09-25; the recreation probe and load vehicles remain |
| 2026-09-21 | [three-topics-redteam](../reviews/three-topics-redteam.md) | adversary: unfakeable acceptance criteria and forbidden shortcuts | review |

The 2026-09-21 execution plan and generation migration plan were removed on 2026-09-24: the
roadmap replaces their next steps, the design notes above record what was built, and the
generation migration's remaining acceptance is in its own note. Both remain in the repository
history.
