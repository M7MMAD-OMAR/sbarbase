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
#   lab/vm-milestones.sh --dir DIR channel          signed releases from a local source, installed
#                                                    from the console and by automatic mode
#   lab/vm-milestones.sh --dir DIR environments     fill to the environment limit and measure
#   lab/vm-milestones.sh --dir DIR soak MINUTES     sample memory, disk, logs and restarts
#
# A local CA stands in for a public certificate, and a name in /etc/hosts for public
# DNS: this proves the proxy, the pinned port and the reboot, not public networking.
set -euo pipefail

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
    -h|--help) sed -n '2,23p' "$0"; exit 0 ;;
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

# Active is not enough: systemd says so the moment the supervisor starts. The console must answer.
console_answers() { as_service "/usr/bin/python3 lab/install_server.py wait-console --timeout ${1:-300}" >/dev/null; }
wait_service() {
  for _ in $(seq 1 90); do
    guest 'systemctl is-active --quiet sbarbase' 2>/dev/null && { console_answers; return; }
    wait_seconds 5
  done
  return 1
}
# One live check inside the guest; its evidence is copied back whether it passed or not.
run_check() {
  local file=$1 status=0; shift
  as_service "$* --evidence docs/evidence/$file" || status=$?
  evidence "$file"; exit "$status"
}
# The upgrade phase in the installed checkout: returns once it is $1 and the console answers.
phase() {
  for _ in $(seq 1 120); do
    p=$(guest "sudo /usr/bin/python3 -c 'import json;print(json.load(open(\"/opt/sbarbase/.lab/upgrades/state.json\")).get(\"phase\"))'" 2>/dev/null || true)
    [ "$p" = "$1" ] && console_answers 10 && return 0
    case "$p" in failed|rollback_failed) break ;; esac
    wait_seconds 5
  done
  printf 'upgrade phase: %s (expected %s)\n' "$p" "$1"; return 1
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
  run_check vm-invitation-check.json bun lab/invitation-check.ts /home/sbarbase/operator.json
  ;;
backup-traffic)
  step "backup every environment while two of them serve"
  run_check vm-backup-traffic.json bun lab/backup-traffic-check.ts /home/sbarbase/operator.json --drill .lab/drill-users.json
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
  run_check vm-restore-drill.json bun lab/restore-drill-check.ts /home/sbarbase/operator.json /tmp/drill
  ;;
upgrade)
  step "candidate versions on top of the installed one"
  versions=$(guest 'cd /opt/sbarbase && sudo -u sbarbase /usr/bin/python3 lab/upgrade-check.py candidates')
  eval "$versions"
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
  }
  status=0
  rehearse_upgrade || status=1
  as_service "/usr/bin/python3 lab/upgrade-check.py evidence docs/evidence/vm-upgrade-checks.json upgraded operator-rollback rolled-back" || status=1
  evidence vm-upgrade-checks.json || :
  exit "$status"
  ;;
channel)
  # The update channel as an operator uses it: releases signed with the throwaway key `candidates`
  # lists, served from a bare repository in the guest, found by "check now", installed from the
  # management API or by automatic mode, with the supervisor draining, exiting 42 and systemd
  # starting the new version behind the guard and the hold.
  OPERATOR=/home/sbarbase/operator.json
  SOURCE=/home/sbarbase/releases.git
  step "candidate versions on top of the installed one"
  installed=$(as_service "git rev-parse HEAD")
  versions=$(as_service "/usr/bin/python3 lab/upgrade-check.py candidates")
  eval "$versions"
  # The service account cannot read the system journal; a copy of the unit's lines since the
  # stage began is handed to it for the stage's journal checks.
  journal_checks() {
    guest "sudo journalctl -u sbarbase --since @$2 -o cat --no-pager > /tmp/channel-journal.txt \
      && sudo install -o sbarbase -g sbarbase -m 600 /tmp/channel-journal.txt /home/sbarbase/channel-journal.txt" || return 1
    as_service "/usr/bin/python3 lab/upgrade-check.py channel-journal $1 /home/sbarbase/channel-journal.txt"
  }
  rehearse_channel() {
    as_service "/usr/bin/python3 lab/upgrade-check.py before base=$base good=$good bad=$bad" || return 1
    as_service "/usr/bin/python3 lab/upgrade-check.py channel-source $SOURCE" || return 1
    step "back to the installed commit, then onto the base with upgrade.py and one restart"
    as_service "git checkout -q --detach $installed" || return 1
    guest "set -e
sudo mkdir -p /etc/systemd/system/sbarbase.service.d
printf '[Service]\nEnvironment=SBARBASE_RELEASE_SOURCE=$SOURCE\n' | sudo tee /etc/systemd/system/sbarbase.service.d/release-source.conf >/dev/null
sudo systemctl daemon-reload" || return 1
    as_service "/usr/bin/python3 lab/upgrade.py start --to $base" || return 1
    guest 'sudo systemctl restart sbarbase'
    phase confirmed || { printf 'FAIL: the move onto the base was not confirmed\n' >&2; return 1; }
    as_service "/usr/bin/python3 lab/upgrade-check.py channel-base $OPERATOR" || return 1
    for name in good broken automatic attended; do
      step "release $name through the channel"
      since=$(guest 'date +%s')
      as_service "/usr/bin/python3 lab/upgrade-check.py channel-apply $name $OPERATOR" || return 1
      journal_checks "$name" "$since" || return 1
    done
  }
  status=0
  rehearse_channel || status=1
  as_service "/usr/bin/python3 lab/upgrade-check.py evidence docs/evidence/vm-channel-checks.json channel-base channel-applied channel-rolled-back channel-automatic channel-attended" || status=1
  evidence vm-channel-checks.json || :
  exit "$status"
  ;;
environments)
  step "fill the installation to its environment limit and measure it"
  run_check vm-environment-limit.json bun lab/environment-limit-check.ts /home/sbarbase/operator.json
  ;;
soak)
  MINUTES="${1:-60}"; case "$MINUTES" in *[!0-9]*|'') fail "soak takes minutes" ;; esac
  step "soak for $MINUTES minutes: one sample every 5 minutes"
  run_check vm-soak.json /usr/bin/python3 lab/soak.py --minutes "$MINUTES"
  ;;
*) fail "unknown step: $COMMAND" ;;
esac
printf '\n%s: done. Evidence in %s/evidence\n' "$COMMAND" "$DIR"
