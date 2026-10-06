# Portable source verification

Updated 2026-10-06: complete current-code verification passed 1449 tests with no failures, errors, warnings or skips and independent actual review. Repairs cover SQLite and HTTPError response closure, tempfile ownership and expected test diagnostics. All 52 preparation cases also passed; native PostgreSQL startup, Cron/Vault restart continuity and physical restore remain unaccepted. See [current status](../../docs/reference/status.md) for CI results and evidence scope.

The separate public55 V3 role has actual MET: 25 component and 30 pump tests passed, original 290 and added 4 actual mode/byte captures matched, and all three known helpers were removed with no unresolved resources. Public BusyBox V8 help metadata also has actual MET: fixed-path Bash 5.3.3 and BusyBox 1.37.0 help returned zero, with its one helper removed. Neither result accepts private request operation, timeout timing/signal behavior, installed binary source provenance, candidate startup, cold/warm Vault or Cron continuity, physical restore or production.

The preserved warning-window V4 negative run reported 1447 ordinary tests OK but six ResourceWarnings, native 1 and observed window 839 for every warning. Three records have exact sqlite3.Connection type; three report tempfile.py line 484 with NoneType source. Observation windows are not allocation causes, and the three tempfile origins remain unknown. Fresh complete source/identity/mode/full bindings, final-revision CI and manually dispatched empty-host acceptance remain required; published 0e745394 CI and Website results retain only their earlier revision scope.

From any checkout, run `sh deploy/verify/run.sh`. The host needs Docker with
Linux container support and a POSIX shell. Supply an optional output directory
as the first argument. The default is `.lab/portable-verification`. Each
invocation creates a fresh subdirectory, so old reports never represent a
failed new build. Image and container inspection bind evidence to that run.

The runner builds an isolated image with source baked into it, installs locked
Bun dependencies during the build, then runs offline as an unprivileged user.
The dependency layer copies only `package.json` and `bun.lock` before
installation, so source-only edits reuse locked dependencies from Docker's
build cache. Local `node_modules` directories are excluded from the context.
It mounts neither host paths nor a Docker socket. It publishes no ports and
removes only the container and image created by this invocation. Shared Docker
build caches are left intact.

Docker's init process reaps orphaned subprocesses during the test suite. This
keeps the fixed 512-process limit usable for later build stages. A Python
process as container PID 1 accumulated 470 zombies after Python discovery and
caused the subsequent Vite build to abort; the same build succeeded in a fresh
container. This was process exhaustion, not evidence that Node was missing.
Bun remains the package manager and JavaScript runtime in this image.

Checks run independently: project planning integrity, built-in Python unittest discovery, Bun tests, UI
typecheck, UI production build, and Python compilation. A failed check does
not hide the later checks. Empty Python discovery or any skipped Python tests
fails acceptance. Bun skip/todo results or zero passing tests also fail.
Complete logs, package versions, stage outcomes, a source content digest and
image inspection are copied to the output directory even when checks fail.

The Dockerfile-specific ignore file overrides the runtime image's minimal
context. It excludes secrets, private lab state, local agent configuration,
Git history, the context graph, dependency directories and generated output.
Do not place credentials in source files or in an output directory inside the
checkout outside `.lab`.

Ubuntu 26.04 and Bun 1.3.14 use immutable public multiarchitecture image
digests. `APT::Snapshot` freezes Ubuntu package candidates to
`20261002T000000Z`. Snapshot metadata for resolute, resolute-updates,
resolute-security and resolute-backports was checked before choosing this ID.
The minimal base lacks HTTPS trust, so a checksum-pinned `ca-certificates`
package from that same public snapshot supplies the bootstrap root bundle.
APT uses that bundle for HTTPS and verifies Ubuntu archive signatures normally;
update errors fail the image build. APT can fetch current repository metadata
to discover snapshot support, but package selection and downloads use the
configured snapshot. Updating either the snapshot or image digests requires a
new verification run. Python and image identity are included in the evidence. Do not infer another
architecture passed from one local run.

Sources reviewed on 2026-10-03:

