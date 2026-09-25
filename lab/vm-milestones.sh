#!/usr/bin/env bash
# Roadmap milestones rehearsed inside the VM that lab/vm-rehearsal.sh installed.
#
# Until a real server exists, each milestone step that needs one runs here against the
# rehearsal VM instead. Every step runs inside the guest as the service account, from
# the installed checkout, and copies its evidence back to DIR/evidence/.
#
#   lab/vm-milestones.sh --dir DIR tls              local CA, pinned console port, TLS proxy
#                                                    unit, reboot, full first project over HTTPS
#   lab/vm-milestones.sh --dir DIR invitations      invitation check against management Auth
#   lab/vm-milestones.sh --dir DIR backup-traffic   backup of every environment while two serve
#   lab/vm-milestones.sh --dir DIR export OUT       copy the newest backups and the drill users
#                                                    to OUT on the host (private, mode 700)
#   lab/vm-milestones.sh --dir DIR restore-drill IN restore IN (from export) into this VM and
#                                                    sign the drill users in
#   lab/vm-milestones.sh --dir DIR upgrade          upgrade, operator rollback, automatic way back
#   lab/vm-milestones.sh --dir DIR environments     fill to the environment limit and measure
#   lab/vm-milestones.sh --dir DIR soak MINUTES     sample memory, disk, logs and restarts
#
# A local CA stands in for a public certificate, and a name in /etc/hosts for public
# DNS: this proves the proxy, the pinned port and the reboot, not public networking.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="${HOME}/.local/share/sbarbase-vm"
SSH_PORT=2222
HOST_NAME=console.sbarbase.test
fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }
step() { printf '\n== %s\n' "$1"; }
wait_seconds() { /usr/bin/python3 -c "import time; time.sleep($1)"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) shift; DIR="${1:-}" ;;
    --ssh-port) shift; SSH_PORT="${1:-}" ;;
    -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
    *) break ;;
  esac
  shift
done
COMMAND="${1:-}"; shift || :
[ -n "$COMMAND" ] || fail "name a step; see --help"

SSH=(ssh -q -i "$DIR/id_ed25519" -p "$SSH_PORT" -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$DIR/known_hosts" -o ConnectTimeout=5 ops@127.0.0.1)
guest() { "${SSH[@]}" "$@"; }
# One command in the installed checkout as the service account.
as_service() { guest "cd /opt/sbarbase && sudo -u sbarbase env PATH=/home/sbarbase/.bun/bin:/usr/bin:/bin HOME=/home/sbarbase $*"; }
evidence() {
  mkdir -p "$DIR/evidence"
  guest "cd /opt/sbarbase/docs/evidence && sudo tar cf - $*" | tar xf - -C "$DIR/evidence"
}
[ -f "$DIR/qemu.pid" ] && kill -0 "$(cat "$DIR/qemu.pid")" 2>/dev/null || fail "no VM runs from $DIR; start one with lab/vm-rehearsal.sh"
guest true || fail "the VM does not answer on SSH port $SSH_PORT"

wait_service() {
  for _ in $(seq 1 90); do
    guest 'systemctl is-active --quiet sbarbase && sudo test -f /opt/sbarbase/.lab/upstream/server.json' 2>/dev/null && return 0
    wait_seconds 5
  done
  return 1
}
reboot_guest() {
  guest 'sudo systemctl reboot' || :
  wait_seconds 20
  for _ in $(seq 1 60); do guest true 2>/dev/null && break; wait_seconds 5; done
  wait_service || fail "sbarbase.service is not active after the reboot"
}

case "$COMMAND" in
tls)
  step "local CA and a certificate for $HOST_NAME"
  guest "set -e
sudo install -d -m 755 /etc/sbarbase-tls
cd /etc/sbarbase-tls
sudo test -f ca.pem || sudo openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 30 \
  -subj '/CN=Sbarbase rehearsal CA' -keyout ca.key -out ca.pem 2>/dev/null
sudo openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -subj '/CN=$HOST_NAME' \
  -keyout console.key -out console.csr 2>/dev/null
printf 'subjectAltName=DNS:$HOST_NAME\n' | sudo tee san.ext >/dev/null
sudo openssl x509 -req -in console.csr -CA ca.pem -CAkey ca.key -CAcreateserial -days 30 -extfile san.ext -out console.pem 2>/dev/null
sudo chown sbarbase:sbarbase console.key console.pem && sudo chmod 600 console.key ca.key
grep -q '$HOST_NAME' /etc/hosts || echo '127.0.0.1 $HOST_NAME' | sudo tee -a /etc/hosts >/dev/null"

  step "pin the console port and run the TLS proxy as a unit"
  guest "set -e
