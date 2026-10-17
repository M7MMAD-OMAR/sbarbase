#!/bin/bash
# Live probe; starts one database container of its own (needs about 1.5 GiB free). Usage:
#   CPUS=1 PIDS=320 lab/capacity-drill.sh ENVIRONMENTS MEMORY MAX_CONNECTIONS [CLIENTS] [SECONDS]
#   CPUS=1 PIDS=320 lab/capacity-drill.sh 8 1024m 200 23 60
# Results behind docs/evidence/capacity-drill-2026-10-16.json.
#
# Capacity drill: one pinned database container, N environment databases, each loaded with the
# connections Sbarbase gives an environment (Auth 6 + REST 6 + Storage 6) running a mixed
# read/write pgbench workload at once. Prints peak memory and whether the kernel killed anything.
set -u
ENVS=$1; MEM=$2; MAXCONN=$3; CLIENTS=${4:-18}; SECONDS_RUN=${5:-90}
IMAGE=$(/usr/bin/python3 -c "import json;print(json.load(open('$(dirname "$0")/distro-image.lock.json'))['tag'])")
WORK=$(mktemp -d)
NAME=sbar-capacity-drill
docker rm -f $NAME >/dev/null 2>&1
docker run -d --rm --name $NAME --label io.sbarbase.owner=capacity-drill --memory $MEM --memory-swap $MEM --cpus ${CPUS:-1} --pids-limit ${PIDS:-128} \
  -e POSTGRES_PASSWORD=drill-only-password $IMAGE postgres -c config_file=/etc/postgresql/postgresql.conf -c max_connections=$MAXCONN >/dev/null
for i in $(seq 1 120); do docker exec $NAME pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1 && break; sleep 1; done
sleep 5
sql() { docker exec -i $NAME psql -X -qAt -U supabase_admin -d postgres "$@"; }
for e in $(seq 1 $ENVS); do
  echo "CREATE DATABASE env$e;" | sql >/dev/null
  docker exec $NAME pgbench -h 127.0.0.1 -U supabase_admin -i -s 5 -q env$e >/dev/null 2>&1
done
echo "baseline: $(docker stats --no-stream --format '{{.MemUsage}}' $NAME)"
PEAK=0
( while docker inspect $NAME >/dev/null 2>&1; do
    m=$(docker stats --no-stream --format '{{.MemUsage}}' $NAME | awk '{print $1}')
    echo "$m" >> $WORK/mem.log
    sleep 2; done ) &
SAMPLER=$!
: > $WORK/mem.log
pids=()
for e in $(seq 1 $ENVS); do
  docker exec $NAME pgbench -h 127.0.0.1 -U supabase_admin -c $CLIENTS -j 2 -T $SECONDS_RUN -n env$e > $WORK/pgbench-$e.log 2>&1 &
  pids+=($!)
done
fail=0; for p in "${pids[@]}"; do wait $p || fail=$((fail+1)); done
kill $SAMPLER 2>/dev/null
tps=$(grep -h "^tps" $WORK/pgbench-*.log | awk '{s+=$3} END {printf "%.0f", s}')
lat=$(grep -h "latency average" $WORK/pgbench-*.log | awk '{s+=$4;n++} END {printf "%.1f", s/n}')
peak=$(sort -h $WORK/mem.log | tail -1)
oom=$(docker inspect $NAME --format '{{.State.OOMKilled}}' 2>/dev/null || echo gone)
alive=$(docker exec $NAME pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1 && echo yes || echo NO)
kills=$(docker exec $NAME sh -c "grep -c 'terminated by signal 9' /proc/1/fd/2 2>/dev/null" 2>/dev/null || true)
echo "envs=$ENVS mem=$MEM max_connections=$MAXCONN clients/env=$CLIENTS  failed_runs=$fail total_tps=$tps avg_latency_ms=$lat peak_mem=$peak oom_killed=$oom db_alive=$alive"
docker logs $NAME 2>&1 | grep -iE "out of memory|terminated by signal|server process .* was terminated" | head -3
docker rm -f $NAME >/dev/null 2>&1
rm -rf "$WORK"
