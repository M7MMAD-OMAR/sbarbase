#!/bin/bash
# usage: repeat.sh hardened|root
# A fresh container per round with the jsr: function (sbjs) as the first request, then the npm: ones.
WORK=${WORK:-$HOME/.cache/sbarbase-container-exp}; cd "$WORK"
. ./flags.sh
MODE=${1:-hardened}
for i in 1 2 3 4 5; do
  P=$((19220+i))
  if [ "$MODE" = hardened ]; then ./run.sh fnr-ctr-r$i $P $HARD $TMP $CACHE >/dev/null; else ./run.sh fnr-ctr-r$i $P >/dev/null; fi
  sleep 4
  line="$MODE round $i:"
  for f in sbjs sbnpm npmfn hello; do
    code=$(curl -s -m 90 -o /dev/null -w '%{http_code}' 127.0.0.1:$P/$f)
    line="$line $f=$code"
  done
  echo "$line"
  if [ "$MODE" = hardened ] && [ $i = 1 ]; then docker exec fnr-ctr-r$i sh -c 'echo "deno cache KiB after the four functions: $(du -sk /deno-dir | cut -f1)"'; fi
  docker rm -f fnr-ctr-r$i >/dev/null
done
