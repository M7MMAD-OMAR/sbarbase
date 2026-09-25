#!/usr/bin/env bash
# Server acceptance: prerequisites, preflight, deployment rehearsal, evidence.
#
# Run this ON the server, from the repository root:
#
#   deploy/server-acceptance.sh                    # checks and preflight only
#   deploy/server-acceptance.sh --rehearse         # full rehearsal, no bootstrap secrets
#   deploy/server-acceptance.sh --bootstrap-file /path/to/operator.json
#   deploy/server-acceptance.sh --rehearse --skip-install
#   sudo deploy/server-acceptance.sh --rehearse --install-unit --bootstrap-file /path/operator.json
#   sudo deploy/server-acceptance.sh --rehearse --install-unit --first-project --bootstrap-file /path/operator.json
#
# The supervised installation runs as an account that exists on the server and
# holds the checkout. Name it when it is not 'sbarbase', the shipped default:
#
#   deploy/server-acceptance.sh --rehearse --install-unit \
#        --service-user ops-account --home /srv/ops-account --bun-dir /srv/ops-account/.bun/bin
#
# Every step runs as that account, so name the Docker endpoint when the account's
# Docker context does not resolve to the daemon the unit will use (a workstation
# with Docker Desktop, a non-default context, a socket systemd must be told
# about). The unit itself carries no DOCKER_HOST: on such a host add it to the
# unit with a drop-in, as docs/guides/server-deployment.md describes.
#
#   deploy/server-acceptance.sh --rehearse --docker-host unix:///var/run/docker.sock
#
# Every start of the unit is recorded only once its console answers over
# loopback; --console-timeout SECONDS bounds that wait (default 300).
#
# It never prints a secret: only whether a bootstrap file was used. Every step
# that fails stops the run and exits non-zero. Evidence lands in
# docs/evidence/deployment-rehearsal.json and is copied to
# docs/evidence/server-acceptance-latest.json for handoff.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
PYTHON=/usr/bin/python3
BOOTSTRAP=""
REHEARSAL=0
SKIP_INSTALL=0
INSTALL_UNIT=0
FIRST_PROJECT=0
SERVICE_USER=""
SERVICE_HOME=""
BUN_DIR=""
DOCKER_HOST_ARG=""
CONSOLE_WAIT=300

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }
step() { printf '\n== %s\n' "$1"; }
# Prints systemctl's own word for the unit (active, activating, inactive, failed)
# for a stopped unit too; `systemctl is-active` exits non-zero when the unit is
# not active, so the function must not append anything of its own.
unit_state() { systemctl is-active sbarbase.service 2>/dev/null || :; }

while [ $# -gt 0 ]; do
  case "$1" in
    --rehearse) REHEARSAL=1 ;;
    --skip-install) SKIP_INSTALL=1 ;;
    --install-unit) INSTALL_UNIT=1 ;;
    --first-project) FIRST_PROJECT=1 ;;
    --bootstrap-file) shift; [ $# -gt 0 ] || fail "--bootstrap-file needs a path"; BOOTSTRAP="$1" ;;
    --service-user) shift; [ $# -gt 0 ] || fail "--service-user needs an account name"; SERVICE_USER="$1" ;;
    --home) shift; [ $# -gt 0 ] || fail "--home needs a path"; SERVICE_HOME="$1" ;;
    --bun-dir) shift; [ $# -gt 0 ] || fail "--bun-dir needs a path"; BUN_DIR="$1" ;;
    --docker-host) shift; [ $# -gt 0 ] || fail "--docker-host needs an endpoint"; DOCKER_HOST_ARG="$1" ;;
    --console-timeout) shift; [ $# -gt 0 ] || fail "--console-timeout needs a number of seconds"; CONSOLE_WAIT="$1" ;;
    --python) shift; [ $# -gt 0 ] || fail "--python needs a path"; PYTHON="$1" ;;
    -h|--help) sed -n '2,33p' "$0"; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
  shift
done
case "$CONSOLE_WAIT" in
  ''|*[!0-9]*|0) fail "--console-timeout must be a positive whole number of seconds: $CONSOLE_WAIT" ;;
esac

