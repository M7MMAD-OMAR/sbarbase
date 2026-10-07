#!/bin/sh
# Fixed Compose commands route every installation or startup mutation through admission.
set -eu
refuse() { printf '%s\n' "Docker profile refused [$1]: $2. Action: $3." >&2; exit 1; }
release=
case "$#:${1-}" in
    2:upgrade) release=$2;;
    1:check|1:up|1:build|1:pull|1:start|1:restart|1:ps|1:logs|1:down|1:bootstrap|1:smoke) ;;
    *) refuse invalid_arguments 'The command or its argument count is invalid' 'Use check, up, build, pull, start, restart, ps, logs, down, bootstrap or smoke; upgrade requires one release tag';;
esac
command=$1
command -v tr >/dev/null 2>&1 || refuse prerequisite_missing 'Required utility tr is unavailable' 'Install the documented Linux utility prerequisites'
# Environment values are opaque scalars. Reject every control byte before parsing.
scalar() {
    clean=$(printf '%s' "$2" | LC_ALL=C tr -d '[:cntrl:]')
    [ "$clean" = "$2" ] || refuse deployment_scalar_invalid "Declared input $1 contains control characters" 'Use exactly one plain scalar value without line breaks, tabs or other control characters'
}
scalar SBARBASE_DOCKER_PROFILE "${SBARBASE_DOCKER_PROFILE-}"
scalar SBARBASE_CONTAINER "${SBARBASE_CONTAINER-}"
scalar SBARBASE_DOCKER_DATA_ROOT "${SBARBASE_DOCKER_DATA_ROOT-}"
scalar SBARBASE_DOCKER_SOCKET "${SBARBASE_DOCKER_SOCKET-}"
scalar SBARBASE_ROOT "${SBARBASE_ROOT-}"
scalar SBARBASE_COMPOSE_PROJECT "${SBARBASE_COMPOSE_PROJECT-}"
scalar COMPOSE_PROJECT_NAME "${COMPOSE_PROJECT_NAME-}"
scalar SBARBASE_CONSOLE_PORT "${SBARBASE_CONSOLE_PORT-}"
scalar SBARBASE_DATABASE_PORT "${SBARBASE_DATABASE_PORT-}"
scalar SBARBASE_DATABASE_BIND "${SBARBASE_DATABASE_BIND-}"
scalar SBARBASE_PUBLIC_URL "${SBARBASE_PUBLIC_URL-}"
scalar SBARBASE_RELEASE_SOURCE "${SBARBASE_RELEASE_SOURCE-}"
scalar SBARBASE_BACKUP_HOUR "${SBARBASE_BACKUP_HOUR-}"
scalar SBARBASE_BACKUP_KEEP "${SBARBASE_BACKUP_KEEP-}"
scalar SBARBASE_UPLOAD_LIMIT_MB "${SBARBASE_UPLOAD_LIMIT_MB-}"
scalar TZ "${TZ-}"
scalar DOCKER_HOST "${DOCKER_HOST-}"
scalar DOCKER_CONTEXT "${DOCKER_CONTEXT-}"
scalar DOCKER_TLS "${DOCKER_TLS-}"
scalar DOCKER_TLS_VERIFY "${DOCKER_TLS_VERIFY-}"
scalar DOCKER_CERT_PATH "${DOCKER_CERT_PATH-}"
scalar DOCKER_API_VERSION "${DOCKER_API_VERSION-}"
scalar COMPOSE_FILE "${COMPOSE_FILE-}"
scalar COMPOSE_PROFILES "${COMPOSE_PROFILES-}"
scalar COMPOSE_ENV_FILES "${COMPOSE_ENV_FILES-}"
scalar COMPOSE_DISABLE_ENV_FILE "${COMPOSE_DISABLE_ENV_FILE-}"
if [ "$command" = upgrade ]; then
    scalar release "$release"
    printf '%s\n' "$release" | LC_ALL=C awk '/^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9]+([.-][a-zA-Z0-9]+)*)?$/ {good=1} END {exit !(good && NR==1)}' || refuse release_tag_invalid 'The release tag is malformed' 'Use a plain vN.N.N release tag with an optional prerelease suffix'
fi
checkout=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P) || refuse checkout_unavailable 'The checkout is unavailable' 'Use a complete checkout'
[ -f "$checkout/compose.yaml" ] || refuse checkout_unavailable 'The pinned Compose file is unavailable' 'Use a complete checkout'
case "$command" in check|up|build|pull|start|restart|bootstrap|smoke|upgrade) "$checkout/deploy/host-preflight.sh" ;; esac
[ "$command" != check ] || exit 0
# Cleanup and observations remain available when a capability is no longer supported.
# They still use the same fixed local endpoint and Compose source.
for value in "${DOCKER_CONTEXT-}" "${DOCKER_TLS-}" "${DOCKER_TLS_VERIFY-}" "${DOCKER_CERT_PATH-}" "${DOCKER_API_VERSION-}" "${COMPOSE_FILE-}" "${COMPOSE_PROFILES-}" "${COMPOSE_ENV_FILES-}" "${COMPOSE_DISABLE_ENV_FILE-}"; do
    [ -z "$value" ] || refuse deployment_override 'An endpoint or Compose override conflicts with the fixed deployment' 'Unset overrides and select the declared local deployment'
