[العربية](server-deployment.ar.md)

# Server deployment

Runbook for deploying a sbarbase installation to a Linux server. Status:
2026-09-20. The deployment path is implemented: preflight, installer, systemd
supervision, a one-command rehearsal and this runbook. The full source and target
lifecycle rehearsal passes on the development host (11 of 11 checks,
`docs/evidence/deployment-rehearsal.json`). It has **not** been run on a real
server, and two independent adversarial reviews of the deployment code found
defects that are now fixed
([review](../engineering/reviews/target-and-deployment-review.md),
[second review](../engineering/reviews/deployment-tooling-review-2.md)); the fresh target and
retained adoption paths are proven on disposable fixtures and on the retained
databases, not through a full restore or install. Read
[DEPLOYMENT-READINESS](../reference/deployment-readiness.md) for the itemised status.

## Prerequisites

| Requirement | Why |
|---|---|
| Linux x86-64 host with Docker (native daemon, not remote) | every placement runs pinned containers |
| Bun on PATH (for a system service, add its directory to the unit's `PATH`, e.g. `/home/sbarbase/.bun/bin`) | package manager, console build, gateway checks |
| `/usr/bin/python3` 3.12 or newer, with `cryptography` | the lab runtime uses f-strings that need 3.12; `cryptography` encrypts recovery bundles and off-site backups |
| Git checkout of this repository | state and lock files live in the checkout by default |
| Headroom: on an empty server 4352 MiB available (1792 MiB for the database, Storage and management Auth containers plus a 2560 MiB reserve) and at least 2 CPU cores; more as environments are added. The preflight states the exact figure and refuses below it | The requirement is derived from the placement the next start runs: on an empty host the three system containers at their tier limits, afterwards every retained container at its own limits (each environment adds 512 MiB and 0.5 CPU of ceilings for its Auth and REST), plus the reserve, plus, on an installation that has been moved, the measured cost of the running source stage (`docs/evidence/source-stage-footprint.json`). CPU ceilings may add up to twice the cores after one core is kept for the host; the cgroup pressure gate refuses new work under real contention. The development host's retained split placement still needs 5888 MiB of limits plus the reserve. The preflight prints the composition, so a refusal names each term |
| A service account that exists, holding the checkout | the unit runs as that account (`User=`), so `--apply` refuses an account that does not exist instead of installing a unit that cannot start. The shipped default is `sbarbase`; name the server's account with `--service-user`, `--home` and `--bun-dir` (also forwarded by `deploy/server-acceptance.sh`) |
| Docker socket access for the service user | the supervisor starts and stops owned containers only. The unit reaches the socket its Docker context resolves to; a host whose context points elsewhere (a Docker Desktop socket, for example) must forward `DOCKER_HOST` in the unit, and the preflight names the endpoint it tried when the daemon is unreachable |

Pinned images are pulled by digest on install; no floating tags are used. See
[upstream update policy](../engineering/UPSTREAM-UPDATE-POLICY.md) before changing any pin.

## Install

One command on the server covers prerequisites, preflight, the full rehearsal and
the acceptance evidence:

```
deploy/server-acceptance.sh --rehearse --bootstrap-file /path/to/operator.json
```

Without `--rehearse` the same command runs the prerequisites and the preflight
only. It refuses before touching anything when a prerequisite is missing (docker, bun,
git, `/usr/bin/python3` 3.12+, native Linux daemon), when the bootstrap file is
not mode 600, or when the preflight reports a blocker, and it never prints a
secret. The step-by-step sequence below is what it runs, for an operator who
wants to drive each stage by hand.

```
/usr/bin/python3 lab/install_server.py check        # read-only preflight, non-zero on blockers
/usr/bin/python3 lab/install_server.py plan         # print the exact steps
/usr/bin/python3 lab/operator_file.py /root/sbarbase-operator.json   # prompts, no echo, mode 0600
/usr/bin/python3 lab/install_server.py install --bootstrap-file /root/sbarbase-operator.json
```

Create the file with `lab/operator_file.py` rather than by hand: it prompts for
the email, the organization and the password twice without echoing, refuses a
password shorter than 12 characters, a malformed email, a relative path, a
symlink and an existing file (unless `--force`), creates it with mode 0600, and
never prints the password. For automation it also accepts the same JSON on
bounded stdin with `--stdin`, so a password never reaches an argument or the
shell history.

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
sudo /usr/bin/python3 lab/install_server.py supervise --apply \
     --service-user ops-account --home /srv/ops-account --bun-dir /srv/ops-account/.bun/bin
```

`sudo` replaces `PATH`, so a Bun outside a system directory must be named with
`--bun-dir` or the unit starts without it.

Without `--apply` it only renders and verifies, prints the exact commands it would
run, and writes `docs/evidence/supervisor-unit.json`. It refuses to install a unit
that does not verify, refuses `--apply` without root, and refuses to install for an
account that does not exist on the host. Point it at a different layout with
`--service-user`, `--home` and `--bun-dir`; the shipped unit is never hand-edited.
`deploy/server-acceptance.sh` forwards the same three flags, so the one-command
acceptance path can name the server's account too.

The unit restarts the supervisor on failure, and also carries
`RestartForceExitStatus=42`: after an update or rollback from the console moves the
checkout, the supervisor stops everything cleanly and exits with code 42 so systemd
starts it again on the new version ([upgrades](upgrades.md)). A unit installed
before this line existed still restarts on that exit, because `Restart=on-failure`
covers it; reinstall it with `supervise --apply` when you move to that version.

Four things must be true before the unit can serve, and the preflight names each
one rather than failing obscurely:

1. **The service account exists.** Create it and give it the checkout, or install
   with `--service-user`/`--home`/`--bun-dir`.
2. **The service can reach a Docker daemon.** A system service does not inherit
   the operator's shell, so it uses the socket its docker context resolves to. On
   a server with native Docker that is `/var/run/docker.sock`, and the service
   account must be in the `docker` group. Where the account's context points at a
   desktop or per-user socket, point the unit at the system socket with a drop-in:
   `Environment=DOCKER_HOST=unix:///var/run/docker.sock` in
   `/etc/systemd/system/sbarbase.service.d/docker.conf`. One endpoint has to
   serve both the service and the acceptance run: the unit carries no
   `DOCKER_HOST` of its own, and the run's steps use the account's context, so
   name the same socket for the run with `--docker-host` (below) whenever the
   drop-in is needed. The preflight reports
   `Docker daemon unreachable from this process (tried <endpoint>)`, and when the
   daemon is unreachable it no longer guesses about pinned images or the existing
   containers.
3. **The host has the memory.** The next start needs the limits of the
   containers it runs plus a 2560 MiB reserve: 4352 MiB on an empty server, more
   with each environment, 8.8 GiB for the development host's retained split
   placement. The unit's `ExecStartPre`, the preflight and the runtime admission
   derive the same figure, and the gate refuses with `host_memory_headroom`
   rather than half-starting.
4. **Write access stays inside the checkout.** `ReadWritePaths` names the
   installation root only. A `ReadWritePaths` entry for a directory that does not
   exist makes systemd fail the unit with `226/NAMESPACE` before it runs anything,
   so the unit never lists a path the installation does not create; every secret
   lives in `<checkout>/.secrets/upstream`. The rendering has been verified on the development host and the
install commands are recorded in that evidence file; the install itself needs root
on the target server.

The unit runs `lab/dev.py`, which builds the console, starts the owned runtime,
runs the API and the provisioning worker, and stops the runtime on SIGTERM.
`ExecStartPre` re-runs the preflight, so an unfit host fails before any
container is touched. For a foreground run instead, execute
`/usr/bin/python3 lab/dev.py` in a terminal and stop it with Ctrl+C.

The one command that produces the acceptance evidence is
`deploy/server-acceptance.sh` (below); it runs the preflight, the checks and the
rehearsal in order and keeps the handoff copy. To drive the two stages yourself
instead:

```
/usr/bin/python3 lab/install_server.py check
/usr/bin/python3 lab/deployment_rehearsal.py --bootstrap-file /path/to/operator.json
```

The install itself needs the placement's memory plus the reserve (4352 MiB on an
empty server): the owned runtime refuses to start when the host cannot support the
placement containers plus the reserve, and names the reason (`host_memory_headroom`) rather
than half-starting. The installer releases the installation operation lock after
the console build and before it starts the owned runtime, because the runtime
takes that lock itself and holds it for its lifetime.

