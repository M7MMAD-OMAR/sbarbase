# Deployment readiness

Assessment 2026-09-20. One page answering "can this be deployed and tested on a
server, and what is proven versus assumed". Every claim links to its evidence
file. Nothing here is a production capacity claim.

## Readiness matrix

| Requirement | Status | Evidence or gap |
|---|---|---|
| One-command server acceptance with evidence | implemented, refusals tested | `deploy/server-acceptance.sh`: prerequisites, preflight, rehearsal, handoff copy of the evidence; 7 tests in `lab/test_server_acceptance.py` cover a missing prerequisite, a public bootstrap file, unknown arguments and no secret leakage |
| Host preflight (Docker native, Bun, `/usr/bin/python3` 3.14+, pinned images, headroom, disk, state) | implemented, proven | `lab/install_server.py check`; on this host it reports exactly one host-capacity blocker and the retained-installation actions. The headroom requirement now names its composition, including the measured cost of the running source stage, so a combined start refuses before creating containers instead of dying halfway |
| Supervisor unit rendering, verification and install | rendering and verification **passed on this host**; the install needs root on the server | `lab/install_server.py supervise`: the shipped unit is rewritten for the installation's paths, service user and Bun directory, the result is verified with `systemd-analyze verify`, and the exact install commands are recorded (`docs/evidence/supervisor-unit.json`, `passed: true`). It refuses to rewrite a unit whose directives moved, refuses an empty Bun directory, refuses to install a unit that does not verify, and refuses `--apply` without root. `--apply` installs to `/etc/systemd/system/sbarbase.service`, reloads, enables and starts it |
| Supervised path under systemd | **passed on this host**, 10 checks | `docs/evidence/supervised-run.json`, 16:18: a temporary systemd user unit mirroring the shipped unit's directives (`ExecStartPre` gate, `ExecStart`, PATH) was loaded, accepted the start, started the supervisor and the console, the console and management realm answered, owned containers were started by the unit, the preflight gate ran under systemd, `systemctl stop` returned cleanly, the unit file was removed and no owned container was left running. Not the shipped unit installed at `/etc/systemd/system`, which the server acceptance run covers (`--require-unit`) |
| Full source and target lifecycle rehearsal | **passed on this host** | `docs/evidence/deployment-rehearsal.json`, 11 of 11 checks, 2026-09-20 16:38 to 16:39: preflight, built page intact, supervisor start, console serving, management identity realm 200, all 8 environment routes 200, 18 live operator bootstrap checks, 14 combined gateway checks, supervised path, clean shutdown, no owned container left running. The acceptance variant (`--require-unit`, own evidence file) additionally requires the shipped unit to be installed, which needs root on the server |
| Fresh install from an empty host | every step proven in isolation, the empty-host run has not happened | the steps are separately evidenced on this host: private directories guarded, all 5 pins present and digest-verified, console built and its page verified, the owned runtime started by the rehearsal, the operator bootstrap passing 18 live checks, and supervision under systemd. An empty-host run needs a host with no retained installation, because placement container names are fixed |
| Combined runtime admission on this host | passed | `docs/evidence/combined-runtime-admission.json`: 5888 MiB of container limits and 5.75 CPUs admitted at 9328 MiB available, target started in the same run |
| HTTPS termination | **passed on this host**, 21 checks | `lab/tls_termination_check.py`: `deploy/console-tls-proxy.ts` terminates TLS (self-signed certificate in the check) in front of a stub serving the real built page; the page and its assets arrive byte for byte over HTTPS, a management route is reachable, HSTS and nosniff are set, the protocol is forwarded as https, plain HTTP gets a 308, a client supplied `Host` header cannot override the configured public host or the redirect target, hop by hop headers are not forwarded, a declared body over `--max-body` is answered 413 without being read, and the proxy refuses a world-readable key, a non-loopback upstream, a missing certificate, a missing public host and a public host that is not a bare host name (`docs/evidence/tls-termination.json`). A public certificate and the operator's own proxy are the server's business |
| Operator bootstrap step, live | **passed on this host**, 18 checks | the rehearsal now runs `bun lab/bootstrap-check.ts` against the live management Auth and records `18 live operator bootstrap checks passed`; it covers real Auth operator creation, interrupted bootstrap recovery, password verification, the private journal, idempotent catalog and authenticated organization discovery, and cleans its probe account up (`docs/evidence/bootstrap-checks.json`) |
| Console build step, isolated and verified | proven on this host | `lab/console_build_check.py`: build exit 0, page 392 bytes with both referenced assets present and hashed in `docs/evidence/console-build.json`; 8 tests in `lab/test_console_build.py` |
| Console static-serving layer, isolated | proven on this host, 15 checks | `bun lab/console-serve-check.ts`: the built page and both assets are served on an ephemeral loopback port, served asset bytes match the built files, and the security headers, method refusal, traversal refusal and loopback host check all hold (`docs/evidence/console-serve.json`) |
| Pinned images present and matching their digests | proven on this host | `lab/pinned_images_check.py`: all 5 pins resolve to the pinned digest in `docs/evidence/pinned-images.json`; 6 tests in `lab/test_pinned_images.py`. The installer now re-verifies every pin after ensuring presence |
| Retained installation migrated to owned HBA authority | implemented and executed | both retained databases adopted; per role an operation section (7 checks) and a verification section (12 checks) in `docs/evidence/retained-source-adoption.json` and `retained-target-adoption.json`, rules preserved byte for byte |
| Recovery target placement starts and serves | rehearsed live, passed | `docs/evidence/target-placement-rehearsal.json`, 12 checks: lifecycle start, Auth and REST 200, routing resumed, clean stop |
| Fresh-target HBA writer on a disposable container | proven, 16 checks | `docs/evidence/target-hba-creation-checks.json`; the consuming restore path has not been re-run with the owned writer |
| Adversarial review of the target and deployment code | completed, 12 must-fix findings all fixed | `docs/reviews/target-and-deployment-review.md`; fixes covered below |
| Deployment rehearsal in one command | implemented, records refusal honestly | `lab/deployment_rehearsal.py`; current run records `Host headroom insufficient` without starting anything, `docs/evidence/deployment-rehearsal.json` |
| Supervision with a restart policy | implemented, unit reviewed by file only | `deploy/sbarbase.service` with `ExecStartPre` preflight and a 150 s stop budget for the supervised runtime stop |
| HTTPS and network exposure | documented, operator-provided | `docs/SERVER-DEPLOYMENT.md`; no TLS termination is implemented or tested here, no port is published by the installation |
| Upgrade and rollback | policy plus runbook, no release adopted yet | `docs/UPSTREAM-UPDATE-POLICY.md`, `docs/SERVER-DEPLOYMENT.md` |
| Backup and restore | restore evidenced locally, off-host not implemented | `docs/INDEPENDENT-RESTORE.md`; off-host restore remains out of scope |
| Capacity at 10 or 100 projects | not claimed | configured ceilings only, no measured peak demand |
| Realtime, Functions, pooler, cron | not implemented | out of scope for this revision |