# sudo replaces PATH with a secure default, so a Bun installed under the
# invoking user's home disappears. --bun-dir names it once for every step: the
# prerequisite check here and the unit's PATH below.
if [ -n "$BUN_DIR" ]; then
  [ -x "$BUN_DIR/bun" ] || fail "--bun-dir does not hold a bun executable: $BUN_DIR"
  PATH="$BUN_DIR:$PATH"
  export PATH
  printf 'ok: bun directory %s added to PATH\n' "$BUN_DIR"
fi

# The steps run as the installation's account, and that account's Docker context
# may resolve elsewhere than the daemon the unit will use, so the endpoint is
# named explicitly when the host needs it. It is forwarded to every step below.
if [ -n "$DOCKER_HOST_ARG" ]; then
  case "$DOCKER_HOST_ARG" in
    *[[:space:]]*) fail "--docker-host must be a single endpoint with no whitespace: $DOCKER_HOST_ARG" ;;
  esac
  export DOCKER_HOST="$DOCKER_HOST_ARG"
  printf 'note: docker steps use %s\n' "$DOCKER_HOST"
fi

# The installation's state belongs to an account, and its ownership model refuses
# a process whose uid does not match the files it holds ("HBA ownership inode
# mismatch"). So every step that touches the installation runs as that account,
# and root is used only for the two unit steps. Without --service-user the
# checkout's own owner is used, which is who a sudo run usually is.
REPO_OWNER="$(stat -c '%U' "$REPO_ROOT")"
if [ -z "$SERVICE_USER" ]; then
  SERVICE_USER="$REPO_OWNER"
  printf 'note: --service-user not given; using the checkout owner %s\n' "$SERVICE_USER"
fi
run_as_installation() {
  if [ "$(id -u)" != "0" ] || [ "$SERVICE_USER" = "root" ]; then
    "$@"
    return
  fi
  if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    fail "--service-user $SERVICE_USER does not exist on this host"
  fi
  local environment=("PATH=$PATH")
  if [ -n "${DOCKER_HOST:-}" ]; then environment+=("DOCKER_HOST=$DOCKER_HOST"); fi
  sudo -u "$SERVICE_USER" -H env "${environment[@]}" "$@"
}
# systemd reports the unit active the moment dev.py is executed; the console
# answers only once the owned runtime and the API are up. A start is recorded
# only after the console answers over loopback, within a bound (--console-timeout).
console_answers() {
  run_as_installation "$PYTHON" lab/install_server.py wait-console --timeout "$CONSOLE_WAIT"
}
# System units need root; a non-root run uses sudo when it has the right to.
unit_control() {
  if [ "$(id -u)" = "0" ]; then systemctl "$@"; else sudo -n systemctl "$@"; fi
}

step "prerequisites"
for tool in docker bun git; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    if [ "$tool" = "bun" ] && [ -n "${SUDO_USER:-}" ]; then
      fail "bun is not on PATH: sudo replaces PATH with a secure default, so an installation under /home/$SUDO_USER is invisible. Pass --bun-dir, for example --bun-dir /home/$SUDO_USER/.bun/bin"
    fi
    fail "$tool is not on PATH"
  fi
  printf 'ok: %s %s\n' "$tool" "$("$tool" --version 2>/dev/null | head -1)"
done
[ -x "$PYTHON" ] || fail "$PYTHON is missing; pass --python with a 3.12+ interpreter"
"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info>=(3,12) else 1)' \
  || fail "$PYTHON is older than 3.12; the lab runtime needs PEP 701 f-strings"
printf 'ok: %s %s\n' "$PYTHON" "$("$PYTHON" -c 'import platform;print(platform.python_version())')"
docker info --format '{{.Name}} {{.OSType}}' >/dev/null 2>&1 || fail "Docker daemon unavailable or not native Linux"
printf 'ok: docker daemon %s\n' "$(docker info --format '{{.OSType}}')"
[ -f lab/install_server.py ] || fail "run this from the sbarbase checkout (lab/install_server.py missing)"
[ -f deploy/sbarbase.service ] || fail "deploy/sbarbase.service missing"
printf 'ok: repository at %s\n' "$REPO_ROOT"

