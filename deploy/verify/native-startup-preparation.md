# Native startup material preparation

Updated 2026-10-06: complete current-code verification passed 1449 tests with no failures, errors, warnings or skips and independent actual review. Repairs cover SQLite and HTTPError response closure, tempfile ownership and expected test diagnostics. All 52 preparation cases also passed; native PostgreSQL startup, Cron/Vault restart continuity and physical restore remain unaccepted. See [current status](../../docs/reference/status.md) for CI results and evidence scope.

The separate public55 V3 role has actual MET: 25 component and 30 pump tests passed, original 290 and added 4 actual mode/byte captures matched, and all three known helpers were removed with no unresolved resources. Public BusyBox V8 help metadata also has actual MET: fixed-path Bash 5.3.3 and BusyBox 1.37.0 help returned zero, with its one helper removed. Neither result accepts private request operation, timeout timing/signal behavior, installed binary source provenance, candidate startup, cold/warm Vault or Cron continuity, physical restore or production.

The preserved warning-window V4 negative run reported 1447 ordinary tests OK but six ResourceWarnings, native 1 and observed window 839 for every warning. Three records have exact sqlite3.Connection type; three report tempfile.py line 484 with NoneType source. Observation windows are not allocation causes, and the three tempfile origins remain unknown. Fresh complete source/identity/mode/full bindings, final-revision CI and manually dispatched empty-host acceptance remain required; published 0e745394 CI and Website results retain only their earlier revision scope.

This is a preparation fragment for Linux Docker. It does not start PostgreSQL,
open native routing, authenticate a SQL session, prove Vault continuity or
accept production. The default command returns nonzero because startup
acceptance is unrun. Only explicit `--prepare-only` may return the distinct
`STARTUP_MATERIAL_HANDOFF_ONLY` status.

The operator executes from the repository baked at `/opt/sbarbase`. It needs
Docker CLI and access to the selected daemon, supplied through the existing
`deploy/verify/Dockerfile.identity` image. Build the public source image using
`deploy/verify/Dockerfile` and its Dockerfile-specific ignore file, then build
`Dockerfile.identity` with that exact source image as `SOURCE_VERIFY_IMAGE`.
Use fresh owned build/container names and image labels. Independent frozen
source review, bounded private-safe build capture and explicit native
preparation declaration are required before executing this new native path.
No native result is supplied by this document.

Inside that baked identity image, the explicit invocation is:

```sh
/usr/bin/python3 deploy/verify/native_factory_startup_inspect.py \
  --prepare-only \
  --profile /public-profile/factory-profile.json \
  --verifier-container "$SBARBASE_VERIFY_CONTAINER_ID" \
  --evidence /evidence/preparation.json
```

`SBARBASE_VERIFY_CONTAINER_ID` is the full CID of this invocation, supplied
by the surrounding Docker launcher. The operator compares its image and
hostname with the current invocation, and derives the factory reference from
`lab/distro-image.lock.json`. It never changes the lock, substitutes a tag or
reuses an old verifier image identifier.

The profile JSON is an explicit independently admitted public source input.
It is not generated from secret volumes or learned from private logs. The
packet contains `profile` and `source`. `profile` has the fields of
`NativeProfile` in `native_factory_startup_inspect.py`: immutable factory
reference/ID, current verifier ID, native UID/GID, custom/credential/data/socket
paths, original config/provider/key paths, original entrypoint/command and
exact inherited disabled-health shape. `source` contains exact selected
`factory_projection` and `verifier_projection`, `provider_hex` for the public
provider script, complete relative `custom_files` mapped to public hex bytes,
`custom_file_metadata` mapped to `uid:gid:mode:nlink`, and complete
`custom_directories` mapped to `uid:gid:mode`, including the root as an empty
relative path. The root-key file is forbidden in that original public tree.
The source profile must be independently bound to the committed image and
original provider/configuration before use. A caller-supplied declaration by
itself is not admission authority.

The currently committed factory lock and the separately tested candidate
image may differ. Missing profile or source mismatch refuses preparation.
This profile admission is an additional preparation prerequisite, distinct
from startup's socket compatibility, real diagnostic grammar and native SQL
privacy/provider sequencing requirements.

