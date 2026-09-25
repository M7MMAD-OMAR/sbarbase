#!/usr/bin/env bash
# Empty-server rehearsal inside a local virtual machine.
#
# Until a real server exists, this is how an install from nothing is tested:
# a disposable VM booted from a stock cloud image, a clean clone of the current
# commit, the one-command acceptance with the first-project step, then a reboot
# that must bring the service back. Nothing touches the host's Docker, systemd
# or ports other than one loopback SSH forward.
#
#   lab/vm-rehearsal.sh --image /path/Fedora-Cloud-Base-Generic-44-*.qcow2
#   lab/vm-rehearsal.sh --image IMG --cpus 4 --memory 6144 --dir ~/.local/share/sbarbase-vm
#   lab/vm-rehearsal.sh --dir DIR --stop          # power the VM off
#   --organization NAME names the first client (a restore drill needs a name the backup
#   does not use, because a restore never attaches a backup to a client by name)
#   --preload-images copies every pinned image the host already has into the guest by
#   digest before the install, so a slow link downloads only what the host lacks
#
# The image is not downloaded for you: fetch a Fedora 44 Cloud Base qcow2 from
# fedoraproject.org and verify its checksum first. The guest needs
# /usr/bin/python3 3.12 or newer; Fedora 44 ships 3.14.
#
# Requirements on the host: qemu-system-x86_64 with KVM, qemu-img, genisoimage,
# ssh, git, bun. The VM's disk is a copy-on-write overlay, so the base image is
# never written. On btrfs the directory is marked No_COW (database I/O inside a
# copy-on-write image on a copy-on-write filesystem is what stalls desktops).
# A watchdog powers the VM off if host MemAvailable falls under 3 GiB.
#
# Evidence is copied back to DIR/evidence/. The operator password is generated
# inside the guest and never leaves it.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE=""
DIR="${HOME}/.local/share/sbarbase-vm"
CPUS=4
MEMORY=6144
SSH_PORT=2222
STOP=0
ORGANIZATION="First client"
PRELOAD=0
fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }
step() { printf '\n== %s\n' "$1"; }
wait_seconds() { /usr/bin/python3 -c "import time; time.sleep($1)"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --image) shift; IMAGE="${1:-}" ;;
    --dir) shift; DIR="${1:-}" ;;
    --cpus) shift; CPUS="${1:-}" ;;
    --memory) shift; MEMORY="${1:-}" ;;
    --ssh-port) shift; SSH_PORT="${1:-}" ;;
    --organization) shift; ORGANIZATION="${1:-}" ;;
    --preload-images) PRELOAD=1 ;;
    --stop) STOP=1 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
  shift
done
case "$CPUS$MEMORY$SSH_PORT" in *[!0-9]*) fail "--cpus, --memory and --ssh-port take numbers" ;; esac

