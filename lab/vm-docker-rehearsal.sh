#!/usr/bin/env bash
# Install with Docker, rehearsed from a bare Ubuntu 26.04 machine in a local VM.
#
# The CI docker-install job starts on a runner that already has Docker. This
# rehearsal starts one step earlier: a stock cloud image with nothing added, then
# exactly the commands docs/guides/docker.md gives, package install included:
# apt-get, a clone to /opt/sbarbase, deploy/compose.sh check, up and bootstrap,
# a first project through supabase-js, a reboot after which the console answers
# again, and the smoke check. Nothing touches the host's Docker, systemd or ports
# other than one loopback SSH forward.
#
#   lab/vm-docker-rehearsal.sh --image /path/ubuntu-26.04-server-cloudimg-amd64.img
#   lab/vm-docker-rehearsal.sh --image IMG --cpus 4 --memory 6144 --dir ~/.local/share/sbarbase-vm-docker
#   lab/vm-docker-rehearsal.sh --dir DIR --stop          # power the VM off
#
# The image is not downloaded for you: fetch the Ubuntu 26.04 server cloud image
# from cloud-images.ubuntu.com and verify it against SHA256SUMS first.
#
# The clone comes from a bundle of the current commit instead of GitHub, so an
# unpushed change can be rehearsed; everything after the clone is the guide's.
# bootstrap is interactive in the guide; here the same lab/bootstrap.py reads a
# generated operator file on standard input, and the password never leaves the
# guest.
#
# Requirements on the host: qemu-system-x86_64 with KVM, qemu-img, genisoimage,
# ssh, scp, socat, git. A watchdog powers the VM off if host MemAvailable falls
# under 3 GiB. Evidence is written to DIR/evidence/.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE=""
DIR="${HOME}/.local/share/sbarbase-vm-docker"
CPUS=4
MEMORY=6144
SSH_PORT=2223
STOP=0
fail() { printf 'FAIL: %s\n' "$1" >&2; record "$1" false; write_evidence false; exit 1; }
step() { printf '\n== %s\n' "$1"; }
wait_seconds() { /usr/bin/python3 -c "import time; time.sleep($1)"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --image) shift; IMAGE="${1:-}" ;;
    --dir) shift; DIR="${1:-}" ;;
    --cpus) shift; CPUS="${1:-}" ;;
    --memory) shift; MEMORY="${1:-}" ;;
    --ssh-port) shift; SSH_PORT="${1:-}" ;;
    --stop) STOP=1 ;;
    -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
  shift
done
case "$CPUS$MEMORY$SSH_PORT" in *[!0-9]*) printf -- '--cpus, --memory and --ssh-port take numbers\n' >&2; exit 2 ;; esac