sudo mkdir -p /etc/systemd/system/sbarbase.service.d
printf '[Service]\nEnvironment=SBARBASE_CONSOLE_PORT=8787\n' | sudo tee /etc/systemd/system/sbarbase.service.d/console-port.conf >/dev/null
sudo tee /etc/systemd/system/sbarbase-tls.service >/dev/null <<'UNIT'
[Unit]
Description=Sbarbase console TLS proxy (rehearsal)
After=sbarbase.service
Wants=sbarbase.service

[Service]
User=sbarbase
WorkingDirectory=/opt/sbarbase
# SELinux on Fedora refuses systemd executing a binary under /home directly
# (status 203/EXEC, Permission denied); a shell started by systemd may exec it.
ExecStart=/bin/sh -c 'exec /home/sbarbase/.bun/bin/bun deploy/console-tls-proxy.ts --cert /etc/sbarbase-tls/console.pem --key /etc/sbarbase-tls/console.key --public-host $HOST_NAME --upstream http://127.0.0.1:8787 --https-port 8443 --http-port 8080'
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl restart sbarbase
sudo systemctl enable --now sbarbase-tls"
  wait_service || fail "sbarbase.service did not come back with the pinned port"

  https_checks() {
    guest "set -e
for i in \$(seq 1 60); do curl -fsS -o /dev/null --cacert /etc/sbarbase-tls/ca.pem https://$HOST_NAME:8443/ && break; sleep 3; done
code=\$(curl -s -o /dev/null -w '%{http_code}' --cacert /etc/sbarbase-tls/ca.pem https://$HOST_NAME:8443/)
hsts=\$(curl -sI --cacert /etc/sbarbase-tls/ca.pem https://$HOST_NAME:8443/ | grep -ci '^strict-transport-security' || true)
redirect=\$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' http://$HOST_NAME:8080/)
port=\$(sudo /usr/bin/python3 -c 'import json;print(json.load(open(\"/opt/sbarbase/.lab/upstream/server.json\"))[\"url\"])')
echo \"console over https: \$code; hsts: \$hsts; plain http: \$redirect; console upstream: \$port\"
[ \"\$code\" = 200 ] && [ \"\$hsts\" = 1 ] && case \"\$redirect\" in 308\ https://$HOST_NAME/*) true;; *) false;; esac && case \"\$port\" in *:8787) true;; *) false;; esac"
  }
  step "console and redirect over HTTPS"
  https_checks || fail "the console does not answer over HTTPS"
  step "reboot, then the same over HTTPS"
  reboot_guest
  https_checks || fail "the console does not answer over HTTPS after the reboot"
  step "a first project end to end through the TLS proxy"
  as_service "NODE_EXTRA_CA_CERTS=/etc/sbarbase-tls/ca.pem bun lab/first-project-check.ts /home/sbarbase/operator.json --base https://$HOST_NAME:8443 --evidence docs/evidence/vm-https-first-project.json" \
    || { evidence vm-https-first-project.json; fail "the first project check over HTTPS failed"; }
  evidence vm-https-first-project.json
  ;;
invitations)
  step "invitation check against the real management Auth"
  status=0
  as_service "bun lab/invitation-check.ts /home/sbarbase/operator.json --evidence docs/evidence/vm-invitation-check.json" || status=$?
  evidence vm-invitation-check.json; exit "$status"
  ;;
backup-traffic)
  step "backup every environment while two of them serve"
  status=0
  as_service "bun lab/backup-traffic-check.ts /home/sbarbase/operator.json --evidence docs/evidence/vm-backup-traffic.json --drill .lab/drill-users.json" || status=$?
  evidence vm-backup-traffic.json; exit "$status"
  ;;
export)
  OUT="${1:-}"; [ -n "$OUT" ] || fail "export needs an output directory"
  mkdir -p "$OUT"; chmod 700 "$OUT"
  step "copy the newest backup of each environment and the drill users to $OUT"
  guest 'cd /opt/sbarbase/.lab && sudo sh -c '"'"'set -e; list=drill-users.json
for e in backups/e_*; do newest=$(ls -1 "$e" | grep -E "^[0-9]{8}T[0-9]{6}Z$" | tail -1); list="$list $e/$newest"; done
tar cf - $list'"'" > "$OUT/export.tar"
  chmod 600 "$OUT/export.tar"
  tar tf "$OUT/export.tar" | grep -c manifest.json | xargs printf 'exported %s backup(s)\n'
  ;;
restore-drill)
  IN="${1:-}"; [ -f "$IN/export.tar" ] || fail "restore-drill needs the directory an export wrote"
  step "copy the export into the fresh installation"
  guest 'rm -rf /tmp/drill && mkdir /tmp/drill' && "${SSH[@]}" 'tar xf - -C /tmp/drill' < "$IN/export.tar"
  guest 'sudo chown -R sbarbase:sbarbase /tmp/drill && sudo chmod -R go-rwx /tmp/drill'
  step "restore every exported environment onto this installation"
  status=0
  as_service "bun lab/restore-drill-check.ts /home/sbarbase/operator.json /tmp/drill --evidence docs/evidence/vm-restore-drill.json" || status=$?
  evidence vm-restore-drill.json; exit "$status"
  ;;
