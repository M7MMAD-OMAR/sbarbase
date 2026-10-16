[العربية](2026-10-14-upstream-currency.ar.md)

# Upstream currency and Select 2026 adoption

Plan, 2026-10-14. The owner asked for a complete plan to bring every upstream component current, to support the useful capabilities announced at Supabase Select 2026, and to keep tracking upstream from now on, without any regression of the base. The research and the adopt or defer decisions are in the [Select 2026 review](../../upstream/2026-10-14-select-2026-landscape.md).

Done locally with this plan, verified by unit tests and independent review: the adoption tool `lab/pin_update.py stage` now works for flat lock files and rewrites the lock's digest proof; `lab/upstream_watch.py` reports drift; `.github/workflows/upstream-watch.yml` runs it weekly once pushed. No image has been changed, and nothing here claims production readiness.

## 1. Goal and non-goals

Goal: every pinned component on a current upstream release that passed our gates, a standing process that notices new releases within a week, and the Select 2026 capabilities that fit a self-hosted installation available per environment.

Non-goals: hosted-only products (Compute, Pipelines, Turso, scoped personal access tokens for the Supabase Management API, Enterprise-Managed Auth for MCP); developer tools that never run on a server (the native CLI runtime); forks or patches of upstream services; release candidates; OrioleDB and Multigres before they qualify (section 7).

## 2. Where we stand