Preparation creates a fresh namespace with four owned local volumes. A
no-mount source helper checks the public provider and full original custom
tree against the profile. Factory image copy-up then populates the custom
volume; a CHOWN-only root helper checks the same public tree and assigns
native ownership. Native verifier helpers provision the root key through
`RootKey` and password through `PasswordFile`. Separate read-only helpers
verify both numeric witnesses. Helpers are removed through their exact CIDs,
then volume ownership and zero consumers are renewed before candidate create.
The candidate receives password-file and read-only/nocopy material mounts.
It is never started. Its created state, source, mounts and resource limits
are admitted twice before exact removal. Images and zero consumers are
renewed afterward. All four volumes remain for explicit reconciliation or a
subsequently authorized startup operation, including on failure. No volume
is removed or automatically adopted by a later invocation.

The native boundary has a 240-second whole budget, a 180-second work budget,
20-second normal commands, 10-second cleanup commands, a 1 MiB cumulative
output budget with 256 KiB reserved for cleanup, and 64 KiB per stream.
Successful stderr is refused. Public projections exclude full environment,
private values and logs. Unknown create outcomes preserve uncertainty and
cannot authorize name-based cleanup. This assumes cooperative exclusive
ownership of the selected daemon namespace; Docker inspection and removal
are not an atomic transaction.

Run meaningful source tests only inside the baked source image:

```sh
/usr/bin/python3 -m unittest discover -s lab -p 'test_native_startup_materials.py' -v
/usr/bin/python3 -m unittest discover -s lab -p 'test_native_factory_startup.py' -v
/usr/bin/python3 deploy/verify/unittest_checks.py
```

The tests exercise private file collision/binding/IO/closure refusal and the
actual preparation orchestration through a public boundary double, including
source drift, uncertain create, worker failure and mutation before cleanup.
They do not supply native profile, guard, copy-up or startup evidence.

## Independent daemon guardian

The full first implementation was refused in an independent source review:
local attach timeout killed only the Docker client, leaving the remote helper
without a lifetime bound. That NOT_MET result and its CE1 remain preserved.
The first guardian design was also refused because its wrapper could expire
before its internal cleanup deadline. Revised design acceptance was planning only. The current source correction
has independent scoped source acceptance, and its 14/9/29 focused suites passed
in the completed V9 initV2 baked run. That full regression failed; native
material preparation and PostgreSQL startup remain separate unrun gates.

Preparation now starts one separate daemon guardian from the freshly baked
identity image before any helper intent. It mounts only the selected Docker
socket and a fifth fresh public control/receipt volume at
`/sbarbase-public-control`; it never mounts the four material/runtime volumes.
The independently admitted profile includes `docker_socket_source`, defaulting
to the Linux Engine socket `/var/run/docker.sock`. The socket type, control
namespace and exact guardian source/mount/resource identity are checked. The
socket grants Docker administration and assumes cooperative exclusive run
ownership; namespace checks do not make that socket a sandbox.

The source wrapper captures its actual monotonic hard end before spawning its
owned guardian session and passes that value through a closed inherited
public descriptor. The guardian maps controller clock budgets conservatively,
then sets cleanup to the smaller of the mapped controller whole deadline and
wrapper hard end minus10seconds. Work ends at the smaller of mapped controller
work and cleanup minus30seconds. Deadlines cannot be extended by heartbeats,
configuration or control messages. Start requires another20seconds of role
runtime plus the complete30second cleanup reserve. The material worker also
has a15second internal watchdog; source and seed workers remain covered by the
independent guardian even without that internal watchdog.

Only one unsettled role is allowed. A next creation intent requires the prior
role's independently renewed exact CID/name absence and durable ACK. Unknown
create never permits start or name adoption. Every startable fullCID is
registered, independently inspected and ACKed before the start grant.
The candidate follows all six helper ACKs and is permanently never-startable.
A timed-out controller, disconnect, missing25second control lease or controller
death leaves the separate daemon guardian responsible for registered workers.
It re-admits source/owner/resources/mounts/fullCID before exact kill, wait,
terminal inspection, removal and CID/name absence. Forced cleanup is always
REFUSED. Candidate cleanup stays created-state only. Kernel/daemon/guardian
failure or missing evidence remains unresolved and blocks handoff.