if [ -n "$BOOTSTRAP" ]; then
  [ -f "$BOOTSTRAP" ] || fail "bootstrap file not found"
  permissions="$(stat -c '%a' "$BOOTSTRAP")"
  [ "$permissions" = "600" ] || fail "bootstrap file must be mode 600 (found $permissions)"
  printf 'ok: bootstrap file present and private (contents never printed)\n'
fi

step "read-only preflight"
run_as_installation "$PYTHON" lab/install_server.py check || fail "preflight refused; fix the blockers above before installing"

if [ "$REHEARSAL" = "0" ]; then
  step "done"
  printf 'Preflight passed. Re-run with --rehearse (and --bootstrap-file) to run the full rehearsal.\n'
  exit 0
fi

# The serving check below reads the built page. On an empty host nothing has been
# built yet, so install the pinned dependencies and build the console first,
# exactly as the installer does; an existing build is verified, not rebuilt.
step "console build"
if [ ! -d node_modules ]; then
  run_as_installation bun install --frozen-lockfile || fail "bun install --frozen-lockfile failed; the lockfile and package.json disagree or the registry is unreachable"
fi
if [ -f .lab/ui/index.html ]; then
  run_as_installation "$PYTHON" lab/console_build_check.py --verify-only || fail "the existing console build is not intact; see docs/evidence/console-build.json"
else
  run_as_installation "$PYTHON" lab/console_build_check.py || fail "console build failed; see docs/evidence/console-build.json"
fi

step "console static-serving check"
if ! run_as_installation bun lab/console-serve-check.ts; then
  if [ -f docs/evidence/console-serve.json ]; then
    fail "console static-serving check failed; the recorded findings are in docs/evidence/console-serve.json"
  else
    fail "console static-serving check failed before it could write evidence; the refusal is in the output above"
  fi
fi

step "TLS termination check (reference proxy)"
if ! run_as_installation "$PYTHON" lab/tls_termination_check.py; then
  if [ -f docs/evidence/tls-termination.json ]; then
    fail "TLS termination check failed; the recorded findings are in docs/evidence/tls-termination.json"
  else
    fail "TLS termination check failed before it could write evidence; the refusal is in the output above"
  fi
fi

step "supervisor unit"
supervise_args=(supervise)
if [ -n "$SERVICE_USER" ]; then supervise_args+=(--service-user "$SERVICE_USER"); fi
if [ -n "$SERVICE_HOME" ]; then supervise_args+=(--home "$SERVICE_HOME"); fi
if [ -n "$BUN_DIR" ]; then supervise_args+=(--bun-dir "$BUN_DIR"); fi
if [ "$INSTALL_UNIT" = "1" ]; then
  [ "$(id -u)" = "0" ] || fail "--install-unit needs root (run the whole script with sudo)"
  "$PYTHON" lab/install_server.py "${supervise_args[@]}" --apply --timeout "$CONSOLE_WAIT" || fail "the supervisor unit could not be installed, or its console did not answer"
  unit_control is-active --quiet sbarbase.service || fail "sbarbase.service is not active after install"
  printf 'ok: sbarbase.service installed, enabled and active, and its console answers\n'
else
  run_as_installation "$PYTHON" lab/install_server.py "${supervise_args[@]}" || fail "the supervisor unit did not render and verify for this installation"
fi
"$PYTHON" - <<'PY'
import json
record=json.load(open('docs/evidence/supervisor-unit.json'))
if not record['applied']:
    print('The unit is not installed on this host. Install it with --install-unit (as root):')
    for command in record['install_commands']:
        print('  '+command)
PY

