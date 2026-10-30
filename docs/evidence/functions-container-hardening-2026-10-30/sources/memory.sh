#!/bin/bash
# Does the container survive a worker call once both tmpfs are full? One container at a time, removed after each case.
# Each case: warm hello, sbjs (jsr:), sbnpm and npmfn (npm:), optionally fill /tmp and /deno-dir, then call alloc1 (a 130 MiB
# worker, limit 150), or alloc1 and alloc2 at once. The fill uses /dev/urandom: a first run with /dev/zero showed the kernel
# moving the zero pages out of the cgroup's charge, which says nothing about data that cannot be compressed. Prints the cgroup's memory figures, then OOMKilled and the exit code.
# usage: memory.sh old|new|two|restart|all
WORK=${WORK:-$HOME/.cache/sbarbase-container-exp}; cd "$WORK"
. ./flags.sh
mem() {
  docker exec "$1" sh -c 'st() { grep "^$1 " /sys/fs/cgroup/memory.stat | cut -d" " -f2; }; echo "memory.current=$(( $(cat /sys/fs/cgroup/memory.current) / 1048576 )) anon=$(( $(st anon) / 1048576 )) shmem=$(( $(st shmem) / 1048576 )) file=$(( $(st file) / 1048576 )) swap=$(( $(cat /sys/fs/cgroup/memory.swap.current) / 1048576 )) swap.max=$(cat /sys/fs/cgroup/memory.swap.max) peak=$(( $(cat /sys/fs/cgroup/memory.peak) / 1048576 )) MiB (limit 384)"'
}
state() { docker inspect "$1" --format 'State.Status={{.State.Status}} OOMKilled={{.State.OOMKilled}} ExitCode={{.State.ExitCode}} RestartCount={{.RestartCount}}'; }
case_() { # label port fill(yes|no) workers(1|2) mode(hard|default) [extra docker args]
  local label=$1 port=$2 fill=$3 workers=$4 mode=$5; shift 5
  echo "=== $label"
  if [ "$mode" = hard ]; then ./run.sh fnr-ctr-m $port $HARD $TMP $CACHE "$@" >/dev/null; else ./run.sh fnr-ctr-m $port "$@" >/dev/null; fi
  sleep 4
  for f in hello sbjs sbnpm npmfn; do curl -s -m 90 -o /dev/null 127.0.0.1:$port/$f; done
  echo -n "after warm-up:    "; mem fnr-ctr-m
  if [ "$fill" = yes ]; then
    docker exec fnr-ctr-m sh -c 'head -c 200M /dev/urandom > /tmp/fill; head -c 200M /dev/urandom > /deno-dir/fill' 2>/dev/null
    echo -n "both tmpfs full:  "; mem fnr-ctr-m
  fi
  echo "StartedAt before: $(docker inspect fnr-ctr-m --format '{{.State.StartedAt}}')"
  for n in $(seq 1 $workers); do curl -s -m 20 -o /dev/null -w "alloc$n http=%{http_code}\n" 127.0.0.1:$port/alloc$n & done; wait
  sleep 6
  state fnr-ctr-m
  if [ "$(docker inspect fnr-ctr-m --format '{{.State.Running}}')" = true ]; then
    echo -n "after the call:   "; mem fnr-ctr-m
    echo "StartedAt after:  $(docker inspect fnr-ctr-m --format '{{.State.StartedAt}}')"
    docker exec fnr-ctr-m sh -c 'echo "du /tmp $(du -sk /tmp | cut -f1) KiB, /deno-dir $(du -sk /deno-dir | cut -f1) KiB"'
    echo "hello after: $(curl -s -m 20 -w ' [%{http_code}]' 127.0.0.1:$port/hello)"
  else
    echo "the container is not running"
  fi
  docker rm -f fnr-ctr-m >/dev/null
}
sizes() { TMPSIZE=$1; CACHESIZE=$2; . ./flags.sh; }
case "${1:-all}" in
  old)   sizes 64m 96m; for i in 1 2 3; do case_ "64m + 96m (first sizes), both full, ONE 130 MiB worker, run $i" 19240 yes 1 hard; done ;;
  new)   sizes 32m 32m; for i in 1 2 3; do case_ "32m + 32m, both full, ONE 130 MiB worker, run $i" 19241 yes 1 hard; done ;;
  two)   sizes 32m 32m
         case_ "32m + 32m, tmpfs empty, TWO concurrent 130 MiB workers" 19242 no 2 hard
         case_ "32m + 32m, both full, TWO concurrent 130 MiB workers" 19245 yes 2 hard
         case_ "default flags (root, no tmpfs, as before this change), TWO concurrent 130 MiB workers" 19243 no 2 default ;;
  restart) sizes 64m 96m
         case_ "64m + 96m both full, TWO workers, no restart policy" 19246 yes 2 hard
         case_ "64m + 96m both full, TWO workers, --restart on-failure:3" 19244 yes 2 hard --restart on-failure:3 ;;
  all)   bash "$0" old; bash "$0" new; bash "$0" two; bash "$0" restart ;;
esac
