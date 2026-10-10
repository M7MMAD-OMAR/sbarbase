# Release candidate coordination, 2026-10-08

The owner requested coordination between project conversations and a stable
release before deployment to a real server. This is the shared coordination
record. Direct delivery to other Codex conversations is unavailable in this
session; no other conversation has acknowledged this record. The candidate
freeze below is proposed, not enforced or accepted by other writers.

## Candidate identity

Use `aad7b5403ad14140e9d9499668e863d7488ce15b` as the proposed verification
baseline. Package and release metadata both identify version `0.2.0`, which
remains a development snapshot. No stable tag, signed release or production
acceptance was created by this coordination task.

[CI run 37688338269](https://github.com/M7MMAD-OMAR/sbarbase/actions/runs/37688338269)
was queried from GitHub during this task. All four jobs completed successfully
on that exact commit: checks, python-floor, docker-install and
empty-host-acceptance. This supersedes the older published CI observation as a
candidate baseline, but does not establish machine reboot, complete recovery
to a fresh target or public TLS acceptance.

The inspected working tree additionally contains a one-line installer plan
message change in `lab/install_server.py`, plus untracked evidence directories.
They are not included in the published commit's CI result. Preserve them and
have their writers resolve inclusion before declaring the final candidate.

## Requests to the existing work owners

Use the owners in the [integration programme](../plans/2026-10-06-integration-programme.md).
For each workstream, report the source commit, pending source paths, required
repairs, exact evidence identity and unresolved production requirements.
An acknowledgement must come from the actual work owner; this record does not
substitute for delivery or confirmation.

| Workstream | Required release evidence | Current coordination status |
|---|---|---|
| Main integration | Final source assembly, immutable candidate identity, full checks and independent review | Awaiting owner acknowledgement |
| Host admission | Supported Linux host install, systemd startup and machine reboot preserving data | Awaiting owner acknowledgement |
| Recovery | Full restore to a separate fresh target, including databases, objects, configuration, secrets and functions | Awaiting owner acknowledgement |
| Management and lifecycle | Live authorization, transfer, deletion, interruption and neighboring environment safety | Awaiting owner acknowledgement |
| Deployment | Public DNS, HTTPS, certificate renewal, Auth callbacks, SMTP and browser verification | Awaiting owner acknowledgement |
| Database and native services | Live SQL and pooler workloads, migration, required Cron/Vault continuity and original services | Awaiting owner acknowledgement |
| Capability registry | Current-source acceptance coverage, with failures and missing requirements visible | Awaiting owner acknowledgement |

## Proposed release sequence

1. Gather owner acknowledgements and finish the scoped source imports.
2. Select one immutable commit for the candidate. Continue unrelated work on
   separate branches; serialize Docker fixtures on each daemon.
3. Run the full source checks and native installation checks on that commit,
   retain raw results, and obtain independent review on the same source.
4. Prove machine reboot and complete fresh-target recovery using disposable
   owned environments. Record any failure without replacing historical results.
5. Verify the real deployment's public DNS, TLS, Auth, email and browser flows.
6. Apply the project's complete release acceptance requirements, resolve all
   required unproven rows, and only then prepare the signed stable release.

Until those requirements are proved, the candidate has no production go-ahead.
Successful component tests alone do not authorize production data migration.

## Verification from this coordination task

Local observations and raw logs are retained in the
[verification packet](../../evidence/release-candidate-coordination-2026-10-08/summary.json).
They describe the working tree and their stated checks, separately from the
published commit's CI. No live Docker installation or real server was changed.

| Check | Observed result |
|---|---|
| Full Python suite without a reachable Docker daemon | 2118 tests, OK, no skips, exit 0 |
| Bun tests | 914 passed, 0 failed |
| Focused installer suite | 36 tests, OK |
| UI and control TypeScript checks | Both exit 0 |
| Documentation links | 6 tests, OK |

The full Python log preserves deliberately failing diagnostic fixtures; its
unittest result is successful. Hash comparison covers only the files named in
the packet, not an immutable whole-source snapshot. No independent release
review was performed in this task, and no other work owner's completion was
inferred from these checks.
