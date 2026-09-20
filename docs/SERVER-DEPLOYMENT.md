# Server deployment

Runbook for deploying a sbarbase installation to a Linux server. Status:
2026-09-20. The deployment path is implemented: preflight, installer, systemd
supervision, a one-command rehearsal and this runbook. The full source and target
lifecycle rehearsal passes on the development host (10 of 10 checks,
`docs/evidence/deployment-rehearsal.json`). It has **not** been run on a real
server, and an independent adversarial review of the deployment code found
defects that are now fixed
([review](reviews/target-and-deployment-review.md)); the fresh target and
retained adoption paths are proven on disposable fixtures and on the retained
databases, not through a full restore or install. Read
[DEPLOYMENT-READINESS](DEPLOYMENT-READINESS.md) for the itemised status.

## Prerequisites

| Requirement | Why |
|---|---|
| Linux x86-64 host with Docker (native daemon, not remote) | every placement runs pinned containers |
| Bun on PATH (for a system service, add its directory to the unit's `PATH`, e.g. `/home/sbarah/.bun/bin`) | package manager, console build, gateway checks |
| `/usr/bin/python3` 3.14 or newer | the lab runtime uses modern f-strings |
| Git checkout of this repository | state and lock files live in the checkout by default |
| Headroom: the preflight states the exact figure it needs and refuses below it | `CombinedAdmission` and `ResourceAdmission` measure the host; the requirement is the combined placement (5888 MiB of container limits) plus a 2560 MiB reserve, plus, on an installation that has been moved, the measured cost of the already-running source stage recorded in `docs/evidence/source-stage-footprint.json`. The preflight prints the composition, so a refusal names each term |
| Docker socket access for the service user | the supervisor starts and stops owned containers only. The unit reaches the socket its Docker context resolves to; a host whose context points elsewhere (a Docker Desktop socket, for example) must forward `DOCKER_HOST` in the unit, and the preflight names the endpoint it tried when the daemon is unreachable |

Pinned images are pulled by digest on install; no floating tags are used. See
[upstream update policy](UPSTREAM-UPDATE-POLICY.md) before changing any pin.

## Install

One command on the server covers prerequisites, preflight, the full rehearsal and
the acceptance evidence:

```
deploy/server-acceptance.sh --bootstrap-file /path/to/operator.json
```

It refuses before touching anything when a prerequisite is missing (docker, bun,
git, `/usr/bin/python3` 3.14+, native Linux daemon), when the bootstrap file is
not mode 600, or when the preflight reports a blocker, and it never prints a
secret. Without `--rehearse` it stops after a passing preflight. The step-by-step
sequence below is what it runs, for an operator who wants to drive each stage by
hand.

```
/usr/bin/python3 lab/install_server.py check        # read-only preflight, non-zero on blockers
/usr/bin/python3 lab/install_server.py plan         # print the exact steps
/usr/bin/python3 lab/install_server.py install --bootstrap-file /root/sbarbase-operator.json
```

`install` performs, in order: preflight, private state and secret directories
(0700), pinned image pull when not local, `bun install` when needed, console
build, owned runtime startup, and the operator identity bootstrap. The
bootstrap file is a 0600 JSON object with exactly `email`, `password` and
`organization`; it is piped on stdin and never passed as an argument or printed.

A fresh install initializes exactly one HBA generation for the new database. A
retained installation (containers already present) is refused until its source
and current recovery target carry generation pins:

```
/usr/bin/python3 lab/adopt-retained.py source
/usr/bin/python3 lab/verify_retained.py source
/usr/bin/python3 lab/adopt-retained.py target
/usr/bin/python3 lab/verify_retained.py target
```

Adoption starts and stops only the captured database container and preserves its
existing rules byte for byte. Never delete a pin, journal or receipt to bypass
the gate.

## Supervise

Install and start the unit with one command. It renders the shipped unit for this
installation (paths, service user, Bun directory), verifies the result with
`systemd-analyze verify`, then installs, reloads, enables and starts it:

```
sudo /usr/bin/python3 lab/install_server.py supervise --apply
```

Without `--apply` it only renders and verifies, prints the exact commands it would
run, and writes `docs/evidence/supervisor-unit.json`. It refuses to install a unit
that does not verify and refuses `--apply` without root. Point it at a different
layout with `--service-user`, `--home` and `--bun-dir`; the shipped unit is never
hand-edited. The rendering has been verified on the development host and the
install commands are recorded in that evidence file; the install itself needs root
on the target server.

The unit runs `lab/dev.py`, which builds the console, starts the owned runtime,
runs the API and the provisioning worker, and stops the runtime on SIGTERM.
`ExecStartPre` re-runs the preflight, so an unfit host fails before any
container is touched. For a foreground run instead, execute
`/usr/bin/python3 lab/dev.py` in a terminal and stop it with Ctrl+C.

On the server, one command produces the acceptance evidence:

```
/usr/bin/python3 lab/install_server.py check
/usr/bin/python3 lab/deployment_rehearsal.py --bootstrap-file /path/to/operator.json
```

On a server where the unit still has to be installed, run the acceptance path as
root with `--install-unit`: it renders and verifies the unit, installs and starts
it, proves the console and the TLS termination, runs the rehearsal with the unit
required, and leaves the evidence in one place.

```
sudo deploy/server-acceptance.sh --rehearse --install-unit \
     --bootstrap-file /path/to/operator.json
```

`docs/evidence/server-acceptance-rehearsal.json` then records the host facts
(kernel, Docker, Bun, Python, headroom, free disk), the exact command that ran,
the full pin set with digests, the state of the `sbarbase.service` unit, start and
finish times, and one row per check with its result. A rehearsal that cannot run
records the blocking finding instead of a pass, and exits non-zero. The plain
rehearsal (without `--require-unit`) writes `docs/evidence/deployment-rehearsal.json`,
so an acceptance run never overwrites it.

### Check: does the supervised path work, not just a direct run?

The rehearsal runs the supervisor directly. It records the systemd unit's own
state, and fails the `supervised path exercised through systemd` check when
`/etc/systemd/system/sbarbase.service` is not installed, so a green run means
the unit was present, enabled and verified.

## Verify after install

- `lab/console_build_check.py` rebuilds the console and proves the served page is
  intact (non-empty index, every local asset it references present and hashed).
  The installer now refuses a build that produces an unusable page, and the
  rehearsal records `built console page is intact` before it starts anything.
- `lab/pinned_images_check.py` proves every pin is present locally and resolves
  to the pinned digest. It never pulls, and a tag that now points at a different
  digest fails instead of passing.

```
/usr/bin/python3 lab/install_server.py smoke         # management Auth, per-environment routes, console pid
bun lab/combined-gateway-check.ts                    # 14 simultaneous gateway checks (needs the console built)
bun lab/combined-supervisor-check.ts                 # full rehearsal: start, checks, supervised shutdown
/usr/bin/python3 lab/deployment_rehearsal.py         # one command: install, supervise, verify, shut down, evidence
/usr/bin/python3 lab/target_placement_rehearsal.py   # retained target placement: start, probes, stop
```

`deployment_rehearsal.py` writes `docs/evidence/deployment-rehearsal.json` and
exits non-zero on any failure; on a host without the required headroom it
records the preflight refusal and stops without starting anything.
`target_placement_rehearsal.py` exercises the adopted recovery-target placement
end to end and returns it to the paused, stopped state.

The smoke command reports each endpoint status and does not modify state. The
supervisor rehearsal additionally requires the same headroom as a normal start.

## HTTPS and network exposure

The console and the gateway bind loopback and are reachable only through the
host. Terminate TLS in a reverse proxy and keep the Docker networks internal: the
installer creates no published ports. Do not expose the management Auth endpoint
or the provisioning API directly. Set the public URL the console should advertise
in the proxy, not in the console build.

A reference termination ships with the repository and is exercised by the test
suite (`bun lab/tls_termination_check.py`, 15 checks,
`docs/evidence/tls-termination.json`). It needs only Bun and a certificate:

```
bun deploy/console-tls-proxy.ts \
    --cert /etc/letsencrypt/live/console.example.com/fullchain.pem \
    --key  /etc/letsencrypt/live/console.example.com/privkey.pem \
    --public-host console.example.com \
    --https-port 8443 --http-port 8080
```

It refuses to start unless the certificate and key are regular files, the key is
not group or world readable, and the upstream is loopback (it takes the console
URL from `.lab/upstream/server.json` when `--upstream` is omitted). It answers
plain HTTP with a 308 redirect to HTTPS, adds `Strict-Transport-Security`,
`X-Content-Type-Options`, `Referrer-Policy` and `X-Forwarded-Proto`, and logs only
method, path and status: never bodies, query strings, cookies or credentials. An
operator may prefer nginx, Caddy or the platform proxy; the checks above state
which behaviour any replacement must keep.

## If a restore is interrupted

A failed restore leaves `.lab/upstream/recovery-target.json`, and a plain rerun
refuses because the descriptor exists. Do not edit that state by hand:

```
/usr/bin/python3 lab/recovery_reconcile.py        # stop the retained containers
/usr/bin/python3 lab/retire_recovery_target.py --reason "why it failed"
/usr/bin/python3 lab/recovery-restore-db.py       # now a fresh restore may run
```

Retirement refuses unless the descriptor's status is `interrupted`, `failed` or
`cleanup-failed`, every container of that prefix is stopped, and no per-target
HBA operation is pending. It archives the descriptor into
`recovery-target-history/`, records the path in the cutover journal, and removes
the active descriptor; containers and volumes are never deleted. A target that
already holds a usable database can instead be adopted in place with
`lab/adopt-retained.py target`.

## Backup, upgrade and rollback

- Backup: stop the supervisor, then back up the `pgdata` volumes plus the
  private state directory. The encrypted export and independent-restore path is
  documented in [INDEPENDENT-RESTORE](INDEPENDENT-RESTORE.md); it is the only
  restore path with recorded evidence.
- Upgrade: follow [UPSTREAM-UPDATE-POLICY](UPSTREAM-UPDATE-POLICY.md): read the
  upstream changelog, write a dated entry in `docs/upstream/`, adopt one
  component at a time, run the full Python and Bun suites plus the live checks,
  and record the rollback pin before starting.
- Rollback: restore the previous pin, then restart the supervisor. Data
  migrations are the operator's responsibility and must be recorded in the same
  entry.

## Known limits at this revision

- No end-to-end install rehearsal has been run on a real server yet; the
  preflight, the runtime startup, adoption and verification are each proven
  separately in the evidence files under `docs/evidence/`.
- No production capacity claim: 5888 MiB and 5.75 CPUs are configured ceilings,
  not measured peak demand. Sustained mixed load and 10/100-project capacity are
  unproven.
- Realtime, Functions, the connection pooler and cron are not implemented.
- Off-host restore, multi-host coordination and automatic upgrades are out of
  scope for this revision.
- The bootstrap flow has no invitations, MFA or rate limiting.