The full table is in the [review](../../upstream/2026-10-14-select-2026-landscape.md#current-drift-2026-10-14). In short:

- Against the official self-hosting compose file, which upstream tests as one set, we are equal or ahead for every component except REST (v14.15 against v14.17) and Storage (v1.73.1 against v1.74.0).
- Against the newest releases, every component is behind: PostgreSQL by five minor releases (17.6 against 17.11), Storage by nine minor releases, Realtime by five, REST by three patches in its line and one major line (v16).
- The policy promised recurring tracking and a `docs/upstream/` log; neither existed until today, and `stage` could not stage four of the six lock files.

## 3. The base we do not regress

These rules hold for every step below. They extend the [update policy](../UPSTREAM-UPDATE-POLICY.md); they do not relax it.

1. **One component per change**, staged with `lab/pin_update.py stage`, which writes the dated review entry first, records the rollback pin and keeps the lock's digest proof valid. Studio with postgres-meta is the only candidate pair. Policy rule 5 forbids bundling, so A6 is two changes unless the owner first amends rule 5 for this pair in its own reviewed change (section 9).
2. **A tested bar.** A release is a candidate when it is the official compose pin, or a newer stable release in the same line. Release candidates, `-orioledb` variants and new major lines need their own review entry and an explicit decision.
3. **A frozen baseline suite.** Before the first adoption, record one baseline run on the current pins, and run the identical set before and after every adoption:
   - `sh deploy/verify/run.sh`, `bun test`, `bun run typecheck:ui`, and the full Python suite;
   - the live checks the policy names: the fresh worker and supabase-js lifecycle, the known-refusal checks and the HBA probes. The baseline entry records each exact command line, and every later run replays that recorded list unchanged;
   - the Studio rehearsal, and a backup and restore drill of one environment;
   - the update channel check `lab/upgrade-check.py`. It hardcodes the next release after today's pins: `NEWER_REST` (postgrest v14.16), which CI's update channel job uses on every push, and `NEWER_AUTH` (gotrue v2.197.0), which its VM stages use. Every upgrade and rollback case in CI and in `lab/vm-milestones.sh` derives from these two candidates, and A2 to A7 and P1 rely on exactly that path, so it must never go dark. A1 and A4 change the check in the same change: the candidate is a release newer than the adopted pin when one exists; otherwise it is a different, digest-pinned earlier release used explicitly as a test-only candidate, and the evidence names its direction (for example v14.18 to v14.17). The check refuses a candidate equal to the base pin and never reports a downgrade as an upgrade. P1 and every later step wait while this check does not run;
   - for Auth changes, the management MFA fixture.

   A difference from the baseline blocks adoption until it is explained in the entry.
4. **Rollback is real.** Storage, Realtime and Auth run schema migrations inside the databases they serve, and swapping the image back does not undo a migration. The entry records the backups taken immediately before the change and the restores that return to them, covering every database the component touches:
   - Auth: each environment database and the management realm's Auth database;
   - Storage: its own `storage_metadata` database, where the shared process keeps its multitenant schema, every environment database, because that one process migrates them all, and the objects;
   - Realtime: each environment that runs it.

   Before A3, A4 and A5 run on the lab host, each rehearses its whole way back in the VM: forward, `lab/upgrade.py rollback`, the data restores and the baseline suite. P4 covers a Storage image that cannot start on its migrated schema; if the previous Auth (including the management realm) or Realtime image does not start in that rehearsal, the step is blocked until an equivalent way back exists for that service.

   The baseline drill restores one environment; before A3, A4 and A5 the backup of every affected database is taken and verified, not only the drill environment. Restoring these backups returns each database to the moment of the backup and discards every write made after it, so A3, A4 and A5 run in a maintenance window with application writes paused through P5 from before the step's first backup until the post-checks pass, and the entry records that cut-off.

   **How a pin reaches the running services, and how it leaves.** A lock edit alone moves nothing: the runtime refuses a pinned image that differs from a running container unless an upgrade intent exists, and only `lab/upgrade.py` writes one ([upgrades guide](../../guides/upgrades.md)). So each of A1 to A5 is committed as its own Sbarbase version, then installed with `lab/upgrade.py start --to <commit>` and a restart; the next start replaces only the Auth, REST, Storage, Realtime or Functions containers whose pin changed, after `upgrade.py` has backed up every environment and `storage_metadata`. After a rollback on the lab host, the step's lock change is reverted in the repository before anything else is staged (its review entry stays and records the rollback), and the next step waits until the failed one is reverted or accepted; otherwise the next `upgrade.py start` would bring the failed pin back together with the next component. The way back is `lab/upgrade.py rollback` and a restart, which brings the previous images back first; only then are data backups restored for a release that migrated schemas, because a restore admits images from the current lock and would otherwise restart the new image and migrate again. B1 is a REST image, so it reaches and leaves an installation the same way; REST keeps no schema of its own, so its way back needs no data restore. Studio and postgres-meta (A6) are delivered the same way, as a committed version installed with `lab/upgrade.py start` and left with `lab/upgrade.py rollback`; they start on demand from the lock, so no running container is replaced, and they need no data restore. A lock edited in place on an installed server leaves a local change, and `upgrade.py` then refuses every later start and rollback, so it is never done there. A8 changes only the stock fixture image in `lab/images.lock.json`, so its way back is reverting that lock change in the repository. The PostgreSQL image is refused by `upgrade.py` and reaches an installation only through P1 (A7).

   **Editing a lock by hand.** When a version already has an entry (a deferral, a retry after a rollback, or a later rebuild of a moving tag such as `17-alpine`), `stage` refuses it. The existing entry first gains a dated section in the shape `stage` writes: the exact new pin with its `digests` proof, and a `## Rollback` block with the exact pin it replaces, so the rollback pin is recorded as policy rule 6 requires. The lock is then changed by hand to that new pin, and checked with `lab/pin_update.py verify` before the commit.
5. **Independent review.** Each adoption gets an adversarial reviewer with a fresh context. Old evidence is never transferred to a new pin.
6. **Fixed container names.** One placement per Docker daemon, because container names are installation-wide constants. Live runs happen on the owner's lab host or the rehearsal VM, never two at once.

## 4. Continuous tracking

| Cadence | What | How |
|---|---|---|
| Weekly, automatic | Drift of every pin against the newest release in its line, the newest overall, the compose pin, and digest drift under the pinned tag | `lab/upstream_watch.py` in `.github/workflows/upstream-watch.yml`. Exit 3 means drift that needs action: a release without a review entry, or a digest that moved under a pinned tag, which only restaging clears. Exit 2 means a lookup failed. While known drift is pending the job stays red, so the maintainer compares each row with the previous week's JSON artifact: a changed newest version or digest is new drift. The workflow runs only after it is pushed |
| Within 7 days of a PostgreSQL or Auth security release | A review entry and a decision | Security fast track. A deferral needs a written reason. The 17.11 security release (2026-09-25) predates this cadence; the Select 2026 review is its first entry, and A7's analysis entry carries the decision |
| Whenever a release is deferred | A dated entry named the way `stage` names it, `docs/upstream/YYYY-MM-DD-<label>-<image>-<version>.md`, whose Decision says defer and why | Every deferral a review decides gets its entry in a named step (A1 for REST v16, A8 for `postgres:18-alpine`). The tracker then counts that release as reviewed. A digest that moved under the pinned tag is not settled by any entry: only restaging the new digest clears it (A8 for the stock fixture), so the weekly job stays red until that is done |
| Before a step that cannot be staged yet | An analysis entry under a name `stage` does not use, such as `docs/upstream/YYYY-MM-DD-<label>-<version>-analysis.md` | Keeps the per-release name free, so `stage` can still write the real entry when the prerequisites are met |
| Monthly | The Supabase changelog filtered for self-hosting, and the compose file's history | Read by the maintainer and recorded in `docs/upstream/` when it changes a surface |
| Quarterly | The watch list in section 7, against its triggers | A short entry per item, even if nothing moved |
| Later checkpoint T2 | A vulnerability scan of every pinned image digest (Grype or Trivy), and a host timer that feeds drift into the operator notification outbox | Separate change with its own tests; no secrets in the workflow |

## 5. Adoption order

Five tooling prerequisites gate the riskier steps, because the plan's own rollback rules depend on them; each names the steps it blocks:

- **P1, a delivery path for the PostgreSQL image.** Two refusals stand in the way today, and P1 covers both:
  - `lab/migrate-generation.py` recreates a database container only on the image recorded in its generation pin, and `validate_replacement` in `lab/hba_migration.py` refuses an image change as a change of database identity. P1 extends it: the replacement image comes from the target distro lock, the volume and owner stay the same, and the crash tests cover the new path.
  - `lab/upgrade.py` refuses any target version whose distro lock differs (`release_channel.database_changes`), and `lab/durable_runtime.py` refuses to start when the running database image differs from the lock. P1 adds one attended flow that moves an installed checkout to such a version and runs the generation change in the same maintenance window, and the same flow back to a version on 17.6.1.166 for the return.

  `lab/upgrade.py rollback`, and the console's roll back action, check only the upgrade phase, local changes and the catalog and key store schema, not the database image. After a generation change, a stale upgrade record could move the checkout back to a version locked to the other image while the database runs the new one. P1 is accepted only when rollback refuses to cross a database image change, or when P1 writes its own upgrade state so that rollback targets the right version.

  Every Sbarbase version released after A7 carries the new distro lock, so an installation still on 17.6.1.166 can take it only through this flow. That is why A7 runs last, after A8, which is independent of it. P1 blocks A7.
- **P2, a backup of the management database.** `lab/backup.py` covers environment databases and `storage_metadata`, not the `management` database that holds the management realm's Auth. P2 adds its backup and restore, with a restore drill. P2 blocks A4 and A7. (Code reviewed in `b6f5f6a`; see section 11.)
- **P3, restore into a fresh cluster on a chosen image.** `lab/backup.py` restores into the running cluster, so after A7 it would restore onto 17.11, and its per-database dumps carry no cluster roles. The fixture prototype pair `lab/recovery-export.py` and `lab/recovery-restore-db.py` (see [INDEPENDENT-RESTORE](../INDEPENDENT-RESTORE.md)) already captures one environment into an encrypted export with its scoped roles and restores it into a fresh pinned cluster. P3 turns the pair into a supported export and restore of the whole installation (every environment, the management database and `storage_metadata`, with cluster roles and scoped logins) onto the 17.6.1.166 image, rehearsed in the VM. P3 blocks A7.
- **P4, a Storage way back when the previous image cannot start.** The [upgrades guide](../../guides/upgrades.md) records that if the previous Storage image cannot start on a migrated `storage_metadata`, the previous version does not start (`rollback_failed`), and the `storage_metadata` restore is unit tested only. A5 crosses v1.80.0, so this case is likely. P4 adds a supported restore of `storage_metadata` and the environment databases with the database running and Storage stopped, proven by a live drill that migrates forward, fails back and restores. P4 blocks A5.
- **P5, a supported write pause.** The rollbacks above restore backups and so discard writes made after them, which is safe only while application writes are paused. No supported pause exists today: the upgrade hold releases application traffic as soon as health checks confirm the upgrade, before the baseline post-checks, and the internal routing maintenance flag set through `lab/routing-operator.ts` is undocumented and affects only gateway traffic, while `src/http/database-proxy.ts` keeps accepting writes on the direct database port. P5 adds one documented operator pause that stops writes through the gateway and the direct database port for chosen environments, holds until the operator releases it after the post-checks, and is proven by a live check that writes on both paths are refused while it holds. P5 blocks A3, A4, A5 and A7.

Patch and minor updates within each line come next. They are the lowest risk, and they prove the pipeline before the larger steps. Every step is one change, gated by section 3.

| Step | Component | From, to (as of 2026-10-14) | Why this position | Specific checks |
|---|---|---|---|---|
| A1 | REST | v14.15 to v14.18 | patch line upstream still maintains; compose is already on v14.17 | supabase-js lifecycle, embedded resource filters, schema cache reload. A1 also writes the deferral entry for `postgrest` v16.4 (B1's reason), so the REST row does not stay red after A1 |
| A2 | Edge Functions | v1.77.0 to v1.77.4 | patches only | deploy, invoke, the 150 s wall clock, JWT check in `lab/functions/main/index.ts` |
| A3 | Realtime | v2.138.1 to v2.143.x | grants fix on `realtime.messages` (v2.138.6) and JWKS-only tenants (v2.140.4), which F1 needs | its schema migrations, a replication slot per environment, Postgres Changes and Broadcast through the gateway |
| A4 | Auth | v2.196.0 to v2.197.0 | management realm and environment Auth share the image | MFA fixture, sign-in providers, SMTP, SDK revocation retry |
| A5 | Storage | v1.73.1 to the newest stable release when staged (v1.83.0 by the evening of 2026-10-14) | one shared tenant-aware process, the widest blast radius among the services | needs P4; tenant migrations on every environment, the "ahead migration" rule from v1.80.0, uploads, signed URLs, the restore pause |
| A6 | Studio with postgres-meta | 2026.09.07 to the newest dated build, v0.99.0 to v0.100.0 | brings the Database Connections viewer | **F5 below**: the viewer must not show or cancel another environment's sessions. Also record whether Explorer and Notebooks work without the hosted log pipeline, and confirm that the upstream default changes (Studio's role moving to `postgres`, Analytics and Vector opt-in) do not affect our scoped Studio login or our stack without Analytics |
| A8 | Stock PostgreSQL fixture | 17-alpine, new digest | the tag was rebuilt upstream | the image also runs `lab/management-mfa-fixture.py` (part of the baseline for Auth changes), `lab/pressure-response-check.py`, `lab/partial-database-crash-check.py` and `lab/run.py`, so these run before and after, and the baseline is recorded again after A8 |
| A7 | PostgreSQL distribution | 17.6.1.166 to 17.11.0.x | five PostgreSQL minor releases of fixes; the riskiest step | the P1 flow only, never another path; `lab/distro-check.py` hardcodes 17.6.1.166 and rewrites the distro lock when run, so it is not used as A7 evidence, and is not run on an installation after A7, until P1 changes it to read its image from the lock; extensions, HBA, bootstrap SQL, backup, restore, a recreation probe; the four actions of upstream's [17.11 breaking-changes notice](https://supabase.com/changelog/postgres-15-19-17-11-breaking-changes), run in every environment database, the management database and `storage_metadata` (see below). We would run ahead of compose, so the coordination noted in `COMPLIANCE.md` applies |
| B1 | REST major | v14 to v16 | after A1 and a passing supabase-js compatibility run; compose moving to v16 starts that run, it does not replace it | `url-use-legacy-target-names`, the `Vary` header, JWT role extraction, graceful shutdown |

**A7 and the 17.11 notice.** Upstream lists four checks, each with a detection query; no rows means no action:

- ltree: indexes affected by case folding or by label-count overflow are rebuilt with `REINDEX INDEX CONCURRENTLY`.
- pgcrypto (CVE-2026-14663): values encrypted with the Blowfish or CAST5 ciphers are re-encrypted with AES before A7, because after the upgrade their decryption fails by default (it then needs the `ignore-cipher-failure=1` option). AES reads on both 17.6 and 17.11, so the way back still works. Secrets stored that way are rotated. Public-key messages cannot be scanned, so they count as affected if those ciphers were ever used.
- btree_gist: `gist` indexes on float columns that may hold `NaN` are rebuilt.
- Custom operators (CVE-2026-2004): operators outside extensions with a non-built-in selectivity estimator keep working, but recreating them through dump and restore fails without a superuser. They are recreated with a built-in estimator before the step.

The entry records each query's result per database. Any rebuilt index counts as a rebuild for rollback step 2 below, and the custom operator check also gates the P3 restore in step 3.

A7 carries the security value, so the owner may move it earlier (section 9). Its analysis entry is due by 2026-10-28, a proposed date the owner may change, written under the analysis name from section 4 so that `stage` can still write the release entry once P1, P2, P3 and P5 are done.

**A7 rollback.** A generation change only moves forward: `lab/migrate-generation.py` archives the retired generation, removes the retired container by its exact id and gives the replacement the same fixed name. The data volume is what survives. The way back is therefore planned before the step runs:

1. Record the retired container's full identity and HBA digest (the tool archives both), and take two sets of backups in a maintenance window with writes paused through P5 from before the backups until the post-checks pass, because restoring the P3 export discards every write made after it: the ordinary `lab/backup.py` set of every environment, the management database (P2) and `storage_metadata`, and the P3 encrypted export of the whole installation with its roles. Both are verified by a restore in the rehearsal VM; the P3 export is the input to step 3.
2. Rehearse the whole step, and its return, in the VM first. PostgreSQL minor releases share the on-disk format, so a second attended generation change through P1 back to the 17.6.1.166 image on the same volume is possible only if the new image did not upgrade extensions or rewrite system catalogs. The entry records which extensions changed version and whether any catalog or index was rebuilt.
3. If any extension version or catalog changed, the only way back is restoring the P3 export from step 1 into a fresh 17.6.1.166 cluster through P3. The entry says so before the step starts. Indexes rebuilt on 17.11 for the notice (ltree, btree_gist) are rebuilt again with `REINDEX` after a same-volume return, because the two versions can order those values differently; the step 2 rehearsal includes that rebuild, and if it is not rehearsed, P3 is the way back.

## 6. New capabilities (feature checkpoints)

| ID | Capability | Depends on | Contract |
|---|---|---|---|
| F1 | Asymmetric signing keys per environment | A3, A4 | Auth signs with an asymmetric key published as JWKS. REST, Storage, Realtime, the Functions main service and the gateway verify through it. A dual-key window migrates existing shared-secret environments. Rotation and revocation keep the semantics of the `signing_keys` table. Rollback: during the dual-key window every service still accepts the shared secret, so an environment returns by signing with it again; the window closes only after a recorded check that no client still needs it |
| F2 | Optional OAuth 2.1 server per environment | F1 | `GOTRUE_OAUTH_SERVER_ENABLED` per environment, off by default. Dynamic client registration off unless the owner turns it on. The consent page belongs to the tenant's app. Discovery routes pass through the gateway without a wildcard CORS answer. Rollback: turning the setting off per environment stops new grants, and the tokens it issued are revoked |
| F3 | Supabase's app MCP server pattern on Sbarbase | F2, A2 | Deploy the official MCP server block as an Edge Function in a test environment, and pass an MCP client run with row level security enforced. Publish what works within the 150 s limit |
| F4 | Agent access stage 4 input | agent access stage 0 | Whether stage 4 uses GoTrue's OAuth 2.1 server mode or its own thin authorization server is decided by the agent access plan (being drafted separately) in its stage 0 spike (D12), which requires a signing key and issuer separate from the management realm. F2 findings are shared with that spike; they do not decide it |
| F5 | Connections viewer isolation | A6 | Live proof on the shared cluster that a scoped Studio login sees and signals only its own environment. If not, the page is hidden for that build and the reason documented. Session metadata of other roles stays readable through `pg_stat_activity` in the SQL editor either way; this is recorded as a known limit, and cancelling stays impossible without `pg_signal_backend` |
| F6 | Health signals | none | Error rates per service from the existing gateway counters, shown in the console and sent through operator notifications. This is the self-hosted counterpart of Health Check Advisors |
| F7 | Supabase CLI compatibility | none | Pin a CLI version as the policy requires. Test `db push --db-url`, declarative sync with `pg-delta`, and `gen types` against the direct database port. Write a guide |
| F8 | Elicitation as an extra prompt | agent access stage 3 | Sent only where the client declares support, and never in place of plan and apply or the human approval for `destroy` |

## 7. Watch list and triggers

| Item | Re-evaluate when | Why not now |
|---|---|---|
| OrioleDB | it leaves beta in `supabase/postgres`, and logical replication (Realtime) and our extension set are documented as working | beta, B-tree indexes only, chosen when a cluster is created, so a whole generation change |
| Multigres | a beta with documented self-hosting exists, and our single-server soak is done | private alpha; three nodes per cluster, against the four-environment guard and unmeasured memory ceilings. Its built-in pooler is relevant to borrowable connections |
| Turso and agent databases | Supabase ships a self-hostable product | no product; SQLite, not one of the original Supabase services |
| Supabase Compute, Pipelines | a self-hostable release | hosted only |
| PostgreSQL 18 (the stock fixture's `18-alpine` line, and a future distribution major) | the Supabase distribution ships a PostgreSQL 18 line and the official compose file pins it | the stock fixture follows the major line we run (17), so the fixture probes test the same major; a major upgrade is its own generation change and plan. A8 writes the deferral entry for `postgres:18-alpine` with this reason, so the tracker counts it as reviewed |
| Native CLI runtime | it becomes the default | a developer tool; it does not change servers |

## 8. Hosted projects outside this repository

The owner's projects on Supabase Cloud, which are not listed here, gain nothing automatically. The useful actions are:

- Replace any classic personal access tokens in CI and MCP configuration with scoped tokens, read-only where possible.
- Update the Supabase CLI where it is a dev dependency, then try `pg-delta` in the migration workflow.
- Turn on Health Advisors and Explorer per project.
- Use OrioleDB only for new projects; an existing project cannot switch.

## 9. Owner decisions

1. Start P1 to P5 and A1 on the lab host.
2. Studio and postgres-meta as two changes, as rule 5 requires today, or amend rule 5 first to allow this one pair.
3. When A7 runs. It is the security fix, and it puts us ahead of the compose bar.
4. When B1 runs.
5. Whether new environments default to asymmetric keys once F1 passes, and the migration window for existing ones.
6. Push the upstream watch workflow, which activates it on GitHub.

## 10. Acceptance and limits

A step is accepted only with its review entry complete, the baseline comparison clean, live checks passed on the named host and an independent review without must-fix findings. A passed lab run says nothing about a real server or a provider not tested. The pin table in the policy is updated in the same change as each adoption.

## 11. Progress

Updated with each step. Code accepted here has unit tests and an independent review; nothing below has run on the lab host or a real server yet, so no step is accepted under section 10.

| Item | State | Commit | What is still open |
|---|---|---|---|
| P1, rollback across a database image | code reviewed | `fadc66f` | `lab/upgrade.py rollback` refuses to cross a PostgreSQL image change |
| P1, update check and distribution probe | code reviewed | `fadc66f` | the channel check names its direction; `lab/distro-check.py` reads the lock and never writes it |
| P1, migration during an unconfirmed upgrade | code reviewed | `5044dd7` | `lab/migrate-generation.py` refuses while an upgrade or rollback is unconfirmed |
| P1, image change and the attended flow | code reviewed | `a21fd36` | the live move in the rehearsal VM; no exit from a stuck move until P3 |
| P2, management database backup | code reviewed | `b6f5f6a` | the live restore drill |
| P4, database alone for a restore | code reviewed | `2a7e914` | the live drill: migrate Storage forward, fail back, restore |
| P5, write pause | code reviewed | `cc6f888` | the live check that both write paths are refused |
| A1, PostgREST v14.18 (v16 deferred) | staged | `c299a01` | the live gate |
| A2, Edge Runtime v1.77.4 | staged | `5498a61` | the live gate |
| A3, Realtime v2.144.0 | staged | `a229d3f` | the live gate |
| A4, Auth v2.197.0 | staged | `1edeb56` | the live gate; its way back restores the management database too |
| A5, Storage v1.83.3 | staged | `87e6fcf` | the live gate; one-way, the way back is the P4 restore |
| A6, Studio 2026.10.12 | staged | `5566be2`, `48d4840` | the live gate, with F5: the Connections page shows and cancels only its own login's sessions |
| A6, postgres-meta v0.100.0 | staged | `beb8b03`, `0b2c428` | the live gate; postgres-meta is reachable from the shared network while a session runs, a separate fix |
| A8, postgres:17-alpine (17.11), 18 deferred | staged | `238a9c6` | the fixtures on the new pin |
| P3, whole-cluster restore | parts 1 to 3 reviewed; part 4 built, reviewed seven rounds, one gap open | `e520c83`, `dbcb82d`, `35625c5`, `5322fb5`, `46c0afe` | the exit when the takeover's own replacement never becomes ready ([design](../WHOLE-CLUSTER-RESTORE.md#limits)); the VM rehearsal |
| A7, B1, features | not started | | A7 waits for P3 and its VM rehearsal |

Staged means pinned with its review entry and the full unit suites passing; no A step has passed its live gate. When releases are cut, each A step needs a release of its own whose `minimum_from` is the release just before it, so that one upgrade never crosses two upstream components: the P4 procedure and the per-step way back rely on that.

The live drills wait for host memory: the rehearsal VM needs about 9 GiB free.
