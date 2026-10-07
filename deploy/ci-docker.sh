#!/usr/bin/env bash
# CI callers use the same admitted endpoint, checkout and project as operators.
# This file defines helpers only; Bash sources it through the job's BASH_ENV.
ci_require() {
    [[ ${SBARBASE_ROOT:-} == "$GITHUB_WORKSPACE" && ${SBARBASE_DOCKER_PROFILE:-} == local-v1 ]] &&
    [[ ${SBARBASE_COMPOSE_PROJECT:-} =~ ^sbarbase-ci-[0-9]+-[0-9]+$ ]] &&
    [[ ${SBARBASE_DOCKER_SOCKET:-} == /run/docker.sock ]] &&
    [[ ${SBARBASE_DOCKER_DATA_ROOT:-} == /* && -n ${SBARBASE_CI_RUN:-} ]]
}
ci_env() {
    ci_require || return 1
    sudo env -i PATH="$PATH" LC_ALL=C \
        SBARBASE_ROOT="$SBARBASE_ROOT" SBARBASE_DOCKER_PROFILE=local-v1 \
        SBARBASE_DOCKER_SOCKET="$SBARBASE_DOCKER_SOCKET" SBARBASE_DOCKER_DATA_ROOT="$SBARBASE_DOCKER_DATA_ROOT" \
        SBARBASE_COMPOSE_PROJECT="$SBARBASE_COMPOSE_PROJECT" COMPOSE_PROJECT_NAME="$SBARBASE_COMPOSE_PROJECT" \
        SBARBASE_CONSOLE_PORT=8790 SBARBASE_UPLOAD_LIMIT_MB="${SBARBASE_UPLOAD_LIMIT_MB:-50}" "$@"
}
ci_admit() { ci_env "$SBARBASE_ROOT/deploy/host-preflight.sh"; }
ci_route() { ci_env "$SBARBASE_ROOT/deploy/compose.sh" "$@"; }
ci_compose() {
    ci_require || return 1
    case "${1:-}" in ps|logs|exec) ;; *) return 2 ;; esac
    ci_env docker --host unix:///run/docker.sock compose \
        --project-directory "$SBARBASE_ROOT" --project-name "$SBARBASE_COMPOSE_PROJECT" \
        --env-file /dev/null -f "$SBARBASE_ROOT/compose.yaml" "$@"
}
ci_exec() {
    ci_admit || return 1
    # Explicitly pass the genuine private MFA descriptor across Compose exec.
    ci_compose exec -T -e SBARBASE_LIVE_AUTH_FILE="$SBARBASE_LIVE_AUTH_FILE" "$@"
}
ci_docker() { ci_require && sudo docker --host unix:///run/docker.sock "$@"; }
ci_cleanup_call() {
    local useful
    # Reserve two seconds for clock exit and one for the CLI process-group reap.
    useful=$(timeout --signal TERM --kill-after 1s 1s python3 -c 'import sys,time; left=int(sys.argv[1])-int(time.monotonic()*1000)-3000; cap=int(sys.argv[2])*1000; left=min(left,cap); assert left>0; print(f"{left/1000:.3f}s")' "$SBARBASE_CI_CLEANUP_DEADLINE" "$1") || return 1
    shift
    timeout --signal TERM --kill-after 1s "$useful" "$@"
}
ci_cleanup_docker() {
    local cap=$1
    shift
    ci_cleanup_call "$cap" sudo docker --host unix:///run/docker.sock "$@"
}
ci_offsite_cleanup() {
    ci_require || return 1
    local name cid mark running remaining image record
    local SBARBASE_CI_CLEANUP_DEADLINE
    SBARBASE_CI_CLEANUP_DEADLINE=$(timeout --signal TERM --kill-after 1s 1s python3 -c 'import time; print(int(time.monotonic()*1000)+120000)') || return 1
    for name in "$SBARBASE_COMPOSE_PROJECT-minio" "$SBARBASE_COMPOSE_PROJECT-s3mock"; do
        remaining=$(ci_cleanup_docker 15 container ls -aq --no-trunc --filter "name=^/$name$") || return 1
        [[ -z $remaining ]] && continue
        [[ $remaining =~ ^[a-f0-9]{64}$ ]] || return 1
        cid=$remaining
        record="$RUNNER_TEMP/$name.image"
        [[ -f $record && ! -L $record ]] || return 1
        image=$(ci_cleanup_call 2 head -c 80 "$record") || return 1
        [[ $image =~ ^sha256:[a-f0-9]{64}$ ]] || return 1
        mark=$(ci_cleanup_docker 15 inspect --format '{{.Id}}|{{.Name}}|{{index .Config.Labels "io.sbarbase.ci.owner"}}|{{.Image}}' "$cid") || return 1
        [[ $mark == "$cid|/$name|$SBARBASE_COMPOSE_PROJECT|$image" ]] || return 1
        # Docker must never escalate to SIGKILL. A stalled stop remains negative.
        ci_cleanup_docker 35 stop --time -1 "$cid" >/dev/null || return 1
        running=$(ci_cleanup_docker 15 inspect --format '{{.State.Running}}' "$cid") || return 1
        [[ $running == false ]] || return 1
        mark=$(ci_cleanup_docker 15 inspect --format '{{.Id}}|{{.Name}}|{{index .Config.Labels "io.sbarbase.ci.owner"}}|{{.Image}}' "$cid") || return 1
        [[ $mark == "$cid|/$name|$SBARBASE_COMPOSE_PROJECT|$image" ]] || return 1
        ci_cleanup_docker 15 rm "$cid" >/dev/null || return 1
        remaining=$(ci_cleanup_docker 15 container ls -aq --no-trunc --filter "id=$cid") || return 1
        [[ -z $remaining ]] || return 1
    done
}
ci_offsite_start() {
    ci_admit || return 1
    local name image cid mark
    ci_offsite_cleanup || return 1
    if ci_docker pull quay.io/minio/minio:latest >/dev/null; then
        name="$SBARBASE_COMPOSE_PROJECT-minio"
        image=$(ci_docker image inspect --format '{{.Id}}' quay.io/minio/minio:latest) || return 1
        [[ $image =~ ^sha256:[a-f0-9]{64}$ ]] || return 1
        (umask 077; set -o noclobber; printf '%s\n' "$image" > "$RUNNER_TEMP/$name.image") || return 1
        ci_admit || return 1
        cid=$(ci_docker create --name "$name" --label "io.sbarbase.ci.owner=$SBARBASE_COMPOSE_PROJECT" \
            --memory 512m --memory-swap 512m --cpus .5 --pids-limit 128 \
            -p 127.0.0.1:9100:9000 -e MINIO_ROOT_USER="$OFFSITE_KEY_ID" -e MINIO_ROOT_PASSWORD="$OFFSITE_SECRET" \
            "$image" server /data) || return 1
        SBARBASE_OFFSITE_PEER=minio
    else
        name="$SBARBASE_COMPOSE_PROJECT-s3mock"
        ci_docker pull adobe/s3mock:latest >/dev/null || return 1
        image=$(ci_docker image inspect --format '{{.Id}}' adobe/s3mock:latest) || return 1
        [[ $image =~ ^sha256:[a-f0-9]{64}$ ]] || return 1
        (umask 077; set -o noclobber; printf '%s\n' "$image" > "$RUNNER_TEMP/$name.image") || return 1
        ci_admit || return 1
        cid=$(ci_docker create --name "$name" --label "io.sbarbase.ci.owner=$SBARBASE_COMPOSE_PROJECT" \
            --memory 512m --memory-swap 512m --cpus .5 --pids-limit 128 \
            -p 127.0.0.1:9100:9090 "$image") || return 1
        SBARBASE_OFFSITE_PEER=s3mock
    fi
    [[ $cid =~ ^[a-f0-9]{64}$ ]] || return 1
    mark=$(ci_docker inspect --format '{{.Id}}|{{.Name}}|{{index .Config.Labels "io.sbarbase.ci.owner"}}|{{.Image}}' "$cid") || return 1
    [[ $mark == "$cid|/$name|$SBARBASE_COMPOSE_PROJECT|$image" ]] || return 1
    ci_admit || return 1
    ci_docker start "$cid" >/dev/null
}