On a server where the unit still has to be installed, run the acceptance path as
root with `--install-unit`: it renders and verifies the unit, installs and starts
it, proves the console and the TLS termination, releases the unit so the rehearsal
can own the containers and state, runs the rehearsal with the unit required,
starts the unit again and asserts it is active, and leaves the evidence in one
place. systemd calls the unit active the moment the supervisor process starts,
long before the console can answer, so after every start of the unit the run
waits until the console answers over loopback, at most 300 seconds
(`--console-timeout` changes the bound), and fails with the last thing it saw
when it does not. The release happens whenever the unit is found active, with or without
`--install-unit`, and a trap starts it again on any exit, so a failure in the
middle of the run cannot leave the installation down. Two supervisors cannot own
the same containers, so the rehearsal never runs against a live installation.

```
sudo deploy/server-acceptance.sh --rehearse --install-unit \
     --service-user ops-account --home /srv/ops-account --bun-dir /srv/ops-account/.bun/bin \
     --bootstrap-file /path/to/operator.json
```

`sudo` replaces `PATH` with a secure default, so a Bun installed under the
invoking user's home is invisible to the script and to the unit it installs.
Pass `--bun-dir` (the script adds it to `PATH` and uses it in the unit) whenever
Bun is not in a system directory; without it the script stops at the
prerequisite step and names the flag.

