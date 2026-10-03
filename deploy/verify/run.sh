#!/bin/sh
# The host requires only a POSIX shell and Docker with Linux container support.
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
destination=${1:-"$repo/.lab/portable-verification"}
mkdir -p "$destination"
destination=$(CDPATH= cd -- "$destination" && pwd)
attempt=0
while :; do
    run="run-$(date -u +%Y%m%dT%H%M%SZ)-$$-$attempt"
    output="$destination/$run"
    if mkdir "$output" 2>/dev/null; then break; fi
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 100 ]; then
        printf 'Could not create a fresh evidence directory.\n' >&2
        exit 1
    fi
done
image="sbarbase-source-verify:$run"
container=''
built=0
cleanup() {
    if [ -n "$container" ]; then docker rm -f "$container" >/dev/null 2>&1 || true; fi
    if [ "$built" = 1 ]; then docker image rm "$image" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf 'Evidence for this invocation: %s\n' "$output"
printf '{"phase":"building","passed":false}\n' > "$output/invocation.json"
if docker build --iidfile "$output/image-id" --file "$repo/deploy/verify/Dockerfile" --tag "$image" "$repo" > "$output/build.log" 2>&1; then
    cat "$output/build.log"
else
    status=$?
    cat "$output/build.log"
    printf '{"phase":"build-failed","passed":false,"exit_code":%s}\n' "$status" > "$output/invocation.json"
    exit "$status"
fi
built=1
docker image inspect "$image" > "$output/image.json"
container=$(docker create --init --network none --cap-drop ALL \
    --security-opt no-new-privileges --pids-limit 512 --memory 1g --memory-swap 1g --cpus 1 "$image")
status=0
docker start --attach "$container" || status=$?
docker cp "$container:/evidence/." "$output/"
docker inspect "$container" > "$output/container.json"
printf '{"phase":"completed","exit_code":%s}\n' "$status" > "$output/invocation.json"
printf 'Evidence: %s\n' "$output"
exit "$status"
