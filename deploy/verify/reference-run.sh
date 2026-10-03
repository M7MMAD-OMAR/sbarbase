#!/bin/sh
# Online public metadata verification only; no host socket, paths or credentials.
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
mode=${1:-verify}
case "$mode" in verify|freeze) ;; *) printf 'Usage: reference-run.sh [verify|freeze] [output-directory]\n' >&2; exit 2 ;; esac
destination=${2:-"$repo/.lab/reference-verification"}
mkdir -p "$destination"
destination=$(CDPATH= cd -- "$destination" && pwd)
attempt=0
while :; do
    run="reference-$(date -u +%Y%m%dT%H%M%SZ)-$$-$attempt"
    output="$destination/$run"
    if mkdir "$output" 2>/dev/null; then break; fi
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 100 ]; then exit 1; fi
done
source_image="sbarbase-source-verify:$run"
image="sbarbase-reference-verify:$run"
container=''
source_built=0
built=0
cleanup() {
    if [ -n "$container" ]; then docker rm -f "$container" >/dev/null 2>&1 || true; fi
    if [ "$built" = 1 ]; then docker image rm "$image" >/dev/null 2>&1 || true; fi
    if [ "$source_built" = 1 ]; then docker image rm "$source_image" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf 'Reference metadata evidence: %s\n' "$output"
printf '{"phase":"building","passed":false}\n' > "$output/invocation.json"
if docker build --iidfile "$output/source-image-id" --file "$repo/deploy/verify/Dockerfile" --tag "$source_image" "$repo" > "$output/source-build.log" 2>&1; then
    source_built=1
else
    status=$?
    cat "$output/source-build.log"
    exit "$status"
fi
if docker build --iidfile "$output/image-id" --build-arg "SOURCE_VERIFY_IMAGE=$source_image" --tag "$image" - < "$repo/deploy/verify/Dockerfile.reference" > "$output/build.log" 2>&1; then
    built=1
else
    status=$?
    cat "$output/build.log"
    exit "$status"
fi
docker image inspect "$image" > "$output/image.json"
if [ "$mode" = verify ]; then
    set -- verify --bundle docs/engineering/benchmarks/supabase-v0.8.2.bundle.json --output /evidence/report.json
else
    set -- freeze --output /evidence/bundle.json
fi
container=$(docker create --init --cap-drop ALL --security-opt no-new-privileges \
    --pids-limit 128 --memory 1g --cpus 1 "$image" "$@")
status=0
docker start --attach "$container" > "$output/run.log" 2>&1 || status=$?
cat "$output/run.log"
docker cp "$container:/evidence/." "$output/"
docker inspect "$container" > "$output/container.json"
if [ "$mode" = verify ]; then expected_report="$output/report.json"; else expected_report="$output/bundle.json"; fi
if [ ! -s "$expected_report" ]; then
    printf 'Missing metadata evidence: %s\n' "$expected_report" >&2
    status=1
fi
printf '{"phase":"completed","exit_code":%s}\n' "$status" > "$output/invocation.json"
printf 'Reference metadata evidence: %s\n' "$output"
exit "$status"
