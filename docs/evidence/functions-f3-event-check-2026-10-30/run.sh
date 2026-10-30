#!/bin/bash
# Live check of the event worker (lab/functions/event-worker/index.ts) with the main service (lab/functions/main/index.ts) in ONE throwaway container launched
# like the production Edge Functions container (pinned image, 384m, 0.5 CPU, 256 pids, cap-drop ALL, no-new-privileges, read only root, 32m noexec tmpfs,
# unprivileged user) and with `--event-worker /home/deno/event`. Calls three functions, waits for the runtime to drop their workers, then reads
# /_sb_stats/<name> as the control plane does. Takes about two minutes. Remove the container afterwards: docker rm -f fnr-event-check
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
PORT=${PORT:-19104}
WAIT=${WAIT:-100}
IMG=public.ecr.aws/supabase/edge-runtime@sha256:ad45164b7548bf5820ed7be714789c17ac2865edce79a09cfb4c3756c3d365a8
tokenfor() { /usr/bin/python3 - "$1" <<'PY'
import base64, hashlib, hmac, json, sys, time
b = lambda d: base64.urlsafe_b64encode(d).rstrip(b'=').decode()
head = b(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode())
body = b(json.dumps({'role': sys.argv[1], 'iss': 'sbarbase', 'exp': int(time.time()) + 3600}).encode())
print(head + '.' + body + '.' + b(hmac.new(b'jwt-secret-for-test', (head + '.' + body).encode(), hashlib.sha256).digest()))
PY
}
SERVICE=$(tokenfor service_role); ANON=$(tokenfor anon); EVENTS=$(tokenfor sb_events)
chmod -R a+rX "$HERE/functions" "$HERE/private" "$ROOT/lab/functions/main" "$ROOT/lab/functions/event-worker"; chmod 644 "$HERE/private/secrets.json"
docker rm -f fnr-event-check >/dev/null 2>&1 || true
docker run -d --name fnr-event-check --memory 384m --memory-swap 384m --cpus 0.5 --pids-limit 256 -p "127.0.0.1:$PORT:9000" \
  --user "$(id -u):$(id -g)" --cap-drop ALL --security-opt no-new-privileges --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=32m --tmpfs "/deno-dir:rw,nosuid,nodev,noexec,size=32m,mode=0700,uid=$(id -u),gid=$(id -g)" -e DENO_DIR=/deno-dir \
  -v "$ROOT/lab/functions/main:/home/deno/main:ro" -v "$ROOT/lab/functions/event-worker:/home/deno/event:ro" -v "$HERE/functions:/home/deno/functions:ro" -v "$HERE/private:/run/sbarbase:ro" \
  -e SB_FUNCTIONS_DIR=/home/deno/functions -e SB_SECRETS_FILE=/run/sbarbase/secrets.json -e SB_SELF_URL=http://127.0.0.1:9000 -e SB_JWT_SECRET=jwt-secret-for-test \
  -e SUPABASE_ANON_KEY="$ANON" -e SUPABASE_SERVICE_ROLE_KEY="$SERVICE" \
  "$IMG" start --main-service /home/deno/main --event-worker /home/deno/event -p 9000 >/dev/null
for _ in $(seq 1 60); do [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:$PORT/" || true)" = 404 ] && break; sleep 0.5; done
call() { printf '{"call":"%s","status":%s}\n' "$1" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 60 "http://127.0.0.1:$PORT/$1" || echo 000)"; }
call talk; call talk; call burn; call boom
probe() { # label, method, path, token
  out=$(curl -s -o /tmp/ev-body -w '%{http_code}' --max-time 30 -X "$2" ${4:+-H "Authorization: Bearer $4"} ${5:+-H "Content-Type: application/json" --data "$5"} "http://127.0.0.1:$PORT$3" || echo 000)
  printf '{"probe":"%s","status":%s,"body":%s}\n' "$1" "$out" "$(/usr/bin/python3 -c "import json;print(json.dumps(open('/tmp/ev-body').read()[:1400]))")"
}
probe "forge events with the service role token" POST /_sb_events "$SERVICE" '[{"event_type":"Shutdown","event":{"cpu_time_used":999999},"metadata":{"service_path":"/home/deno/functions/v1/talk"}}]'
probe "forge events with the anon token" POST /_sb_events "$ANON" '[]'
probe "forge events with a token of role sb_events signed with the raw JWT secret (what Auth could be made to mint)" POST /_sb_events "$EVENTS" '[{"event_type":"Shutdown","event":{"cpu_time_used":999999},"metadata":{"service_path":"/home/deno/functions/v1/talk"}}]'
probe "forge events with no token" POST /_sb_events "" '[]'
probe "read stats with no token" GET /_sb_stats/talk ""
probe "read stats with the anon token" GET /_sb_stats/talk "$ANON"
sleep "$WAIT"
for name in talk burn boom nothing; do probe "stats of $name (service role)" GET "/_sb_stats/$name" "$SERVICE"; done
probe "stats with a bad name" GET "/_sb_stats/..%2Fx" "$SERVICE"
probe "the control plane's own token cannot post events" POST /_sb_events "$SERVICE" '[]'
docker rm -f fnr-event-check >/dev/null
