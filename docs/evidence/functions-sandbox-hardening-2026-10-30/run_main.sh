#!/bin/bash
# Starts one throwaway edge-runtime container, fnr-sbx-main, running the repository's lab/functions/main/index.ts
# (or MAIN) against the evidence functions, and waits until it answers. Remove it afterwards: docker rm -f fnr-sbx-main
#   S     directory holding functions/ (functions.json, v1/<name>/index.ts) and private/ (secrets.json); default: this one
#   MAIN  directory with the main service under test; default: lab/functions/main of the repository
#   PORT  host port on 127.0.0.1; default 19100
# Auth, REST and Storage are answered by the stub functions things, health and bucket through the main service itself.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
S=${S:-$HERE}
MAIN=${MAIN:-$ROOT/lab/functions/main}
PORT=${PORT:-19100}
IMG=public.ecr.aws/supabase/edge-runtime@sha256:ad45164b7548bf5820ed7be714789c17ac2865edce79a09cfb4c3756c3d365a8
docker rm -f fnr-sbx-main >/dev/null 2>&1 || true
docker run -d --name fnr-sbx-main --memory 384m --cpus 0.5 --pids-limit 256 -p "127.0.0.1:$PORT:9000" \
  -v "$MAIN:/home/deno/main:ro" -v "$S/functions:/home/deno/functions:ro" -v "$S/private:/run/sbarbase:ro" \
  -e SB_FUNCTIONS_DIR=/home/deno/functions -e SB_SECRETS_FILE=/run/sbarbase/secrets.json \
  -e SB_SELF_URL=http://127.0.0.1:9000 -e SB_JWT_SECRET=jwt-secret-for-test \
  -e SUPABASE_ANON_KEY=anon-test -e SUPABASE_SERVICE_ROLE_KEY=service-test \
  -e SB_AUTH_URL=http://127.0.0.1:9000 -e SB_REST_URL=http://127.0.0.1:9000 -e SB_STORAGE_URL=http://127.0.0.1:9000 \
  "$IMG" start --main-service /home/deno/main -p 9000 >/dev/null
for _ in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:$PORT/" || true)
  if [ "$code" = 404 ]; then echo "ready on 127.0.0.1:$PORT"; exit 0; fi
  sleep 0.5
done
echo "fnr-sbx-main did not become ready" >&2
docker logs fnr-sbx-main >&2 || true
exit 1
