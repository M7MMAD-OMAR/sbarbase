# Plans and design records

Working plans, design drafts and their reviews. A file here is a proposal until the code it
describes is on main and its evidence is committed under `docs/evidence/`.

| Date | File | Topic | Status |
|---|---|---|---|
| 2026-09-25 | [update-channel](2026-09-25-update-channel.md) | release channel, console update notice, one-click and opt-in automatic updates with a lossless way back | built ([UPDATE-CHANNEL](../UPDATE-CHANNEL.md)); CI cases and VM rehearsal pending |
| 2026-09-23 | [roadmap](2026-09-23-roadmap.md) | milestones in order: a real server, backups as a feature, Studio per environment, upgrades, onboarding | active; the next step for agents |
| 2026-09-23 | [verification-and-migration-plan](2026-09-23-verification-and-migration-plan.md) | verification run, hierarchy follow-ups, competitors, `sbarbase import` design, ordered steps | proposal, partly done |
| 2026-09-21 | [RESOURCE-POLICY](../RESOURCE-POLICY.md) | tiers, weights, IO limits, derived placement arithmetic, measurement method | built, uncalibrated; two measurements blocked (sections 3.6, 5.0) |
| 2026-09-21 | [ENVIRONMENT-EMAIL](../ENVIRONMENT-EMAIL.md) | per-environment SMTP through original Auth, probe with Mailpit | built and probed; the provider decision is open |
| 2026-09-21 | [OPERATOR-NOTIFICATIONS](../OPERATOR-NOTIFICATIONS.md) | durable outbox, channels, dedupe, redaction | built; eleven kinds emitted, three not, no scheduled caller |
| 2026-09-21 | [CONTAINER-GENERATION-MIGRATION](../CONTAINER-GENERATION-MIGRATION.md) | journaled replacement of a managed database container | built and crash-tested; attended run on the retained database done 2026-09-25; the recreation probe and load vehicles remain |
| 2026-09-21 | [three-topics-redteam](../reviews/three-topics-redteam.md) | adversary: unfakeable acceptance criteria and forbidden shortcuts | review |

The 2026-09-21 execution plan and generation migration plan were removed on 2026-09-24: the
roadmap replaces their next steps, the design notes above record what was built, and the
generation migration's remaining acceptance is in its own note. Both remain in the repository
history.
