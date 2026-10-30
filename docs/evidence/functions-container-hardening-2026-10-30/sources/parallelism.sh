#!/bin/bash
# Does --max-parallelism limit the user workers the main service creates? Three distinct functions
# that each answer after 4 s, called at once; a limit of 1 would take about 12 s if it held.
WORK=${WORK:-$HOME/.cache/sbarbase-container-exp}; cd "$WORK"
. ./flags.sh
docker rm -f fnr-ctr-g fnr-ctr-h >/dev/null 2>&1
docker run --rm --pull=never public.ecr.aws/supabase/edge-runtime@sha256:ad45164b7548bf5820ed7be714789c17ac2865edce79a09cfb4c3756c3d365a8 start --help | grep -A1 -E 'max-parallelism|--policy'
three() { for n in 1 2 3; do curl -s -m 40 -o /dev/null -w "slow$n %{http_code} %{time_total}s\n" 127.0.0.1:$1/slow$n & done; wait; }
echo "=== --max-parallelism 1 (default per_worker policy), 3 distinct functions at once"
EXTRA="--max-parallelism 1" ./run.sh fnr-ctr-g 19210 $HARD $TMP $CACHE >/dev/null; sleep 4; three 19210
echo "=== --max-parallelism 1, 5 calls to the same function at once"
for n in 1 2 3 4 5; do curl -s -m 40 -o /dev/null -w "slow1#$n %{http_code} %{time_total}s\n" 127.0.0.1:19210/slow1 & done; wait
docker rm -f fnr-ctr-g >/dev/null
echo "=== --policy per_request --max-parallelism 1, 3 distinct at once (exploratory)"
EXTRA="--policy per_request --max-parallelism 1" ./run.sh fnr-ctr-g 19210 $HARD $TMP $CACHE >/dev/null; sleep 4; three 19210
docker rm -f fnr-ctr-g >/dev/null
echo "=== control: no flag"
./run.sh fnr-ctr-h 19211 $HARD $TMP $CACHE >/dev/null; sleep 4; three 19211
docker rm -f fnr-ctr-h >/dev/null
