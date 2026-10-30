#!/bin/bash
# usage: [PRIV=dir] [EXTRA="runtime args"] run.sh NAME HOSTPORT [extra docker args...]
# Starts the pinned edge-runtime with the real lab/functions/main service, the work directory's
# function code and a private directory, mounted read-only the way durable_runtime.functions_binds
# does. Published on 127.0.0.1 only. The extra docker arguments come before the image.
WORK=${WORK:-$HOME/.cache/sbarbase-container-exp}
REPO=${REPO:-$HOME/R/Projects/P/sbarbase}
PRIV=${PRIV:-$WORK/priv}
NAME=$1; PORT=$2; shift 2
IMG=public.ecr.aws/supabase/edge-runtime@sha256:ad45164b7548bf5820ed7be714789c17ac2865edce79a09cfb4c3756c3d365a8
docker run -d --pull=never --name "$NAME" -p 127.0.0.1:$PORT:9000 \
 -e SB_FUNCTIONS_DIR=/home/deno/functions -e SB_SECRETS_FILE=/run/sbarbase/secrets.json -e SB_SELF_URL=http://127.0.0.1:9000 -e SB_JWT_SECRET=x -e SUPABASE_ANON_KEY=anon -e SUPABASE_SERVICE_ROLE_KEY=svc \
 -v "$REPO/lab/functions/main:/home/deno/main:ro" -v "$WORK/code:/home/deno/functions:ro" -v "$PRIV:/run/sbarbase:ro" \
 --memory 384m --memory-swap 384m --cpus 0.5 --pids-limit 256 "$@" $IMG start --main-service /home/deno/main -p 9000 $EXTRA
