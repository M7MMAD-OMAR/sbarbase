#!/bin/sh
# Identity mode inspects public images; service modes run one owned fixture.
# A socket bind, even readonly, does not restrict Docker API permissions.
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
if [ -n "${DOCKER_CONTEXT:-}" ]; then
    endpoint=$(docker context inspect "$DOCKER_CONTEXT" --format '{{(index .Endpoints "docker").Host}}')
elif [ -n "${DOCKER_HOST:-}" ]; then
    endpoint=$DOCKER_HOST
else
    endpoint=$(docker context inspect --format '{{(index .Endpoints "docker").Host}}')
fi
case "$endpoint" in unix:///*) socket=${endpoint#unix://} ;; *) printf 'Local Unix Docker endpoint required.\n' >&2; exit 1 ;; esac
if [ ! -S "$socket" ]; then printf 'Selected Docker socket unavailable.\n' >&2; exit 1; fi
mode=${2:-identity}
case "$mode" in
    identity) probe=deploy/verify/image_identity_inspect.py ;;
    durable) probe=deploy/verify/durable_identity_inspect.py ;;
    studio) probe=deploy/verify/studio_identity_inspect.py ;;
    upgrade) probe=deploy/verify/upgrade_identity_inspect.py ;;
    data) probe=deploy/verify/data_tools_inspect.py ;;
    workflow) probe=deploy/verify/backup_workflow_inspect.py ;;
    fenced) probe=deploy/verify/fenced_restore_inspect.py ;;
    session) probe=deploy/verify/fenced_session_inspect.py ;;
    files) probe=deploy/verify/fenced_files_inspect.py ;;
    defaults) probe=deploy/verify/native_defaults_inspect.py ;;
    topology) probe=deploy/verify/native_topology_inspect.py ;;
    workers) probe=deploy/verify/native_worker_inspect.py ;;
    bootstrap) probe=deploy/verify/native_bootstrap_inspect.py ;;
    configured-effects) probe=deploy/verify/native_configured_effects_inspect.py ;;
    configmeta) probe=deploy/verify/native_config_metadata_inspect.py ;;
    configpreserve) probe=deploy/verify/native_config_preservation_inspect.py ;;
    *) printf 'Unknown identity probe mode.\n' >&2; exit 2 ;;
