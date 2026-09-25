# Engineering notebook

Detailed notes written while each mechanism was built and tested. They are dated, precise and sometimes superseded by a later note; the header of each note says when it was recorded. Read the matching [explain page](../README.md#explain-why-it-works-this-way) first for the plain version, and [status](../reference/status.md) for what holds today. Terms are defined in the [glossary](../reference/glossary.md).

Continuity notes for coding agents are in [handoff](handoff/README.md). The chronological project log is [checkpoints](checkpoints.md).

## Architecture and control plane

- [ARCHITECTURE-REVIEW](ARCHITECTURE-REVIEW.md): the original foundation review, options and acceptance gates.
- [CONTROL-PLANE](CONTROL-PLANE.md): catalog roles, management HTTP, management Auth and key issuance.
- [PERSISTENT-ROUTING](PERSISTENT-ROUTING.md): routing records, maintenance flag, revisions and placement.
- [EDGE-FUNCTIONS](EDGE-FUNCTIONS.md): one edge-runtime per environment, its mounts and networks, deploy versions and the gateway path.
- [REALTIME](REALTIME.md): one Realtime per environment, its login and its superuser window, and the socket path through the gateway.
- [STUDIO-INTEGRATION](STUDIO-INTEGRATION.md): specification for one upstream Studio per environment (on-demand path implemented; see the update at its top).
- [DESIGN-REFERENCE-SUPABASE](DESIGN-REFERENCE-SUPABASE.md): Supabase dashboard screenshots used as a visual reference.

## Provisioning and crash safety

- [PROVISIONING](PROVISIONING.md): durable local provisioning jobs and the worker.
- [PROVISIONING-RECEIPTS](PROVISIONING-RECEIPTS.md): effect receipts and why unknown outcomes block replay.
- [WORKER-EFFECT-OWNERSHIP](WORKER-EFFECT-OWNERSHIP.md): the worker lock surviving into the provisioning process.
- [EFFECT-GUARDIAN](EFFECT-GUARDIAN.md): parent-bound guardian with a deadline and group cleanup.
- [NATIVE-OUTCOME-RECOVERY](NATIVE-OUTCOME-RECOVERY.md): completion witness that recovers a lost acknowledgment.
- [PREFLIGHT-RECOVERY](PREFLIGHT-RECOVERY.md): bounded requeue of interruptions proven to be before any mutation.
- [ACTIVE-PREFLIGHT-CRASH](ACTIVE-PREFLIGHT-CRASH.md): a real supervisor SIGKILL during preflight.
- [PROVISIONING-INSPECTION](PROVISIONING-INSPECTION.md): the read-only inspector for unresolved effects.
- [PROVISIONING-MUTATION-MAP](PROVISIONING-MUTATION-MAP.md): every mutation boundary and the SQL integration gate.
- [PARTIAL-DATABASE-CRASH](PARTIAL-DATABASE-CRASH.md): state left by interruption between SQL phases.
- [FRESH-WORKER-LIFECYCLE](FRESH-WORKER-LIFECYCLE.md): the full worker, SQL, Auth, REST and Storage lifecycle test.
- [PUBLISHED-ENVIRONMENT-RESUME](PUBLISHED-ENVIRONMENT-RESUME.md): restarting published environments without reprovisioning.

## SQL fencing

- [DATABASE-OPERATION-FENCING-DESIGN](DATABASE-OPERATION-FENCING-DESIGN.md): design constraints for fencing across databases.
- [SQL-OPERATION-FENCE](SQL-OPERATION-FENCE.md): database-local operation tokens and tombstones.
- [SQL-PAIR-REVOCATION](SQL-PAIR-REVOCATION.md): sequential two-database revocation prototype.
- [GUARDED-PROVISIONING-SQL](GUARDED-PROVISIONING-SQL.md): the guarded adapter around the provisioning SQL path.
- [RECEIPT-BOUND-SQL](RECEIPT-BOUND-SQL.md): SQL authority bound to the exact worker receipt.
- [SQL-DEADLINES](SQL-DEADLINES.md): REST statement and transaction deadlines per environment.

## Connection rules (HBA) and container generations

- [HBA-OPERATION-AUTHORITY-DESIGN](HBA-OPERATION-AUTHORITY-DESIGN.md): the authority protocol for writing connection rules.
- [ATOMIC-HBA-REPLACEMENT](ATOMIC-HBA-REPLACEMENT.md): complete-file replacement and stale request rejection.
- [SOURCE-HBA-INTEGRATION](SOURCE-HBA-INTEGRATION.md): the authority protocol wired into startup and the worker.
- [WORKER-HBA-CRASH](WORKER-HBA-CRASH.md): two native worker interruption cases.
- [HBA-LEGACY-ADOPTION-DESIGN](HBA-LEGACY-ADOPTION-DESIGN.md) and [HBA-LEGACY-ADOPTION](HBA-LEGACY-ADOPTION.md): adopting a retained database into the protocol.
- [TARGET-HBA-WRITERS](TARGET-HBA-WRITERS.md): inventory of every writer of `pg_hba.conf`.
- [CONTAINER-GENERATION-MIGRATION](CONTAINER-GENERATION-MIGRATION.md): replacing a database container under a journal, crash-tested.

## Gateway and load

- [GATEWAY-OVERLOAD](GATEWAY-OVERLOAD.md): per-environment and total admission, streaming.
- [FAIR-SHARE-ADMISSION](FAIR-SHARE-ADMISSION.md): a guaranteed share plus borrowed room, and the saturation notice.
- [INVITATIONS](INVITATIONS.md): inviting people into an organization, and the only path that creates a management account.
- [GATEWAY-DRAIN](GATEWAY-DRAIN.md): in-process pause lease and drain.
- [REST-CANCELLATION](REST-CANCELLATION.md): REST cancellation and retained admission.
- [SUSTAINED-OVERLOAD](SUSTAINED-OVERLOAD.md): sustained arrival probe and service admission.
- [SDK-LOAD](SDK-LOAD.md): managed-gateway SDK workload checkpoints.

## Resources and admission

- [RESOURCE-POLICY](RESOURCE-POLICY.md): tiers, CPU and IO limits and placement accounting (design record; implemented in 0.1.0, see the changelog).
- [RESOURCE-ADMISSION](RESOURCE-ADMISSION.md): memory and disk checks before allocation.
- [PRESSURE-ADMISSION](PRESSURE-ADMISSION.md): cgroup pressure thresholds.
- [CONNECTION-BUDGET](CONNECTION-BUDGET.md): per-login connection limits.
- [NOISY-NEIGHBOR](NOISY-NEIGHBOR.md): one bounded neighbouring-environment SQL probe.
- [COMBINED-RUNTIME](COMBINED-RUNTIME.md): starting source and target placements together.

## Recovery and placement

- [RECOVERY-EXPORT](RECOVERY-EXPORT.md): the encrypted per-environment export.
- [SOURCE-FENCING](SOURCE-FENCING.md): closing a source database and its service logins.
- [INDEPENDENT-RESTORE](INDEPENDENT-RESTORE.md): restore to a separate engine, verification and cutover rehearsal.
- [TARGET-LIFECYCLE](TARGET-LIFECYCLE.md): starting and stopping the retained moved target.

## Mail, notifications and operations

- [ENVIRONMENT-EMAIL](ENVIRONMENT-EMAIL.md): per-environment Auth mail through an operator SMTP relay (design record; implemented in 0.1.0).
- [OPERATOR-NOTIFICATIONS](OPERATOR-NOTIFICATIONS.md): operator events by email or signed webhook (design record; implemented in 0.1.0).
- [UPSTREAM-UPDATE-POLICY](UPSTREAM-UPDATE-POLICY.md): the binding rules for changing a pinned upstream image.
- [UPDATE-CHANNEL](UPDATE-CHANNEL.md): signed releases, classification from the diff, console requests, the health-gated confirmation and hold, snapshots and the way back (unit tests only; CI cases and VM rehearsal pending).

## Reviews

- [alternatives-product](reviews/alternatives-product.md): existing multi-project Supabase projects and licenses.
- [supabase-feasibility](reviews/supabase-feasibility.md): component feasibility and pinned Auth scan.
- [security-operations](reviews/security-operations.md): adversarial security and operations review.
- [capacity-method](reviews/capacity-method.md): how capacity would be measured.
- [distribution-bootstrap](reviews/distribution-bootstrap.md): the Supabase PostgreSQL distribution probe.
- [durable-runtime](reviews/durable-runtime.md): the durable upstream runtime experiment.
- [shared-storage](reviews/shared-storage.md): one Storage process for several tenants.
- [storage-recovery](reviews/storage-recovery.md): database and object recovery rehearsal.
- [independent-restore](reviews/independent-restore.md): separate-cluster restore gate.
- [upstream-management](reviews/upstream-management.md): dedicated management Auth and the composed API.
- [legacy-adoption-review](reviews/legacy-adoption-review.md): adversarial review of legacy HBA adoption.
- [target-and-deployment-review](reviews/target-and-deployment-review.md): recovery target, adoption, restore and deployment.
- [deployment-tooling-review-2](reviews/deployment-tooling-review-2.md): TLS termination, unit install and server acceptance.
- [three-topics-redteam](reviews/three-topics-redteam.md): acceptance criteria for resources, mail and notifications.

## Plans

- [plans](plans/README.md): working plans, led by the [roadmap](plans/2026-09-23-roadmap.md) and the [verification and migration plan](plans/2026-09-23-verification-and-migration-plan.md).