## Rehearsal order once a server exists

1. `/usr/bin/python3 lab/install_server.py check` on the server. Fix every
   blocker first; a host that fails validation must not be half-installed.
2. `/usr/bin/python3 lab/deployment_rehearsal.py --bootstrap-file <0600 JSON>`
   for a fresh host. It installs, supervises, verifies console and management
   reachability, probes every recorded environment route, shuts the supervisor
   down and asserts no owned container is left running, writing
   `docs/evidence/deployment-rehearsal.json`.
3. `/usr/bin/python3 lab/target_placement_rehearsal.py` when the installation
   has a recovery target, to re-prove that placement on the server.
4. `bun lab/combined-supervisor-check.ts` for the full source plus target
   rehearsal, which needs the full headroom.
5. Keep the evidence files with the deployment record. A rehearsal that fails
   is evidence too: the refusal is recorded, not hidden.

## Honest gaps that remain after a successful server rehearsal

- Sustained mixed load, connection saturation and 10/100-project capacity are
  still unmeasured; a short rehearsal proves availability, not capacity.
- Controller death during active provisioning, power loss and multi-host
  coordination remain unproven; the current crash evidence covers bounded
  checkpoints, not arbitrary crashes.
- The storage verification probe and the legacy bootstrap scripts still write
  `pg_hba.conf` directly, as inventoried in `docs/TARGET-HBA-WRITERS.md`. They
  are check-time or legacy paths, not managed runtime writers.
- Container-generation migration has a design only
  (`docs/CONTAINER-GENERATION-MIGRATION.md`).
- The bootstrap flow has no invitations, MFA or rate limiting, and no
  supported repair or reset procedure.