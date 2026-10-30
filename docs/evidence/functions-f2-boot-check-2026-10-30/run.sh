#!/bin/bash
# Live check of the boot route of lab/functions/main/index.ts in ONE throwaway container launched like the production Edge
# Functions container: pinned image, 384m, 0.5 CPU, 256 pids, --cap-drop ALL, no-new-privileges, read only root, the 32m tmpfs
# mounts and an unprivileged user. Prints one JSON line per probe. Remove it afterwards: docker rm -f fnr-boot-check
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
PORT=${PORT:-19102}
IMG=public.ecr.aws/supabase/edge-runtime@sha256:ad45164b7548bf5820ed7be714789c17ac2865edce79a09cfb4c3756c3d365a8
SECRET=jwt-secret-for-test
tokenfor() { /usr/bin/python3 - "$1" <<'PY'
import base64, hashlib, hmac, json, sys, time
b = lambda d: base64.urlsafe_b64encode(d).rstrip(b'=').decode()
head = b(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode())
body = b(json.dumps({'role': sys.argv[1], 'iss': 'sbarbase', 'exp': int(time.time()) + 3600}).encode())
print(head + '.' + body + '.' + b(hmac.new(b'jwt-secret-for-test', (head + '.' + body).encode(), hashlib.sha256).digest()))
PY
}
SERVICE=$(tokenfor service_role); ANON=$(tokenfor anon)
chmod -R a+rX "$HERE/functions" "$HERE/private" "$ROOT/lab/functions/main"; chmod 644 "$HERE/private/secrets.json"
docker rm -f fnr-boot-check >/dev/null 2>&1 || true
docker run -d --name fnr-boot-check --memory 384m --memory-swap 384m --cpus 0.5 --pids-limit 256 -p "127.0.0.1:$PORT:9000" \
  --user "$(id -u):$(id -g)" --cap-drop ALL --security-opt no-new-privileges --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=32m --tmpfs "/deno-dir:rw,nosuid,nodev,noexec,size=32m,mode=0700,uid=$(id -u),gid=$(id -g)" -e DENO_DIR=/deno-dir \
  -v "$ROOT/lab/functions/main:/home/deno/main:ro" -v "$HERE/functions:/home/deno/functions:ro" -v "$HERE/private:/run/sbarbase:ro" \
  -e SB_FUNCTIONS_DIR=/home/deno/functions -e SB_SECRETS_FILE=/run/sbarbase/secrets.json -e SB_SELF_URL=http://127.0.0.1:9000 -e SB_JWT_SECRET=$SECRET \
  -e SUPABASE_ANON_KEY="$ANON" -e SUPABASE_SERVICE_ROLE_KEY="$SERVICE" \
  "$IMG" start --main-service /home/deno/main -p 9000 >/dev/null
for _ in $(seq 1 60); do [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:$PORT/" || true)" = 404 ] && break; sleep 0.5; done
probe() { # label, method, path, token
  out=$(curl -s -o /tmp/boot-body -w '%{http_code}' --max-time 40 -X "$2" ${4:+-H "Authorization: Bearer $4"} "http://127.0.0.1:$PORT$3" || echo 000)
  printf '{"probe":"%s","status":%s,"body":%s}\n' "$1" "$out" "$(/usr/bin/python3 -c "import json;print(json.dumps(open('/tmp/boot-body').read()[:300]))")"
}
probe "boot good, service role" POST /_sb_boot/good/v1abc-00000001 "$SERVICE"
probe "boot syntax error" POST /_sb_boot/syntax/v1abc-00000001 "$SERVICE"
probe "boot missing import" POST /_sb_boot/nomodule/v1abc-00000001 "$SERVICE"
probe "boot no entry point" POST /_sb_boot/noentry/v1abc-00000001 "$SERVICE"
probe "boot top level throw (not caught by create)" POST /_sb_boot/throws/v1abc-00000001 "$SERVICE"
probe "boot unknown version folder" POST /_sb_boot/good/v1abc-00000009 "$SERVICE"
probe "no token" POST /_sb_boot/good/v1abc-00000001 ""
probe "anon token" POST /_sb_boot/good/v1abc-00000001 "$ANON"
probe "wrong method" GET /_sb_boot/good/v1abc-00000001 "$SERVICE"
probe "bad name" POST /_sb_boot/..%2Fx/v1abc-00000001 "$SERVICE"
probe "bad version" POST "/_sb_boot/good/..%2F..%2Fx" "$SERVICE"
probe "a live function still answers (verify_jwt off)" GET /good ""
probe "an unknown function is 404" GET /nothing ""
docker inspect fnr-boot-check --format '{"user":"{{.Config.User}}","readonly":{{.HostConfig.ReadonlyRootfs}},"capdrop":{{json .HostConfig.CapDrop}},"security":{{json .HostConfig.SecurityOpt}}}'
docker rm -f fnr-boot-check >/dev/null
