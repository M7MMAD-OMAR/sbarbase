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
#
# It never prints a secret: only whether a bootstrap file was used. Every step
# that fails stops the run and exits non-zero. Evidence lands in
# docs/evidence/deployment-rehearsal.json and is copied to
# docs/evidence/server-acceptance-latest.json for handoff.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
PYTHON=/usr/bin/python3
BOOTSTRAP=""
REHEARSAL=0
SKIP_INSTALL=0
INSTALL_UNIT=0

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }
step() { printf '\n== %s\n' "$1"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --rehearse) REHEARSAL=1 ;;
    --skip-install) SKIP_INSTALL=1 ;;
    --install-unit) INSTALL_UNIT=1 ;;
    --bootstrap-file) shift; [ $# -gt 0 ] || fail "--bootstrap-file needs a path"; BOOTSTRAP="$1" ;;
    --python) shift; [ $# -gt 0 ] || fail "--python needs a path"; PYTHON="$1" ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
  shift
done

step "prerequisites"
for tool in docker bun git; do
  command -v "$tool" >/dev/null 2>&1 || fail "$tool is not on PATH"
  printf 'ok: %s %s\n' "$tool" "$("$tool" --version 2>/dev/null | head -1)"
done
[ -x "$PYTHON" ] || fail "$PYTHON is missing; pass --python with a 3.14+ interpreter"
"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info>=(3,14) else 1)' \
  || fail "$PYTHON is older than 3.14; the lab runtime needs modern f-strings"
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
"$PYTHON" lab/install_server.py check || fail "preflight refused; fix the blockers above before installing"

if [ "$REHEARSAL" = "0" ]; then
  step "done"
  printf 'Preflight passed. Re-run with --rehearse (and --bootstrap-file) to run the full rehearsal.\n'
  exit 0
fi

step "console static-serving check"
bun lab/console-serve-check.ts || fail "console static-serving check failed; see docs/evidence/console-serve.json"

step "TLS termination check (reference proxy)"
"$PYTHON" lab/tls_termination_check.py || fail "TLS termination check failed; see docs/evidence/tls-termination.json"

step "supervisor unit"
if [ "$INSTALL_UNIT" = "1" ]; then
  [ "$(id -u)" = "0" ] || fail "--install-unit needs root (run the whole script with sudo)"
  "$PYTHON" lab/install_server.py supervise --apply || fail "the supervisor unit could not be installed"
  systemctl is-active --quiet sbarbase.service || fail "sbarbase.service is not active after install"
  printf 'ok: sbarbase.service installed, enabled and active\n'
else
  "$PYTHON" lab/install_server.py supervise || fail "the supervisor unit did not render and verify for this installation"
fi
"$PYTHON" - <<'PY' || true
import json
record=json.load(open('docs/evidence/supervisor-unit.json'))
if not record['applied']:
    print('The unit is not installed on this host. Install it with --install-unit (as root):')
    for command in record['install_commands']:
        print('  '+command)
PY

step "deployment rehearsal"
rehearsal_args=(--attempts 3 --require-unit)
[ -n "$BOOTSTRAP" ] && rehearsal_args+=(--bootstrap-file "$BOOTSTRAP")
[ "$SKIP_INSTALL" = "1" ] && rehearsal_args+=(--skip-install)
"$PYTHON" lab/deployment_rehearsal.py "${rehearsal_args[@]}" || fail "rehearsal failed; see docs/evidence/deployment-rehearsal.json"

step "evidence"
evidence=docs/evidence/deployment-rehearsal.json
[ -f "$evidence" ] || fail "rehearsal reported success but wrote no evidence"
cp "$evidence" docs/evidence/server-acceptance-latest.json
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
printf 'Server acceptance: PASSED\n'