done
socket=$(readlink -e -- "${SBARBASE_DOCKER_SOCKET-/var/run/docker.sock}") || refuse docker_socket_unavailable 'The declared socket is unavailable' 'Select the existing local Unix socket'
[ "$(stat -L -c %F -- "$socket" 2>/dev/null)" = socket ] || refuse local_unix_socket_required 'The endpoint is not a Unix socket' 'Use the existing local Unix socket'
if [ -n "${DOCKER_HOST-}" ]; then
    case "$DOCKER_HOST" in unix:///*) requested=$(readlink -e -- "${DOCKER_HOST#unix://}") || refuse host_endpoint_mismatch 'The endpoint cannot be resolved' 'Use the declared local socket';; *) refuse local_unix_socket_required 'The endpoint is remote or malformed' 'Use the declared local Unix socket';; esac
    [ "$requested" = "$socket" ] || refuse host_endpoint_mismatch 'The endpoint selects another socket' 'Use the declared local socket'
fi
project=${SBARBASE_COMPOSE_PROJECT-${COMPOSE_PROJECT_NAME-sbarbase}}
printf '%s\n' "$project" | LC_ALL=C awk '/^[a-z0-9][a-z0-9_-]*$/ {good=1} END {exit !(good && NR==1)}' || refuse compose_project_invalid 'The project name is malformed' 'Use a lowercase Compose project name'
[ -z "${COMPOSE_PROJECT_NAME-}" ] || [ "$COMPOSE_PROJECT_NAME" = "$project" ] || refuse compose_project_conflict 'Project name inputs disagree' 'Use the same project name'
root=$(readlink -e -- "${SBARBASE_ROOT-$checkout}") || refuse checkout_unavailable 'The checkout root is unavailable' 'Use this existing checkout'
[ "$root" = "$checkout" ] || refuse checkout_mismatch 'The checkout differs from the pinned Compose source' 'Use this existing checkout'
data_root=${SBARBASE_DOCKER_DATA_ROOT-/var/lib/docker}
case "$command" in up|build|pull|start|restart|bootstrap|smoke|upgrade) data_root=$(readlink -e -- "$data_root") || refuse docker_data_root_unavailable 'The admitted data root disappeared' 'Retry admission against the existing data root';; esac
case "$command" in
    up) set -- up --detach --build ;;
    build) set -- build ;;
    pull) set -- pull ;;
    start) set -- start ;;
    restart) set -- restart ;;
    ps) set -- ps ;;
    logs) set -- logs --tail 100 ;;
    down) set -- down ;;
    bootstrap) set -- exec sbarbase python3 lab/bootstrap.py ;;
    smoke) set -- exec -T sbarbase python3 lab/install_server.py smoke ;;
    upgrade) set -- exec -T sbarbase python3 lab/upgrade.py start --release "$release" --allow-class rebuild ;;
esac
# Only declared interpolation inputs cross into Compose. No .env is sourced or loaded.
exec env -i PATH="$PATH" LC_ALL=C DOCKER_HOST="unix://$socket" \
    SBARBASE_ROOT="$root" SBARBASE_DOCKER_PROFILE=local-v1 \
    SBARBASE_DOCKER_SOCKET="$socket" SBARBASE_DOCKER_DATA_ROOT="$data_root" \
    COMPOSE_PROJECT_NAME="$project" SBARBASE_COMPOSE_PROJECT="$project" \
    SBARBASE_CONSOLE_PORT="${SBARBASE_CONSOLE_PORT-8790}" \
    SBARBASE_DATABASE_PORT="${SBARBASE_DATABASE_PORT-6543}" SBARBASE_DATABASE_BIND="${SBARBASE_DATABASE_BIND-127.0.0.1}" \
    SBARBASE_PUBLIC_URL="${SBARBASE_PUBLIC_URL-}" SBARBASE_RELEASE_SOURCE="${SBARBASE_RELEASE_SOURCE-}" \
    SBARBASE_BACKUP_HOUR="${SBARBASE_BACKUP_HOUR-3}" SBARBASE_BACKUP_KEEP="${SBARBASE_BACKUP_KEEP-7}" \
    SBARBASE_UPLOAD_LIMIT_MB="${SBARBASE_UPLOAD_LIMIT_MB-50}" TZ="${TZ-}" \
    docker --host "unix://$socket" compose --project-directory "$checkout" \
    --project-name "$project" --env-file /dev/null -f "$checkout/compose.yaml" "$@"