upgrade)
  step "candidate versions on top of the installed one"
  versions=$(guest 'cd /opt/sbarbase && sudo -u sbarbase /usr/bin/python3 lab/upgrade-check.py candidates')
  eval "$versions"
  phase() {
    for _ in $(seq 1 120); do
      p=$(guest "sudo /usr/bin/python3 -c 'import json;print(json.load(open(\"/opt/sbarbase/.lab/upgrades/state.json\")).get(\"phase\"))'" 2>/dev/null || true)
      [ "$p" = "$1" ] && guest 'curl -fsS -o /dev/null "$(sudo /usr/bin/python3 -c "import json;print(json.load(open(\"/opt/sbarbase/.lab/upstream/server.json\"))[\"url\"])")/"' && return 0
      case "$p" in failed|rollback_failed) break ;; esac
      wait_seconds 5
    done
    printf 'upgrade phase: %s (expected %s)\n' "$p" "$1"; return 1
  }
  # `upgrade.py rollback` takes a confirmed upgrade only, so the operator's rollback comes right
  # after the confirmation, and the broken version then moves back to the installed one.
  # The evidence file is written once, at the end, even when a stage fails: a changed tracked
  # file would make the next `upgrade.py start` refuse.
  rehearse_upgrade() {
    as_service "/usr/bin/python3 lab/upgrade-check.py before base=$base good=$good bad=$bad" || return 1
    step "upgrade to a newer PostgREST"
    as_service "/usr/bin/python3 lab/upgrade.py start --to $good" || return 1
    guest 'sudo systemctl restart sbarbase'
    phase confirmed || { printf 'FAIL: the upgrade was not confirmed\n' >&2; return 1; }
    as_service "/usr/bin/python3 lab/upgrade-check.py after upgraded" || return 1
    step "the operator goes back to the version before the upgrade"
    as_service "/usr/bin/python3 lab/upgrade.py rollback" || return 1
    guest 'sudo systemctl restart sbarbase'
    phase rolled_back || { printf 'FAIL: the rollback did not come back\n' >&2; return 1; }
    as_service "/usr/bin/python3 lab/upgrade-check.py after operator-rollback" || return 1
    step "a version whose PostgREST never starts moves back by itself"
    as_service "/usr/bin/python3 lab/upgrade.py start --to $bad" || return 1
    guest 'sudo systemctl restart sbarbase'
    phase rolled_back || { printf 'FAIL: the broken version was not moved back\n' >&2; return 1; }
    as_service "/usr/bin/python3 lab/upgrade.py status" || :
    as_service "/usr/bin/python3 lab/upgrade-check.py after rolled-back --back-to base" || return 1
    # The installation is back on the version it was installed with: REST runs the original
    # PostgREST again and every environment still has its rows.
    back=$(guest 'cd /opt/sbarbase && sudo -u sbarbase git rev-parse HEAD')
    rest=$(guest "sudo docker inspect -f '{{.Image}}' \$(sudo docker ps --format '{{.Names}}' | grep -m1 -- '-rest\$')")
    printf 'checkout at the end: %s (installed version %s); REST image %s\n' "$back" "$base" "$rest"
    [ "$back" = "$base" ] || { printf 'FAIL: the installation is not back on the installed version\n' >&2; return 1; }
    case "$rest" in *2f8e7b656f09*) ;; *) printf 'FAIL: REST does not run the original PostgREST again: %s\n' "$rest" >&2; return 1 ;; esac
    as_service "/usr/bin/python3 -c 'import sys;sys.path.insert(0,\"lab\");import backup;print({e:backup.counts(e) for e in backup.environments()})'" || return 1
  }
  status=0
  rehearse_upgrade || status=1
  as_service "/usr/bin/python3 lab/upgrade-check.py evidence docs/evidence/vm-upgrade-checks.json upgraded operator-rollback rolled-back" || status=1
  evidence vm-upgrade-checks.json || :
  exit "$status"
  ;;
environments)
  step "fill the installation to its environment limit and measure it"
  status=0
  as_service "bun lab/environment-limit-check.ts /home/sbarbase/operator.json --evidence docs/evidence/vm-environment-limit.json" || status=$?
  evidence vm-environment-limit.json; exit "$status"
  ;;
soak)
  MINUTES="${1:-60}"; case "$MINUTES" in *[!0-9]*|'') fail "soak takes minutes" ;; esac
  step "soak for $MINUTES minutes: one sample every 5 minutes"
  status=0
  as_service "/usr/bin/python3 lab/soak.py --minutes $MINUTES --evidence docs/evidence/vm-soak.json" || status=$?
  evidence vm-soak.json; exit "$status"
  ;;
*) fail "unknown step: $COMMAND" ;;
esac
printf '\n%s: done. Evidence in %s/evidence\n' "$COMMAND" "$DIR"
