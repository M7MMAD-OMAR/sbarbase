#!/bin/bash
# The single-container experiments of results.json. Run setup.sh first. Throwaway containers fnr-ctr-*,
# host ports 19200-19299 on 127.0.0.1, all removed at the end.
WORK=${WORK:-$HOME/.cache/sbarbase-container-exp}; cd "$WORK"
IMG=public.ecr.aws/supabase/edge-runtime@sha256:ad45164b7548bf5820ed7be714789c17ac2865edce79a09cfb4c3756c3d365a8
. ./flags.sh
cleanup() { docker rm -f "$@" >/dev/null 2>&1; }

echo "=== 1. baseline: default flags (root, writable layer); what does the runtime write? (docker diff, cache part only)"
./run.sh fnr-ctr-base 19200 >/dev/null; sleep 4; ./hit.sh 19200
docker diff fnr-ctr-base | grep -E '^[ACD] /(root|tmp|home|run|var)' | grep -v '/npm/' | head -20
docker exec fnr-ctr-base sh -c 'id; echo HOME=$HOME DENO_DIR=$DENO_DIR'
cleanup fnr-ctr-base

echo; echo "=== 2. hardened, only /tmp writable, no DENO_DIR (HOME is / for an unlisted uid): npm functions must fail"
./run.sh fnr-ctr-a 19201 $HARD $TMP >/dev/null; sleep 4; ./hit.sh 19201
docker exec fnr-ctr-a sh -c 'id; echo HOME=$HOME'
docker logs fnr-ctr-a 2>&1 | grep -A1 "Failed caching" | head -3
cleanup fnr-ctr-a

echo; echo "=== 3. hardened, /tmp and the Deno cache tmpfs"
./run.sh fnr-ctr-b 19202 $HARD $TMP $CACHE >/dev/null; sleep 4; ./hit.sh 19202
docker diff fnr-ctr-b
docker exec fnr-ctr-b sh -c 'du -sk /deno-dir /tmp; grep -E "CapEff|CapBnd|NoNewPrivs" /proc/1/status; touch /x 2>&1; touch /home/deno/x 2>&1'
docker inspect fnr-ctr-b --format 'ReadonlyRootfs={{.HostConfig.ReadonlyRootfs}} CapDrop={{.HostConfig.CapDrop}} SecurityOpt={{.HostConfig.SecurityOpt}} User={{.Config.User}} Tmpfs={{.HostConfig.Tmpfs}}'
echo "-- readability check as the container user"
docker exec fnr-ctr-b sh -c "$CHECK && echo readable"
cleanup fnr-ctr-b

echo; echo "=== 4. the uid trap: a private directory (700) with secrets.json (600) owned by uid 2000"
cp -r priv priv_other
docker run --rm --pull=never --entrypoint chown -v "$WORK/priv_other:/d" $IMG -R 2000:2000 /d
for user in 1000:1000 2000:2000 65534:65534; do
  UIDGID=$user; . ./flags.sh
  port=$((19230 + ${user%%:*} % 100))
  PRIV=$WORK/priv_other ./run.sh fnr-ctr-uid $port $HARD $TMP $CACHE >/dev/null; sleep 4
  echo "-- private dir owner 2000, container user $user: $(curl -s -w ' [%{http_code}]' 127.0.0.1:$port/hello)"
  docker exec fnr-ctr-uid sh -c "$CHECK && echo 'check: readable' || echo 'check: NOT readable'"
  cleanup fnr-ctr-uid
done
UIDGID=1000:1000; . ./flags.sh
echo "-- private dir owner 1000 (the host user), container user 65534"
UIDGID=65534:65534; . ./flags.sh
./run.sh fnr-ctr-uid 19260 $HARD $TMP $CACHE >/dev/null; sleep 4
echo "$(curl -s -w ' [%{http_code}]' 127.0.0.1:19260/hello)"; docker exec fnr-ctr-uid sh -c "$CHECK && echo 'check: readable' || echo 'check: NOT readable'"
cleanup fnr-ctr-uid
UIDGID=1000:1000; . ./flags.sh
docker run --rm --pull=never --entrypoint rm -v "$WORK/priv_other:/d" $IMG -rf /d/secrets.json
docker run --rm --pull=never --entrypoint rmdir -v "$WORK:/w" $IMG /w/priv_other

echo; echo "=== 5. tmpfs size cap and memory charge (cgroup v2 memory.current inside the container)"
./run.sh fnr-ctr-f 19209 $HARD $TMP $CACHE >/dev/null; sleep 4
docker exec fnr-ctr-f sh -c 'echo "memory.current before: $(cat /sys/fs/cgroup/memory.current)"; head -c 100M /dev/zero > /tmp/f; echo "write of 100M into the tmpfs /tmp: rc=$?"; df -h /tmp | tail -1; echo "memory.current after: $(cat /sys/fs/cgroup/memory.current)"; grep -E "^shmem " /sys/fs/cgroup/memory.stat; rm /tmp/f'
cleanup fnr-ctr-f