esac
destination=${1:-"$repo/.lab/image-identity-verification"}
mkdir -p "$destination"
destination=$(CDPATH= cd -- "$destination" && pwd)
attempt=0
while :; do
    run="identity-$(date -u +%Y%m%dT%H%M%SZ)-$$-$attempt"
    output="$destination/$run"
    if mkdir "$output" 2>/dev/null; then break; fi
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 100 ]; then exit 1; fi
done
source_image="sbarbase-source-verify:$run"
image="sbarbase-identity-verify:$run"
container=''
source_built=0
built=0
cleanup() {
    if [ "$mode" = configpreserve ]; then
        if [ -n "$container" ]; then docker rm -f "$container" >/dev/null 2>&1 || true; container=''; fi
        for helper in baseline seed reader; do
            helper_name="$run-configpreserve-$helper"
            helper_owner=$(docker container inspect --format '{{ index .Config.Labels "io.sbarbase.owner" }}' "$helper_name" 2>/dev/null) || helper_owner=''
            if [ "$helper_owner" = "sbarbase-fixture-$run" ]; then docker rm -f "$helper_name" >/dev/null 2>&1 || true; fi
        done
        for config_volume in "$run-bootstrap-config" "$run-bootstrap-confdir"; do
            config_owner=$(docker volume inspect --format '{{ index .Labels "io.sbarbase.owner" }}' "$config_volume" 2>/dev/null) || config_owner=''
            if [ "$config_owner" = "sbarbase-fixture-$run" ]; then docker volume rm "$config_volume" >/dev/null 2>&1 || true; fi
        done
    fi
    if [ "$mode" = configmeta ]; then
        if [ -n "$container" ]; then docker rm -f "$container" >/dev/null 2>&1 || true; container=''; fi
        docker rm -f "$run-configmeta-only" >/dev/null 2>&1 || true
    fi
    if [ "$mode" = bootstrap ] || [ "$mode" = configured-effects ]; then
        if [ -n "$container" ]; then docker rm -f "$container" >/dev/null 2>&1 || true; container=''; fi
        bootstrap_safe=1
        bootstrap_owner="sbarbase-fixture-$run"
        bootstrap_image='sha256:b3bfedb107413abb3b8cb0d0874b0414a1dceb3d55bc0c778de6ad22d1f7dc86'
        bootstrap_inspect() {
            inspect_kind=$1
            inspect_name=$2
            inspect_format=$3
            inspect_file="$output/fallback-$inspect_kind-$inspect_name"
            inspect_status=0
            docker "$inspect_kind" inspect --format "$inspect_format" "$inspect_name" > "$inspect_file-metadata.stdout" 2> "$inspect_file-metadata.stderr" || inspect_status=$?
            printf '%s\n' "$inspect_status" > "$inspect_file-metadata.status"
            inspect_value=$(cat "$inspect_file-metadata.stdout")
            inspect_stdout_bytes=$(wc -c < "$inspect_file-metadata.stdout")
            inspect_stderr_bytes=$(wc -c < "$inspect_file-metadata.stderr")
            inspect_state=unknown
            if [ "$inspect_status" -eq 0 ]; then
                if [ "$inspect_stderr_bytes" -eq 0 ] && [ "$inspect_stdout_bytes" -eq "$((${#inspect_value} + 1))" ]; then inspect_state=present; fi
            else
                inspect_absence_status=0
                docker "$inspect_kind" inspect "$inspect_name" > "$inspect_file-absence.stdout" 2> "$inspect_file-absence.stderr" || inspect_absence_status=$?
                printf '%s\n' "$inspect_absence_status" > "$inspect_file-absence.status"
                inspect_absence_stdout=$(cat "$inspect_file-absence.stdout")
                inspect_absence_stderr=$(cat "$inspect_file-absence.stderr")
                case "$inspect_kind" in
                    container) inspect_expected="Error response from daemon: No such container: $inspect_name" ;;
                    network) inspect_expected="Error response from daemon: network $inspect_name not found" ;;
                    volume) inspect_expected="Error response from daemon: get $inspect_name: no such volume" ;;
                    *) inspect_expected='' ;;
                esac
                inspect_stdout_bytes=$(wc -c < "$inspect_file-absence.stdout")
                inspect_stderr_bytes=$(wc -c < "$inspect_file-absence.stderr")
                if [ "$inspect_absence_status" -eq 1 ] && [ "$inspect_absence_stdout" = '[]' ] && [ "$inspect_stdout_bytes" -eq 3 ] && [ "$inspect_absence_stderr" = "$inspect_expected" ] && [ "$inspect_stderr_bytes" -eq "$((${#inspect_expected} + 1))" ]; then inspect_state=absent; fi
            fi
        }
        bootstrap_children='configpreserve-baseline configpreserve-seed configpreserve-reader defaults-db bootstrap-auth-1 bootstrap-auth-2'
        if [ "$mode" = configured-effects ]; then bootstrap_children="$bootstrap_children worker-http"; fi
        for child in $bootstrap_children; do
            child_name="$run-$child"
            child_image="$bootstrap_image"
            if [ "$child" = worker-http ]; then
                child_image=${verify_image_id:-}
                child_digest=${child_image#sha256:}
                case "$child_image" in sha256:*) ;; *) child_digest='' ;; esac
                case "$child_digest" in ''|*[!0123456789abcdef]*) child_digest='' ;; esac
                if [ "${#child_digest}" -ne 64 ]; then child_image=''; fi
            fi
            bootstrap_inspect container "$child_name" '{{.Id}}|{{.Image}}|{{.Name}}|{{index .Config.Labels "io.sbarbase.owner"}}'
            child_id=${inspect_value%%|*}
            case "$child_id" in ''|*[!0123456789abcdef]*) child_id='' ;; esac
            if [ "$inspect_state" = present ] && [ "${#child_id}" -eq 64 ] && [ "$inspect_value" = "$child_id|$child_image|/$child_name|$bootstrap_owner" ]; then
                remove_status=0
                docker rm -f "$child_id" > "$output/fallback-$child-remove.stdout" 2> "$output/fallback-$child-remove.stderr" || remove_status=$?
                printf '%s\n' "$remove_status" > "$output/fallback-$child-remove.status"
                if [ "$remove_status" -ne 0 ]; then bootstrap_safe=0; fi
            elif [ "$inspect_state" != absent ]; then
                bootstrap_safe=0
            fi
            absence_status=0
            docker container inspect "$child_name" > "$output/fallback-$child-absence.stdout" 2> "$output/fallback-$child-absence.stderr" || absence_status=$?
            printf '%s\n' "$absence_status" > "$output/fallback-$child-absence.status"
            absence_stdout=$(cat "$output/fallback-$child-absence.stdout")
            absence_stderr=$(cat "$output/fallback-$child-absence.stderr")
            expected_stderr="Error response from daemon: No such container: $child_name"
            stdout_bytes=$(wc -c < "$output/fallback-$child-absence.stdout")
            stderr_bytes=$(wc -c < "$output/fallback-$child-absence.stderr")
            if [ "$absence_status" -ne 1 ] || [ "$absence_stdout" != '[]' ] || [ "$stdout_bytes" -ne 3 ] || [ "$absence_stderr" != "$expected_stderr" ] || [ "$stderr_bytes" -ne "$((${#expected_stderr} + 1))" ]; then bootstrap_safe=0; fi
        done
        if [ "$bootstrap_safe" = 1 ]; then
            network_name="$run-defaults-net"
            bootstrap_inspect network "$network_name" '{{.Id}}|{{.Name}}|{{.Driver}}|{{.Scope}}|{{.Internal}}|{{index .Labels "io.sbarbase.owner"}}|{{len .Containers}}'
            network_id=${inspect_value%%|*}
            case "$network_id" in ''|*[!0123456789abcdef]*) network_id='' ;; esac
            if [ "$inspect_state" = present ] && [ "${#network_id}" -eq 64 ] && [ "$inspect_value" = "$network_id|$network_name|bridge|local|true|$bootstrap_owner|0" ]; then
                remove_status=0
                docker network rm "$network_id" > "$output/fallback-network-remove.stdout" 2> "$output/fallback-network-remove.stderr" || remove_status=$?
                printf '%s\n' "$remove_status" > "$output/fallback-network-remove.status"
                if [ "$remove_status" -ne 0 ]; then bootstrap_safe=0; fi
            elif [ "$inspect_state" != absent ]; then bootstrap_safe=0; fi
            bootstrap_volumes=''
            for volume_name in "$run-defaults-data" "$run-defaults-config" "$run-bootstrap-confdir"; do
                bootstrap_inspect volume "$volume_name" '{{.Name}}|{{.Driver}}|{{.Scope}}|{{index .Labels "io.sbarbase.owner"}}|{{len .Options}}'
                if [ "$inspect_state" = present ] && [ "$inspect_value" = "$volume_name|local|local|$bootstrap_owner|0" ]; then
                    bootstrap_volumes="$bootstrap_volumes $volume_name"
                elif [ "$inspect_state" != absent ]; then bootstrap_safe=0; fi
            done
            if [ "$bootstrap_safe" = 1 ]; then
                for volume_name in $bootstrap_volumes; do
                    remove_status=0
                    docker volume rm "$volume_name" > "$output/fallback-$volume_name-remove.stdout" 2> "$output/fallback-$volume_name-remove.stderr" || remove_status=$?
                    printf '%s\n' "$remove_status" > "$output/fallback-$volume_name-remove.status"
                    if [ "$remove_status" -ne 0 ]; then bootstrap_safe=0; break; fi
                done
            fi
        fi
        if [ "$bootstrap_safe" != 1 ]; then printf 'Bootstrap fallback admission failed; owned volume cleanup requires review.\n' >&2; fi
    fi
    if [ "$mode" = defaults ] || [ "$mode" = topology ] || [ "$mode" = workers ]; then
        if [ -n "$container" ]; then docker rm -f "$container" >/dev/null 2>&1 || true; container=''; fi
        if [ "$mode" = workers ]; then docker rm -f "$run-worker-http" >/dev/null 2>&1 || true; fi
        docker rm -f "$run-defaults-db" >/dev/null 2>&1 || true
        docker network rm "$run-defaults-net" >/dev/null 2>&1 || true
        docker volume rm "$run-defaults-data" "$run-defaults-config" >/dev/null 2>&1 || true
    fi
    if [ "$mode" = session ]; then
        if [ -n "$container" ]; then docker rm -f "$container" >/dev/null 2>&1 || true; container=''; fi
        docker rm -f "$run-session-db" >/dev/null 2>&1 || true
        docker network rm "$run-session-net" >/dev/null 2>&1 || true
    fi
    if [ "$mode" = fenced ] || [ "$mode" = files ]; then
        if [ -n "$container" ]; then docker rm -f "$container" >/dev/null 2>&1 || true; container=''; fi
        set -- "$run-fenced-db" "$run-fenced-storage"
        helper=1
        while [ "$helper" -le 128 ]; do
            set -- "$@" "$run-fenced-helper-$helper"
            helper=$((helper + 1))
        done
        docker rm -f "$@" >/dev/null 2>&1 || true
        docker network rm "$run-fenced-net" >/dev/null 2>&1 || true
        docker volume rm "$run-fenced-objects" >/dev/null 2>&1 || true
    fi
    if [ "$mode" = workflow ]; then
        docker rm -f "$run-workflow-db" >/dev/null 2>&1 || true
        helper=1
        while [ "$helper" -le 64 ]; do
            docker rm -f "$run-workflow-helper-$helper" >/dev/null 2>&1 || true
            helper=$((helper + 1))
        done
        docker network rm "$run-workflow-net" >/dev/null 2>&1 || true
        docker volume rm "$run-workflow-objects" >/dev/null 2>&1 || true
    fi
    if [ "$mode" = data ]; then
        docker rm -f "$run-data-source" "$run-data-db" >/dev/null 2>&1 || true
        docker rm -f "$run-data-helper-1" "$run-data-helper-2" "$run-data-helper-3" "$run-data-helper-4" >/dev/null 2>&1 || true
        docker network rm "$run-data-net" >/dev/null 2>&1 || true
        docker volume rm "$run-data-objects" >/dev/null 2>&1 || true
    fi
    if [ "$mode" = durable ] || [ "$mode" = studio ]; then
        docker rm -f "$run-$mode-service" >/dev/null 2>&1 || true
        docker network rm "$run-$mode-net" >/dev/null 2>&1 || true
    fi
    if [ -n "$container" ]; then docker rm -f "$container" >/dev/null 2>&1 || true; fi
    if [ "$built" = 1 ]; then docker image rm "$image" >/dev/null 2>&1 || true; fi
    if [ "$source_built" = 1 ]; then docker image rm "$source_image" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf 'Image identity evidence: %s\n' "$output"