SSH=(ssh -q -i "$DIR/id_ed25519" -p "$SSH_PORT" -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$DIR/known_hosts" -o ConnectTimeout=5 ops@127.0.0.1)
SCP=(scp -q -i "$DIR/id_ed25519" -P "$SSH_PORT" -o UserKnownHostsFile="$DIR/known_hosts")
guest() { "${SSH[@]}" "$@"; }

running() { [ -f "$DIR/qemu.pid" ] && kill -0 "$(cat "$DIR/qemu.pid")" 2>/dev/null; }
power_off() {
  running || return 0
  printf 'system_powerdown\n' | socat - "UNIX-CONNECT:$DIR/monitor.sock" >/dev/null 2>&1 || kill "$(cat "$DIR/qemu.pid")"
  for _ in $(seq 1 40); do running || return 0; wait_seconds 3; done
  kill -9 "$(cat "$DIR/qemu.pid")" 2>/dev/null || :
}
if [ "$STOP" = "1" ]; then power_off; printf 'VM stopped\n'; exit 0; fi

[ -n "$IMAGE" ] && [ -f "$IMAGE" ] || fail "--image must name a local cloud image (qcow2); it is never downloaded for you"
for tool in qemu-system-x86_64 qemu-img genisoimage ssh scp socat git bun; do
  command -v "$tool" >/dev/null 2>&1 || fail "$tool is not installed"
done
[ -w /dev/kvm ] || fail "/dev/kvm is not writable by this user"
running && fail "a VM from $DIR is already running; stop it with --stop"
if ss -ltn | grep -q "127.0.0.1:$SSH_PORT "; then fail "port $SSH_PORT is taken; pass --ssh-port"; fi
available=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
[ "$available" -gt $((MEMORY + 3072)) ] || fail "host has $available MiB available; a $MEMORY MiB guest needs that plus 3072 MiB for the desktop"

step "fresh VM disk and cloud-init seed"
mkdir -p "$DIR"
chattr +C "$DIR" 2>/dev/null || :
rm -f "$DIR/disk.qcow2" "$DIR/known_hosts"
[ -f "$DIR/id_ed25519" ] || ssh-keygen -q -t ed25519 -N '' -f "$DIR/id_ed25519" -C sbarbase-vm
qemu-img create -q -f qcow2 -b "$(readlink -f "$IMAGE")" -F qcow2 "$DIR/disk.qcow2" 60G
cat > "$DIR/user-data" <<EOF
#cloud-config
hostname: sbarbase-clean
users:
  - name: ops
    groups: [wheel]
    sudo: "ALL=(ALL) NOPASSWD:ALL"
    shell: /bin/bash
    ssh_authorized_keys:
      - $(cat "$DIR/id_ed25519.pub")
ssh_pwauth: false
growpart: {mode: auto, devices: ['/']}
packages: [moby-engine, git, python3-cryptography, rsync, tar]
runcmd:
  - systemctl enable --now docker
  - usermod -aG docker ops
  - touch /var/lib/cloud-ready
EOF
printf 'instance-id: sbarbase-clean-%s\nlocal-hostname: sbarbase-clean\n' "$(date +%s)" > "$DIR/meta-data"
genisoimage -quiet -output "$DIR/seed.iso" -volid cidata -joliet -rock "$DIR/user-data" "$DIR/meta-data"

step "boot: $CPUS vCPU, $MEMORY MiB"
qemu-system-x86_64 -name sbarbase-clean -enable-kvm -machine q35 -cpu host -smp "$CPUS" -m "$MEMORY" \
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

if [ "$PRELOAD" = "1" ]; then
  step "pinned images the host already has, copied into the guest"
  for ref in $(grep -rhoE '"[a-z0-9./-]+@sha256:[0-9a-f]{64}"' "$REPO_ROOT"/lab/*.lock.json | tr -d '"' | sort -u); do
    if docker image inspect "$ref" >/dev/null 2>&1; then
      # A loaded image can arrive without its name; pulling the digest names it and
      # downloads only the manifest, because the layers are already there.
      docker save "$ref" | guest 'sudo docker load -q' >/dev/null && guest "sudo docker pull -q $ref" >/dev/null \
        && printf 'copied %s\n' "${ref%%@*}"
    else
      printf 'not on the host, the install pulls it: %s\n' "${ref%%@*}"
    fi
  done
fi

step "clean clone of $(git -C "$REPO_ROOT" rev-parse --short HEAD) and the service account"
git -C "$REPO_ROOT" bundle create "$DIR/repo.bundle" HEAD >/dev/null 2>&1
"${SCP[@]}" "$DIR/repo.bundle" ops@127.0.0.1:/tmp/repo.bundle
"${SCP[@]}" "$(readlink -f "$(command -v bun)")" ops@127.0.0.1:/tmp/bun
guest 'set -e
sudo useradd -m -s /bin/bash sbarbase && sudo usermod -aG docker sbarbase
sudo install -d -o sbarbase -g sbarbase /opt/sbarbase
sudo -u sbarbase git clone -q /tmp/repo.bundle /opt/sbarbase
sudo -u sbarbase install -D -m 755 /tmp/bun /home/sbarbase/.bun/bin/bun
cd /opt/sbarbase
/usr/bin/python3 -c "import json,secrets;print(json.dumps({\"email\":\"operator@example.com\",\"password\":secrets.token_urlsafe(24),\"organization\":\"'"$ORGANIZATION"'\"}))" \
  | sudo -u sbarbase /usr/bin/python3 lab/operator_file.py --stdin /home/sbarbase/operator.json'

step "one-command acceptance with the first project"
started=$(date +%s)
guest 'cd /opt/sbarbase && sudo deploy/server-acceptance.sh --rehearse --install-unit --first-project \
  --service-user sbarbase --home /home/sbarbase --bun-dir /home/sbarbase/.bun/bin \
  --docker-host unix:///var/run/docker.sock --bootstrap-file /home/sbarbase/operator.json' | tee "$DIR/acceptance.log" \
  || fail "acceptance failed in the VM; the log is $DIR/acceptance.log"
printf 'acceptance took %s s\n' "$(( $(date +%s) - started ))"

step "reboot: the service must come back on its own"
guest 'sudo systemctl reboot' || :
wait_seconds 20
for _ in $(seq 1 60); do guest 'systemctl is-active --quiet sbarbase && sudo test -f /opt/sbarbase/.lab/upstream/server.json' 2>/dev/null && break; wait_seconds 5; done
guest 'systemctl is-active --quiet sbarbase' || fail "sbarbase.service is not active after the reboot"
# systemd says active as soon as the supervisor starts; the service is back once its console answers.
guest 'cd /opt/sbarbase && sudo -u sbarbase /usr/bin/python3 lab/install_server.py wait-console --timeout 300' \
  || fail "sbarbase.service is active after the reboot but its console did not answer"
printf 'ok: sbarbase.service active after reboot and its console answers\n'

step "footprint and evidence"
guest 'docker stats --no-stream --format "{{.Name}} {{.MemUsage}}"; free -m | sed -n 2p'
mkdir -p "$DIR/evidence"
guest 'cd /opt/sbarbase/docs/evidence && tar cf - server-acceptance-rehearsal.json first-project-check.json supervisor-unit.json console-build.json console-serve.json tls-termination.json' \
  | tar xf - -C "$DIR/evidence"
printf '\nVM rehearsal: PASSED. Evidence in %s/evidence. Stop the VM with: lab/vm-rehearsal.sh --dir %s --stop\n' "$DIR" "$DIR"