SSH=(ssh -q -i "$DIR/id_ed25519" -p "$SSH_PORT" -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$DIR/known_hosts" -o ConnectTimeout=5 ops@127.0.0.1)
SCP=(scp -q -i "$DIR/id_ed25519" -P "$SSH_PORT" -o UserKnownHostsFile="$DIR/known_hosts")
guest() { "${SSH[@]}" "$@"; }

# One line per check, turned into the evidence file at the end.
CHECKS=()
record() { CHECKS+=("$2"$'\t'"$1"); }
write_evidence() {
  [ -d "$DIR" ] || return 0
  mkdir -p "$DIR/evidence"
  printf '%s\n' "${CHECKS[@]}" | /usr/bin/python3 -c '
import json, sys, datetime
checks = [{"passed": line.split("\t", 1)[0] == "true", "check": line.split("\t", 1)[1]}
          for line in sys.stdin.read().splitlines() if "\t" in line]
json.dump({
  "probe": "lab/vm-docker-rehearsal.sh",
  "run_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
  "commit": sys.argv[1],
  "host": "local KVM guest from the stock Ubuntu 26.04 server cloud image, " + sys.argv[2] + " vCPU, " + sys.argv[3] + " MiB, nothing preinstalled",
  "scope": "Install with Docker from a bare machine as docs/guides/docker.md gives it: distribution packages, clone (from a bundle of the commit), deploy/compose.sh check, up, bootstrap (lab/bootstrap.py on standard input instead of the interactive prompt), a first project through supabase-js, a reboot after which the console answers, and the smoke check. Not a real server: no public network, certificate or independent host.",
  "checks": checks, "count": len(checks),
  "passed": sys.argv[4] == "true" and all(c["passed"] for c in checks),
}, open(sys.argv[5], "w"), indent=1)
open(sys.argv[5], "a").write("\n")
' "$(git -C "$REPO_ROOT" rev-parse HEAD)" "$CPUS" "$MEMORY" "$1" "$DIR/evidence/vm-docker-install-checks.json"
}

running() { [ -f "$DIR/qemu.pid" ] && kill -0 "$(cat "$DIR/qemu.pid")" 2>/dev/null; }
power_off() {
  running || return 0
  printf 'system_powerdown\n' | socat - "UNIX-CONNECT:$DIR/monitor.sock" >/dev/null 2>&1 || kill "$(cat "$DIR/qemu.pid")"
  for _ in $(seq 1 40); do running || return 0; wait_seconds 3; done
  kill -9 "$(cat "$DIR/qemu.pid")" 2>/dev/null || :
}
if [ "$STOP" = "1" ]; then power_off; printf 'VM stopped\n'; exit 0; fi

[ -n "$IMAGE" ] && [ -f "$IMAGE" ] || { printf -- '--image must name a local Ubuntu 26.04 cloud image; it is never downloaded for you\n' >&2; exit 2; }
for tool in qemu-system-x86_64 qemu-img genisoimage ssh scp socat git; do
  command -v "$tool" >/dev/null 2>&1 || { printf '%s is not installed\n' "$tool" >&2; exit 2; }
done
[ -w /dev/kvm ] || { printf '/dev/kvm is not writable by this user\n' >&2; exit 2; }
running && { printf 'a VM from %s is already running; stop it with --stop\n' "$DIR" >&2; exit 2; }
if ss -ltn | grep -q "127.0.0.1:$SSH_PORT "; then printf 'port %s is taken; pass --ssh-port\n' "$SSH_PORT" >&2; exit 2; fi
available=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
[ "$available" -gt $((MEMORY + 3072)) ] || { printf 'host has %s MiB available; a %s MiB guest needs that plus 3072 MiB for the desktop\n' "$available" "$MEMORY" >&2; exit 2; }

step "fresh VM disk and cloud-init seed (no packages)"
mkdir -p "$DIR"
chattr +C "$DIR" 2>/dev/null || :
rm -f "$DIR/disk.qcow2" "$DIR/known_hosts" "$DIR/evidence/vm-docker-install-checks.json"
[ -f "$DIR/id_ed25519" ] || ssh-keygen -q -t ed25519 -N '' -f "$DIR/id_ed25519" -C sbarbase-vm-docker
qemu-img create -q -f qcow2 -b "$(readlink -f "$IMAGE")" -F qcow2 "$DIR/disk.qcow2" 60G
cat > "$DIR/user-data" <<EOF
#cloud-config
hostname: sbarbase-docker
users:
  - name: ops
    sudo: "ALL=(ALL) NOPASSWD:ALL"
    shell: /bin/bash
    ssh_authorized_keys:
      - $(cat "$DIR/id_ed25519.pub")
ssh_pwauth: false
growpart: {mode: auto, devices: ['/']}
runcmd:
  - touch /var/lib/cloud-ready
EOF
printf 'instance-id: sbarbase-docker-%s\nlocal-hostname: sbarbase-docker\n' "$(date +%s)" > "$DIR/meta-data"
genisoimage -quiet -output "$DIR/seed.iso" -volid cidata -joliet -rock "$DIR/user-data" "$DIR/meta-data"

step "boot: $CPUS vCPU, $MEMORY MiB"
qemu-system-x86_64 -name sbarbase-docker -enable-kvm -machine q35 -cpu host -smp "$CPUS" -m "$MEMORY" \
  -drive file="$DIR/disk.qcow2",if=virtio,cache=none,aio=native,discard=unmap \
  -drive file="$DIR/seed.iso",media=cdrom,readonly=on \
  -netdev user,id=n0,hostfwd=tcp:127.0.0.1:"$SSH_PORT"-:22 -device virtio-net-pci,netdev=n0 \
  -device virtio-balloon-pci,free-page-reporting=on \
  -display none -serial file:"$DIR/serial.log" -monitor unix:"$DIR/monitor.sock",server,nowait \
  -pidfile "$DIR/qemu.pid" -daemonize
( while running; do
    if [ "$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)" -lt 3072 ]; then
      printf '%s host memory low, powering the VM off\n' "$(date)" >> "$DIR/watchdog.log"; power_off; exit 0
    fi
    wait_seconds 10
  done ) >/dev/null 2>&1 &
for _ in $(seq 1 60); do guest test -f /var/lib/cloud-ready 2>/dev/null && break; wait_seconds 8; done
guest test -f /var/lib/cloud-ready || fail "the guest did not finish cloud-init; see $DIR/serial.log"
guest 'test ! -e /usr/bin/docker' || fail "the stock image already has Docker"
record "the stock image boots without Docker: $(guest '. /etc/os-release; printf %s "$PRETTY_NAME"')" true

step "packages, exactly as the guide gives them"
guest 'set -e
sudo apt-get update -q
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q docker.io docker-compose-v2 docker-buildx git
sudo systemctl enable --now docker' | tail -3 || fail "the package install failed"
versions=$(guest 'sudo docker version --format "{{.Server.Version}}"; sudo docker compose version --short; sudo docker buildx version | cut -d" " -f2')
record "docker.io, docker-compose-v2 and docker-buildx install: Engine $(printf '%s' "$versions" | sed -n 1p), Compose $(printf '%s' "$versions" | sed -n 2p), Buildx $(printf '%s' "$versions" | sed -n 3p)" true

step "clone of $(git -C "$REPO_ROOT" rev-parse --short HEAD) to /opt/sbarbase"
git -C "$REPO_ROOT" bundle create "$DIR/repo.bundle" HEAD >/dev/null 2>&1
"${SCP[@]}" "$DIR/repo.bundle" ops@127.0.0.1:/tmp/repo.bundle
guest 'sudo git clone -q /tmp/repo.bundle /opt/sbarbase' || fail "the clone failed"
record "clone to /opt/sbarbase" true

step "deploy/compose.sh check"
guest 'cd /opt/sbarbase && sudo deploy/compose.sh check' | tee "$DIR/check.log" || fail "deploy/compose.sh check refused this host; see $DIR/check.log"
record "deploy/compose.sh check admits the host" true

step "deploy/compose.sh up (builds the image and pulls the pinned services)"
started=$(date +%s)
guest 'cd /opt/sbarbase && sudo deploy/compose.sh up' > "$DIR/up.log" 2>&1 || fail "deploy/compose.sh up failed; see $DIR/up.log"
ready=0
for _ in $(seq 1 240); do
  if guest 'curl -fsS -o /dev/null http://127.0.0.1:8790/' 2>/dev/null; then ready=1; break; fi
  wait_seconds 5
done
[ "$ready" = 1 ] || { guest 'cd /opt/sbarbase && sudo deploy/compose.sh logs' > "$DIR/logs.txt" 2>&1 || :; fail "the console did not answer after up; see $DIR/logs.txt"; }
record "deploy/compose.sh up, console answers on 127.0.0.1:8790 after $(( $(date +%s) - started )) s" true

step "bootstrap: the first operator (generated, never printed)"
# The controller by its Compose labels; a bare docker compose would lack compose.sh's inputs.
controller='c=$(sudo docker ps -q --filter label=com.docker.compose.project=sbarbase --filter label=com.docker.compose.service=sbarbase); [ "$(printf "%s\n" "$c" | grep -c .)" = 1 ] || { echo "expected one controller container" >&2; exit 1; }'
guest "set -e
$controller"'
cd /opt/sbarbase
/usr/bin/python3 -c "import json,secrets;print(json.dumps({\"email\":\"operator@example.com\",\"password\":secrets.token_urlsafe(24),\"organization\":\"Docker rehearsal\"}))" \
  | sudo docker exec -i "$c" python3 lab/operator_file.py --stdin /opt/sbarbase/.secrets/operator.json
sudo docker exec "$c" sh -c "python3 lab/bootstrap.py --stdin < .secrets/operator.json"' || fail "bootstrap failed"
record "bootstrap creates the first operator and client" true

step "a first project through supabase-js"
guest "set -e; $controller"'; cd /opt/sbarbase && sudo docker exec "$c" bun lab/first-project-check.ts /opt/sbarbase/.secrets/operator.json --evidence /opt/sbarbase/.lab/vm-docker-first-project.json' \
  | tee "$DIR/first-project.log" || fail "the first project check failed; see $DIR/first-project.log"
record "first project, environment, key and a supabase-js sign-up through the gateway" true

step "reboot: Docker starts at boot and restart: unless-stopped brings Sbarbase back"
guest 'sudo systemctl reboot' || :
wait_seconds 20
back=0
for _ in $(seq 1 120); do
  if guest 'curl -fsS -o /dev/null http://127.0.0.1:8790/' 2>/dev/null; then back=1; break; fi
  wait_seconds 5
done
[ "$back" = 1 ] || fail "the console did not answer after the reboot"
record "console answers again after a reboot" true
guest 'cd /opt/sbarbase && sudo deploy/compose.sh smoke' | tee "$DIR/smoke.log" || fail "deploy/compose.sh smoke failed after the reboot; see $DIR/smoke.log"
record "deploy/compose.sh smoke passes after the reboot" true

step "footprint and evidence"
guest 'sudo docker stats --no-stream --format "{{.Name}} {{.MemUsage}}"; free -m | sed -n 2p'
mkdir -p "$DIR/evidence"
guest 'sudo cat /opt/sbarbase/.lab/vm-docker-first-project.json' > "$DIR/evidence/vm-docker-first-project.json"
write_evidence true
printf '\nVM Docker rehearsal: PASSED. Evidence in %s/evidence. Stop the VM with: lab/vm-docker-rehearsal.sh --dir %s --stop\n' "$DIR" "$DIR"
