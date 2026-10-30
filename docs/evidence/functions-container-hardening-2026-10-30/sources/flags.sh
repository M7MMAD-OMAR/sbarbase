# Sourced by the experiment scripts: the flags under test, as launch will pass them for uid 1000.
# The tmpfs sizes are resource_policy.HARDENED_TMP_SIZE and HARDENED_CACHE_SIZE (the first session
# measured 64m and 96m, which an OOM test then refused; see results.json).
UIDGID=${UIDGID:-1000:1000}
TMPSIZE=${TMPSIZE:-32m}
CACHESIZE=${CACHESIZE:-32m}
U=${UIDGID%%:*}; G=${UIDGID##*:}
HARD="--user $UIDGID --cap-drop ALL --security-opt no-new-privileges --read-only"
TMP="--tmpfs /tmp:rw,nosuid,nodev,noexec,size=$TMPSIZE"
CACHE="-e DENO_DIR=/deno-dir --tmpfs /deno-dir:rw,nosuid,nodev,noexec,size=$CACHESIZE,mode=0700,uid=$U,gid=$G"
CHECK='test -r /run/sbarbase/secrets.json && test -r /home/deno/main/index.ts && test -r /home/deno/functions && test -x /home/deno/functions'
