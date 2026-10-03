#!/bin/sh
# Build and inspect only the controller image; never execute its startup.
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
destination=${1:-"$repo/.lab/runtime-verification"}
mkdir -p "$destination"
destination=$(CDPATH= cd -- "$destination" && pwd)
attempt=0
while :; do
    run="runtime-$(date -u +%Y%m%dT%H%M%SZ)-$$-$attempt"
    output="$destination/$run"
    if mkdir "$output" 2>/dev/null; then break; fi
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 100 ]; then exit 1; fi
done
image="sbarbase-runtime-verify:$run"
container=''
built=0
cleanup() {
    if [ -n "$container" ]; then docker rm -f "$container" >/dev/null 2>&1 || true; fi
    if [ "$built" = 1 ]; then docker image rm "$image" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf 'Runtime build evidence: %s\n' "$output"
printf '{"phase":"building","passed":false}\n' > "$output/invocation.json"
if docker build --no-cache --iidfile "$output/image-id" --file "$repo/Dockerfile" --tag "$image" "$repo" > "$output/build.log" 2>&1; then
    built=1
else
    status=$?
    cat "$output/build.log"
    exit "$status"
fi
docker image inspect "$image" > "$output/image.json"
container=$(docker create -i --init --network none --cap-drop ALL --security-opt no-new-privileges \
    --user nobody --pids-limit 32 --memory 128m --cpus 0.25 --entrypoint /usr/bin/python3 "$image" -)
status=0
docker start --attach --interactive "$container" < "$repo/deploy/verify/runtime_inspect.py" > "$output/report.json" 2> "$output/probe.log" || status=$?
docker inspect "$container" > "$output/container.json"
if [ ! -s "$output/report.json" ]; then status=1; fi
printf '{"phase":"completed","exit_code":%s}\n' "$status" > "$output/invocation.json"
cat "$output/report.json"
printf 'Runtime build evidence: %s\n' "$output"
exit "$status"
