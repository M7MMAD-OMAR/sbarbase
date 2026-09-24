# Plans and design records

Working plans, design drafts and their reviews. A file here is a proposal until the code it
describes is on main and its evidence is committed under `docs/evidence/`.

| Date | File | Topic | Status |
|---|---|---|---|
| 2026-09-23 | [verification-and-migration-plan](2026-09-23-verification-and-migration-plan.md) | verification run, hierarchy follow-ups, competitors, `sbarbase import` design, ordered steps | proposal |
| 2026-09-21 | [execution-plan](2026-09-21-execution-plan.md) | resource distribution, environment email, operator notifications | in progress |
| 2026-09-21 | [RESOURCE-POLICY](../RESOURCE-POLICY.md) | tiers, weights, IO limits, derived placement arithmetic, measurement method | built, uncalibrated; two measurements blocked (sections 3.6, 5.0) |
| 2026-09-21 | [ENVIRONMENT-EMAIL](../ENVIRONMENT-EMAIL.md) | per-environment SMTP through original Auth, probe with Mailpit | built and probed; the provider decision is open |
| 2026-09-21 | [OPERATOR-NOTIFICATIONS](../OPERATOR-NOTIFICATIONS.md) | durable outbox, channels, dedupe, redaction | built; eleven kinds emitted, three not, no scheduled caller |
| 2026-09-21 | [three-topics-redteam](../reviews/three-topics-redteam.md) | adversary: unfakeable acceptance criteria and forbidden shortcuts | review |
| `2026-09-21-generation-migration-plan.md` | The executable plan for the deferred generation migration, which four open items wait on. |

The three design records were produced by delegated design passes and reviewed by the parent
on 2026-09-21. The review re-read the load bearing claims against the code and reproduced the
counts it rests on. Anything the designers could not confirm against the pinned upstream
version is labelled unconfirmed in place, and the red team document lists the acceptance tests
each implementation must satisfy, including the ones whose pass is the absence of something.