- [Docker build context and Dockerfile-specific ignore files](https://docs.docker.com/build/building/context/)
- [Docker image digest references](https://docs.docker.com/reference/cli/docker/image/pull/)
- [Bun Docker installation](https://bun.sh/docs/installation#docker)
- [Ubuntu snapshot service and APT::Snapshot configuration](https://snapshot.ubuntu.com/)
- [Docker init process](https://docs.docker.com/engine/containers/multi-service_container/)
- [Bun script execution and Node shebangs](https://bun.sh/docs/runtime)
- [Python unittest discovery](https://docs.python.org/3/library/unittest.html#test-discovery)

This is source verification only. It does not exercise real Supabase services,
Docker runtime orchestration, systemd supervision, DNS, TLS, disaster recovery,
cross-server placement or availability. Those checks remain required for
release acceptance. Host kernel and Docker implementation still affect this
container's execution.
Recognized UI build/typecheck and Python compiler diagnostics fail source
verification even if the child command returns zero. Reports preserve the
child exit code and diagnostic log line numbers; later stages still run.
This recognizes conventional warning/error prefixes, Vite reporter markers,
Rolldown diagnostic codes and Python compiler warning classes, including ANSI
formatting. Terminal links/titles and other terminated control strings are
normalized while preserving raw logs and original newline counts; residual or
unclosed controls refuse. Expected warning/error output from unit-test scenarios is not
a build diagnostic. Other tool logs and unknown formats still require review
before broader acceptance.

## Native startup preparation entry point

The [native startup preparation contract](native-startup-preparation.md) describes the integrated material executor, shared contracts and independent daemon guardian. Its explicit prepare-only handoff cannot accept server startup. The [source map](../../docs/reference/project-layout.md#native-startup-preparation-sources) lists all eight public files and their focused 14/9/29 regressions. The earlier V9 initV2 run passed all 52 focused cases; its historical actual full run executed 1447 tests and is NOT_MET, with one failure and 15 errors. The full admission minimum is 1356, not an exact count.

Earlier mode, scratch and warning verification attempts remain historical refusals bound to their own source and date. Resource ownership and fixture diagnostics were repaired, and the earlier complete code verification passed 1449 tests at `066f5ec`. This does not transfer native startup or recovery evidence from another run. The [status history](../../docs/reference/status.md) preserves the earlier observations and their limits.

The failure-notification test captures only its expected failed-start invocation and requires the entire known stderr message. Process-boundary tests save and restore actual caller subreaper state through unittest cleanup; a deliberate nested failure covers both original states. The separately reviewed full baked helper enables init only for tests, requires actual Init=true, and keeps the original PID/resource caps. Historical failed packets are retained; passing component tests do not accept private pump, candidate Cron/Vault or physical restore.

The public source now includes [private_psql_producer.py](private_psql_producer.py) and [private_request_pump.py](private_request_pump.py), with 55 component tests. They define a finite private SQL completion protocol with fixed selectors, byte and deadline limits, closed outcomes and refusal on uncertain completion. The complete frozen 881-file public source run passed 1504 tests with no failures, errors, warnings or skips; independent actual review and root closure passed. See the [public source regression evidence](../../docs/evidence/private-pump-regression-2026-10-06.json). The last CI result, `6c86`, covers the baseline before these four additions. Installed native client provenance, private startup/session authority, the owned restart lease, Cron/Vault cold/warm continuity and full restoration remain UNRUN and unaccepted.

## Frozen public reference metadata

`sh deploy/verify/reference-run.sh verify` builds a separate Linux metadata image
and re-fetches the versioned public upstream source and immutable registry
metadata. `freeze` writes a proposed packet to the invocation's evidence
folder; it does not overwrite the distributed benchmark. The host still needs
only Docker and a POSIX shell. These commands require outbound public Git and
registry access, run unprivileged with no host mounts/socket or forwarded
credentials, publish no ports and remove only their owned container/image tags.
They do not pull service layers, execute upstream installation scripts or start
Supabase. Full logs and unsuccessful attempts remain in separate directories.

The metadata image copies Docker CLI, Compose and Buildx from a pinned public
Docker image. The source verification image remains offline and carries no
Docker CLI. Metadata collection covers core, optional and development Compose
models. Native parser diagnostics, inaccessible images or missing platform
proof prevent metadata acceptance even when partial inventories are retained.
Platform availability is separate from a passing service, migration or host
profile. A refused packet cannot stand in for a complete frozen benchmark.

Plugin paths were verified against the [official Docker CLI image source](https://github.com/docker-library/docker/blob/d576eb69d7bad654b934176e95644995aa85d8f8/29/cli/Dockerfile),
reviewed 2026-10-03. Public input identities and source-file checksums belong in
the packet; generated credentials never belong in it.

## Controller build input inspection

`sh deploy/verify/runtime-run.sh` builds the actual controller Dockerfile with
`--no-cache`, then inspects it offline as the image's unprivileged nobody
account. It overrides startup, mounts no host files or Docker socket and
publishes no ports. The probe checks snapshot/trust settings, Python and
cryptography, Bun/Docker versions, Git/SSH, process/filesystem tools, timezone
and an ephemeral release-signature roundtrip. It retains installed package
versions, baked-file hashes, full build logs and image/container identities.
Failures return nonzero. Only resources created by that invocation are removed.

The controller uses immutable public Ubuntu, Bun and Docker CLI indexes, the
same checksum-pinned HTTPS bootstrap and Ubuntu package snapshot as source
verification. APT may retrieve live metadata to discover snapshot support;
actual package selection/downloads must use the frozen snapshot. Package
inventory and download logs are separate evidence from recipe unit checks.
Updating an image digest or snapshot requires a new inspection and affected
runtime/service checks. Pinning inputs does not imply byte-identical OCI
output, architecture support, absence of vulnerabilities or a completed
Supabase installation. This check runs no supervisor or service startup.

## Public service-image identity inspection

Run `sh deploy/verify/identity-run.sh` from a public checkout with Docker and a POSIX shell. The command builds frozen source plus a pinned Docker CLI, then inspects the nine locked public images by exact repository-qualified digest on the selected local Unix daemon. Missing local images refuse; it never pulls them. It preserves native image records, requested references, daemon IDs, daemon/store metadata, raw logs and an exact source digest. Deliberately substituted repository and malformed-ID proof must also refuse. No Supabase service starts.

The owned probe runs without network or capabilities and binds only the selected local Unix Docker socket. It runs as root to access that socket. A readonly socket bind does **not** make the Docker API read-only; the source restricts itself to `image inspect` and `info`. This is an inspection workflow for a trusted local operator, not an untrusted-code sandbox or a remote-daemon profile. Host context selection determines which socket is bound; inaccessible or non-Unix endpoints refuse. Cleanup removes only the probe's fresh container and two image tags.

A pass establishes installer image-admission proof on that daemon. Fixture tests cover different shapes of daemon image IDs; they do not establish classic-store support. Durable launch, HBA ownership, Studio, upgrade and recovery paths still require their connected immutable-reference migration and actual trials before full runtime portability or G0 acceptance.

Installer absence classification is intentionally narrow: native inspection must return an empty JSON image list and one complete recognized English no-such-image line identifying the exact canonical requested immutable reference. Unknown/localized/new-version error formats refuse with their diagnostic rather than inviting a pull. The inspection command exercises this classification using one unused public reference without pulling it. Future engine-format support needs retained native output and negative ambiguity fixtures.

## Durable launch fixture

Run `sh deploy/verify/identity-run.sh .lab/durable-identity-verification durable`. This uses the frozen source image and pinned CLI to exercise actual `Runtime.launch` creation, retained reuse and database-image/owner drift refusal on one fresh Ubuntu fixture container and internal network. Ubuntu's exact public pinned reference and the ordinary PostgreSQL probe image must already exist locally; no image is pulled. Only the fixture runs, with a harmless true command; no PostgreSQL or Supabase process starts.

The probe uses fresh owner/network names and a private temporary directory inside itself. It filters fallback container listing by that owner, omits block-device IO flags without mounting a host disk, and adds fixture capability restrictions. Production memory/CPU/pids flags remain. This verifies image identity and lifecycle branches, not IO enforcement, HBA startup, full Supabase, classic-store support or independent hosts. Native command output is retained; warnings fail. Child cleanup and outer cleanup target only exact fresh resources. The socket/API privilege limitations documented above still apply.

The source hash helper is image-only. It requires the actual baked `/opt/sbarbase` source root and a root-owned public-tree marker installed outside the checkout by the verification Dockerfile. Missing or invalid proof refuses before source traversal. Do not call it on a contributor or production checkout; Dockerignore is applied when building the public image, not by the hashing loop. This prevents accidental host use, and does not sandbox arbitrary untrusted code.

## Studio identity fixture

Run `sh deploy/verify/identity-run.sh .lab/studio-identity-verification studio`. The probe first runs actual Studio image admission on the two already installed public lock references; missing images or errors refuse rather than pulling in the probe. It then runs actual Studio.launch on one Ubuntu sleep fixture in a fresh internal network, verifies its qualified image and address, and proves missing image proof leaves that fixture unchanged without attempted run/start/remove/pull.

Fresh owner/private paths, filtered fallback listing, fixture entrypoint/capability restrictions and omitted IO flags isolate the trial. Existing memory/CPU/pids limits remain. It does not run real Studio, postgres-meta, database or browser workflows. Native outputs and attempted commands are retained; warnings fail, and cleanup removes only the exact fresh resources. Socket privilege limitations still apply. Actual full up, classic-store/independent hosts and release acceptance remain separate.

### Upgrade image admission fixture

Run `sh deploy/verify/identity-run.sh .lab/upgrade-identity-verification upgrade` on a selected local Unix Docker endpoint. The distributed probe creates an owned temporary Git repository inside its public verifier image using only baked public lock files. Actual upgrade admission functions inspect already available exact public image references. Native Git controls distinguish genuinely absent optional paths from present null, missing core lock and unavailable commits before Docker inventory. No images are pulled, no Supabase services start, and no real checkout, backup, private state, upgrade or rollback changes occur. The only Docker lifecycle is the unique verifier container and its source/probe tags. Socket readonly does not enforce API permissions; trusted probe guards restrict commands to public image inspect. This grants no classic-store, full upgrade/recovery, G0 or release acceptance.

### Native data tools fixture

Run `sh deploy/verify/identity-run.sh .lab/data-tools-verification data` on a selected local Unix Docker endpoint with the already available locked public PostgreSQL and Storage images. The public verifier creates a fresh resource-bounded PostgreSQL container on an internal owned network, PostgreSQL data only in tmpfs, and a unique owned objects volume. No ports are published. Synthetic rows test actual Source query/read-only enforcement/plain dump restoration and native custom archive restoration; an actual Storage helper tar roundtrip tests one synthetic object. Missing owned objects volume must refuse before helper launch. This is not complete project/cloud import, production backup restoration, PITR, HA, supported-host or release acceptance. Socket readonly does not restrict Docker API permissions; trusted guards constrain the fixture to exact fresh resources and public image inspection. Unique source/probe images and every owned fixture resource are removed.

## Native backup workflow fixture

### Fenced restore verification modes

`sh deploy/verify/identity-run.sh .lab/fenced-session-verification session`
checks native archive metadata planning, one retained PostgreSQL target session,
its OID-bound connection fence applied through a separate `postgres` control
connection, ordinary and normal superuser connection refusal, and explicit
stage database OID allocation. The pinned PostgreSQL 17 capability uses the
intentionally undocumented superuser-only `pg_catalog.pg_nextoid` function and
the internal `CREATE DATABASE ... OID` option. Allocation does not reserve an
OID; collisions must refuse. A pin change requires renewed capability proof.
This is a prerequisite, not production cutover or crash recovery acceptance.

`sh deploy/verify/identity-run.sh .lab/fenced-files-verification files`
checks actual Node filesystem identity, rename, removal and successful fsync
on both parents after cross-directory rename. It uses an owned sleeping
Storage image container and objects volume. It proves mechanical operations
and refusal controls, not Storage API traffic or power-loss persistence.

`sh deploy/verify/identity-run.sh .lab/fenced-restore-verification fenced`
exercises the actual production backup restore entry points on synthetic
PostgreSQL data and owned object trees. The owned Storage container is really
stopped and restarted. Auth/REST lifecycle and HTTP/TCP readiness remain
explicit doubles. The production interruption matrix remains required; a
nominal pass or any component pass does not automatically accept fenced
cutover, full Supabase workers, deployment, portability or release readiness.
Every attempt retains source-bound results and must be reviewed in its exact
scope. Missing, failed or unrun required cases remain blockers.

`sh deploy/verify/identity-run.sh .lab/backup-workflow-verification workflow`
executes the actual backup v1 environment and Storage metadata create/restore
functions on an owned synthetic PostgreSQL installation. Database snapshot,
concurrent write, counts, custom-format archive, SQL renames, ACL and connection
limits, tar archives and file rollback are native operations. Verified archives
are admitted before replacing a single pg_restore or tar input with invalid
bytes to test actual command failure after state has moved. Failed restores
must recover the previous rows and files and must not write a success record.

Auth, REST and shared Storage service stop/start, endpoint publication and health
are explicit doubles. Four additional cases inject environment/Storage startup
and health failures after real data replacement, commit native post-cutover rows
and object writes, then execute actual complete_restore and verify preservation.
Private OID/container/archive-bound pending checkpoints, predecessor/retention
guards and repeated non-destructive completion are checked. Up to 64 exact named
helpers are permitted. Existing environment readiness checks Auth HTTP 200 only;
Storage readiness checks TCP only. Shared Storage/direct writer fencing and
earlier data-phase crash safety remain unresolved. This does not prove those
services restart, REST/Realtime or Storage API health, a full
Supabase installation restores, off-host recovery works, or G2 passes. The
fixture needs only already available pinned public images. It creates a fresh
bounded tmpfs database, an owned volume and internal network, with no host ports
or private data. Transient helpers are named, resource bounded and removed.
Archives, raw native command outcomes, failure injection and service double
records are retained. Only exact owned fixture resources are cleaned up.


## Original configuration preservation and configured effects

Run these declared fixtures separately against already available immutable public images:

```bash
sh deploy/verify/identity-run.sh .lab/native-config-preservation configpreserve
sh deploy/verify/identity-run.sh .lab/native-configured-effects configured-effects
```

`configpreserve` checks the original configuration and metadata through bounded owned baseline/seed/reader helpers. Its whole fragment budget is 180 seconds, including sixty seconds reserved for cleanup. `configured-effects` preserves that original setup, bootstraps the pinned original image, then verifies unique owned Cron writes and isolated native HTTP echo behavior across enabled, inactive and resumed phases. It admits only the source/file/GUC/image-bound closed routine model, including the exact seven vendor grants; it does not repair privileges to bypass a refusal.

The configured whole budget remains 780 seconds, split into 540 work and 240 cleanup, with ninety seconds for effects and at most twenty-eight cleanup operations. The verifier is limited to one CPU, 256 MiB and 64 PIDs; the database to half a CPU, 512 MiB and 128 PIDs; the HTTP helper to one quarter CPU, 64 MiB and 16 PIDs. No host ports are published. Fresh exact ownership, internal networks and named configuration volumes delimit the fixture. Missing original images refuse; the fixture does not automatically pull or change the old image pin. It does not use application data or retained production resources.

The wrapper binds only the selected local Unix Docker socket, with a read-only bind. That bind does not make Docker API operations read-only; these trusted fixtures create and clean only declared owned resources. Remote or inaccessible daemon endpoints remain refused. Raw command streams, samples, preservation frames and exact cleanup outcomes are retained under the unique output directory. Accepted configured effects are not proof of owned-job restart, native fencing, archive/key/object continuity, full native service recovery, patched security support, all-host portability, physical HA, PITR or production acceptance. See the [accepted scoped review](../../docs/engineering/reviews/2026-10-04-native-cron-effects.md).