`sudo` is used for the two unit steps only. Everything that touches the
installation runs as the account that owns it (`--service-user`, or the checkout's
owner when the flag is omitted), because the state carries its owner and the
runtime refuses a process whose uid does not match the files it holds
(`HBA ownership inode mismatch`): running the preflight, the checks or the
rehearsal as root against a service-account installation fails, and a root-run
install would leave files the service cannot use. The script does that
substitution itself, so the single `sudo` invocation above is still the whole
command.

That substitution has one consequence worth naming: the step account's Docker
context decides which daemon the preflight, the checks and the rehearsal reach,
and `sudo` does not carry the invoking shell's `DOCKER_HOST` into the script. On a
host whose account context resolves elsewhere (Docker Desktop, a non-default
context, a socket only root's context knows), pass the socket explicitly:

```
deploy/server-acceptance.sh --rehearse --docker-host unix:///var/run/docker.sock
```

The flag exports `DOCKER_HOST` for every step, including the unit steps, so the
run verifies the same daemon the service will use. Without it the preflight names
the endpoint it tried and stops, rather than checking a daemon the service cannot
reach.

An acceptance run writes its rehearsal to
`docs/evidence/server-acceptance-rehearsal.json` and copies it to
`server-acceptance-latest.json`, leaving the plain
`docs/evidence/deployment-rehearsal.json` untouched. That record carries the host
facts (kernel, Docker, Bun, Python, headroom, free disk), the exact command that
ran, the full pin set with digests, the state of the `sbarbase.service` unit,
start and finish times, and one row per check with its result. A rehearsal that
cannot run records the blocking finding instead of a pass, and exits non-zero.

### Check: does the supervised path work, not just a direct run?

The rehearsal runs the supervisor directly. It records the systemd unit's own
state, and fails the `the shipped supervisor unit is installed for an acceptance
run` check when `/etc/systemd/system/sbarbase.service` is not installed, so a
green acceptance run means the unit was present and enabled. The unit it verifies with `systemd-analyze` is
the installed file itself, and the evidence says which file that was; the
checkout's template is verified only when no unit is installed, and the evidence
records that too.

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
/usr/bin/python3 lab/combined-supervisor-check.py   # full rehearsal: start, checks, supervised shutdown
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

When Studio lands, each environment's Studio is a second upstream behind the same
reverse proxy. The shipped proxy terminates exactly one upstream, so it needs a
second upstream and a host allowlist, or the operator brings their own proxy or an
SSH tunnel. Studio has no login of its own and its pages carry a database connection
string, so it must never be reachable unauthenticated, and it must not ride the data
plane's route pattern. [Integration specification](../engineering/STUDIO-INTEGRATION.md), sections 4
and 5, covers the gate and the routing options.

A reference termination ships with the repository and is exercised by the check
(`/usr/bin/python3 lab/tls_termination_check.py`, 23 checks,
`docs/evidence/tls-termination.json`). It needs only Bun and a certificate:

Pin the console's loopback port first, so the proxy's upstream survives restarts
and reboots. Without it the console picks a new ephemeral port at every start:

```
sudo mkdir -p /etc/systemd/system/sbarbase.service.d
printf '[Service]\nEnvironment=SBARBASE_CONSOLE_PORT=8787\n' | sudo tee /etc/systemd/system/sbarbase.service.d/console-port.conf
sudo systemctl daemon-reload && sudo systemctl restart sbarbase
```

```
bun deploy/console-tls-proxy.ts \
    --cert /etc/letsencrypt/live/console.example.com/fullchain.pem \
    --key  /etc/letsencrypt/live/console.example.com/privkey.pem \
    --public-host console.example.com \
    --upstream http://127.0.0.1:8787 \
    --https-port 8443 --http-port 8080
```

To keep it running across reboots, give it its own systemd unit that runs as the service
account. On Fedora, SELinux refuses a unit that executes Bun under `/home` directly
(status `203/EXEC`, permission denied), so start it through a shell:
`ExecStart=/bin/sh -c 'exec /home/sbarbase/.bun/bin/bun deploy/console-tls-proxy.ts ...'`.
The rehearsal VM ran it this way, with a local CA, across a reboot
(`docs/evidence/vm-https-first-project.json`). It redirects plain HTTP to
`https://<public host>/` on port 443, so publish it on 443 on a real server.

It refuses to start unless the certificate and key are regular files, the key is
not group or world readable, `--public-host` is set to a bare host name, and the
upstream is loopback. The loopback assertion is applied to the console URL
whichever way it was supplied, including the one read from
`.lab/upstream/server.json` when `--upstream` is omitted. It answers plain HTTP
with a 308 redirect to HTTPS, adds `Strict-Transport-Security`,
`X-Content-Type-Options`, `Referrer-Policy`, `X-Forwarded-Proto` and
`X-Forwarded-Host`, and logs only method, path and status: never bodies, query
strings, cookies or credentials. The forwarding headers carry `--public-host`,
never the client's own `X-Forwarded-*` or `Host` values, which are dropped
before the request reaches the console.

Its own hardening is part of the checks: the redirect and the forwarded host come
from `--public-host`, never from the client's `Host` header (an attacker supplied
host cannot turn the redirect into an open redirect), hop by hop headers are
stripped before forwarding, and a request body over `--max-body` (the upload
limit plus 1 MiB by default) is answered `413` as soon as the stream passes the cap, without
buffering it in full. An operator may
prefer nginx, Caddy or the platform proxy; the checks above state which behaviour
any replacement must keep.

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