Guardian native commands have a2second cap including local CLI termination and
reaping. The seven-command running cleanup path reserves14seconds. Control,
absence ACK, seal, owned reader, release and margins reserve another16seconds;
local shutdown/reaping has a separate10second reserve. A source-owned reader
child reads only the closed fixed public seal frame. The wrapper centrally
spawns and retains every detached serve, reader and Docker CLI session.
Serve requests execution through an inherited bounded socket broker and never
spawns an unregistered detached session. The wrapper is a Linux subreaper:
it signals each owned group while its leader remains reserved by WNOWAIT,
then reaps the leader and adopted group descendants without sending another
numeric PID signal. Reap or pipe-close uncertainty retains the failed owner
record and refuses completed capture framing. The controller command boundary
also enables subreaper adoption for its own CLI descendants.

Wrapper shutdown begins before hard end minus10seconds, with a0.25second
early polling margin, and all session/descriptor closure must finish before
the immutable hard end. It has no deadline extension. Reader stdout/stderr are
incrementally capped at4096bytes each; every pipe and selector closes before
the broker returns a successful frame. Source-bound control execs have their own1.5second
watchdog. A control reply arriving after the caller's2second limit freezes
progress and refuses preparation; it does not grant a late successful ACK.

Before seal, reader and release the controller reserves all remaining control
stages at2seconds each plus five final commands at10seconds each: guardian
wait, terminal inspection, removal, CID absence and name absence. It refuses
before release when that budget cannot fit. Guardian native 0/notrunning/noOOM
and exact absence are required before material handoff. The public control
volume remains as bounded evidence; all four material/runtime volumes remain
separate and retained. No PostgreSQL startup result is implied.

The shared `native_startup_contract.py` contains public exceptions/identity
and projections without runtime imports. This prevents different exception
identities or circular imports between script execution and guardian imports.
Additional baked tests:

```sh
/usr/bin/python3 -m unittest discover -s lab -p 'test_native_startup_guardian.py' -v
```

These exercise the actual executor with a helper still running after attach
timeout, independent controller-death cleanup, full command-cap reservation,
late absence ACK, exact source drift, uncertain creation, candidate refusal,
and each completion stage's remaining controller reserve. Their execution
results must be bound to the exact baked verifier image; the completed scoped
focused result and failed full result are recorded below.

The focused source suites require 14 material, 9 executor and 29 guardian cases, 52 total. The original full suite requires at least 1356 discovered and executed cases, with no skips or warnings. The completed V9 initV2 baked run passed all 14 material, 9 executor and 29 guardian cases, 52 focused cases. Its full run executed 1447 tests in 72.300 seconds and is NOT_MET, with one failure and 15 errors. Seven server-acceptance permission errors, eight read-only test-scratch errors and one executable-bit assertion prevented full acceptance. Both 290-file captures and all 875 public bindings matched, and all six known helpers were removed with no unresolved resources. The earlier release Git failure did not recur.

Earlier mode, scratch and warning verification attempts remain historical refusals bound to their own source and date. Resource ownership and fixture diagnostics were repaired, and fresh complete verification now passes 1449 tests. This does not transfer native startup or recovery evidence from another run. The [status history](../../docs/reference/status.md) preserves the earlier observations and their limits.

Process-boundary regressions exercise actual wrapper, serve, control, reader, GuardDocker and BoundedDocker functions using public subprocess fixtures. Fixture setup saves actual PR_GET_CHILD_SUBREAPER state, registers cleanup before establishing it, and restores and verifies the original state. A deliberately failing nested unittest case checks original states 0 and 1 without publishing synthetic failure progress. This fixture correction changes no production guardian lifecycle. Separate RootKey coverage retains its own scope and does not establish candidate startup or warm Vault continuity.

Wrapper fixtures inject root UID/GID metadata solely for their temporary public
CONTROL directory while retaining its actual directory type. Production still
requires real root-owned CONTROL. Regression cases also ensure a descendant
cleanup failure after leader reap cannot be accepted on retry and selector
closure failure still attempts every command pipe closure.