printf '{"phase":"building","passed":false}\n' > "$output/invocation.json"
if docker build --iidfile "$output/source-image-id" --file "$repo/deploy/verify/Dockerfile" --tag "$source_image" "$repo" > "$output/source-build.log" 2>&1; then
    source_built=1
else
    status=$?; cat "$output/source-build.log"; exit "$status"
fi
if docker build --iidfile "$output/image-id" --build-arg "SOURCE_VERIFY_IMAGE=$source_image" --tag "$image" - < "$repo/deploy/verify/Dockerfile.identity" > "$output/build.log" 2>&1; then
    built=1
else
    status=$?; cat "$output/build.log"; exit "$status"
fi
docker image inspect "$image" > "$output/image.json"
verify_image_id=$(cat "$output/image-id")
container=$(docker create --entrypoint /usr/bin/python3 --env "SBARBASE_FIXTURE_ID=$run" --env "SBARBASE_VERIFY_IMAGE_ID=$verify_image_id" --env GOMAXPROCS=1 --init --network none --cap-drop ALL --security-opt no-new-privileges \
    --pids-limit 64 --memory 256m --cpus 1 \
    --mount "type=bind,source=$socket,target=/var/run/docker.sock,readonly" "$image" "$probe")
status=0
docker start --attach "$container" > "$output/run.log" 2>&1 || status=$?
cat "$output/run.log"
docker cp "$container:/evidence/." "$output/"
docker inspect "$container" > "$output/container.json"
if [ ! -s "$output/report.json" ]; then printf 'Missing identity report.\n' >&2; status=1; fi
printf '{"phase":"completed","exit_code":%s}\n' "$status" > "$output/invocation.json"
printf 'Image identity evidence: %s\n' "$output"
exit "$status"