step "release the supervised installation for the rehearsal"
# Two supervisors cannot own the same containers and state, and the rehearsal's
# preflight refuses to run against a live installation. This applies whether or
# not the unit was just installed, and the unit is started again on any exit.
restore_unit_on_exit() {
  if [ "${STOPPED_UNIT:-0}" = "1" ]; then
    printf '\n== restore the supervised installation\n'
    if unit_control start sbarbase.service; then
      if [ "$(unit_state)" != "active" ]; then printf 'FAIL: sbarbase.service did not become active again\n' >&2;
      elif console_answers; then printf 'ok: sbarbase.service active again and its console answers\n';
      else printf 'FAIL: sbarbase.service is active again but its console did not answer within %s s\n' "$CONSOLE_WAIT" >&2; fi
    else
      printf 'FAIL: sbarbase.service could not be started again\n' >&2
    fi
  fi
}
trap restore_unit_on_exit EXIT
STOPPED_UNIT=0
if [ "$(unit_state)" = "active" ] || [ "$(unit_state)" = "activating" ]; then
  unit_control stop sbarbase.service || fail "sbarbase.service could not be stopped for the rehearsal"
  STOPPED_UNIT=1
  for _ in $(seq 1 120); do
    if [ "$(unit_state)" = "inactive" ]; then break; fi
    sleep 1
  done
  [ "$(unit_state)" = "inactive" ] || fail "sbarbase.service did not stop; the rehearsal would run against a live installation"
  printf 'ok: sbarbase.service stopped for the rehearsal\n'
else
  printf 'ok: sbarbase.service is not active; nothing to release\n'
fi

rehearsal_args=(--attempts 3 --require-unit --evidence docs/evidence/server-acceptance-rehearsal.json)
if [ -n "$BOOTSTRAP" ]; then rehearsal_args+=(--bootstrap-file "$BOOTSTRAP"); fi
if [ "$SKIP_INSTALL" = "1" ]; then rehearsal_args+=(--skip-install); fi
if ! run_as_installation "$PYTHON" lab/deployment_rehearsal.py "${rehearsal_args[@]}"; then
  if [ -f docs/evidence/server-acceptance-rehearsal.json ]; then
    fail "rehearsal failed; the recorded findings are in docs/evidence/server-acceptance-rehearsal.json"
  else
    fail "rehearsal failed before it could write evidence; the refusal is in the output above"
  fi
fi

if [ "${STOPPED_UNIT:-0}" = "1" ]; then
  restore_unit_on_exit
  # Restored once here; the exit trap must not start it a second time.
  STOPPED_UNIT=0
  unit_control is-active --quiet sbarbase.service || fail "sbarbase.service is not active after the rehearsal"
  console_answers || fail "sbarbase.service is active after the rehearsal but its console did not answer within $CONSOLE_WAIT s"
fi

step "evidence"
evidence=docs/evidence/server-acceptance-rehearsal.json
[ -f "$evidence" ] || fail "rehearsal reported success but wrote no evidence"
run_as_installation cp "$evidence" docs/evidence/server-acceptance-latest.json
# Read-only summary: it prints, it writes nothing, and running it under sudo would
# put a password prompt between the here-document and python.
"$PYTHON" - "$evidence" <<'PY' || fail "evidence could not be summarised"
import json,sys
record=json.load(open(sys.argv[1]))
host=record.get('host',{})
print('passed:',record.get('passed'),'checks:',record.get('count'))
print('host: kernel',host.get('kernel'),'| docker',host.get('docker'),'| bun',host.get('bun'),'| python',host.get('python'))
print('pins:',', '.join(f"{p['component']}={p['digest'][:19]}" for p in record.get('pins',[])))
print('unit:',record.get('unit'))
for item in record.get('checks',[]):
    print(('  ok   ' if item['ok'] else '  FAIL ')+item['check']+('' if item['ok'] else '  '+str(item.get('detail',''))))
raise SystemExit(0 if record.get('passed') else 1)
PY
printf '\nEvidence: %s (copy kept at docs/evidence/server-acceptance-latest.json)\n' "$evidence"

# The rehearsal proves the installation starts with no environments. --first-project
# then does what a new operator does against the supervised installation: a project,
# an environment provisioned by the worker, a key and supabase-js through the gateway.
if [ "$FIRST_PROJECT" = "1" ]; then
  step "first project"
  [ -n "$BOOTSTRAP" ] || fail "--first-project needs --bootstrap-file to log in as the operator"
  unit_control is-active --quiet sbarbase.service || fail "--first-project needs the supervised installation running"
  console_answers || fail "the supervised console did not answer within $CONSOLE_WAIT s"
  run_as_installation bun lab/first-project-check.ts "$BOOTSTRAP" --evidence docs/evidence/first-project-check.json \
    || fail "first project check failed; the recorded findings are in docs/evidence/first-project-check.json"
fi
printf 'Server acceptance: PASSED\n'