The step by step versions are [backup and restore](backup-and-restore.md) and [upgrades](upgrades.md).

- Backup: stop the supervisor, then back up the `pgdata` volumes plus the
  private state directory. The encrypted export and independent-restore path is
  documented in [INDEPENDENT-RESTORE](../engineering/INDEPENDENT-RESTORE.md); it is the only
  restore path with recorded evidence.
- Upgrade: the console's Updates page shows a newer signed release and installs a
  safe one with one click; `lab/upgrade.py` does the same from the command line.
  Each upgrade backs up every environment first, holds application traffic until
  the new version passes its health checks, and moves back by itself if it does
  not. Automatic updates are opt-in. The [upgrades guide](upgrades.md) has the
  steps, the classes that need a rebuild or a migration, and the limits.
- Rollback: before confirmation it is automatic. After it, **Roll back** on the
  Updates page or `lab/upgrade.py rollback` keeps everything written since the
  update, and refuses when the previous version cannot open the control catalog.
- Changing a pinned upstream image is a maintainer's task: follow
  [UPSTREAM-UPDATE-POLICY](../engineering/UPSTREAM-UPDATE-POLICY.md), which records
  the rollback pin and any data migration in a dated entry under `docs/upstream/`.

## Known limits at this revision

- No end-to-end install rehearsal has been run on a real server yet. An empty
  server has been simulated in a local virtual machine (Fedora 44 Cloud, clean
  clone, `lab/vm-rehearsal.sh`), which found and fixed four defects the
  development host could not show; see
  [deployment readiness](../reference/deployment-readiness.md). The
  documented command itself completed end to end on the development host at
  7:25 PM, 13 of 13, including the unit install as root and its restart
  (`docs/evidence/server-acceptance-latest.json`); what a server adds is root,
  the service account, a public certificate and the absence of a retained
  installation. The preflight, the runtime startup, adoption and verification are
  also each proven separately in the evidence files under `docs/evidence/`.
- No production capacity claim: 5888 MiB and 5.75 CPUs are configured ceilings,
  not measured peak demand. Sustained mixed load and 10/100-project capacity are
  unproven.
- Realtime, Functions, the connection pooler and cron are not implemented.
- Off-host restore and multi-host coordination are out of scope for this
  revision.
- Updates from the console and opt-in automatic updates exist, with a health-gated
  way back ([upgrades](upgrades.md)). They are covered by unit tests only: the live
  CI cases and the VM rehearsal of the update channel have not run yet.
- The bootstrap flow has no invitations, MFA or rate limiting.