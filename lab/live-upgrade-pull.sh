#!/usr/bin/env bash
# Pulls every pinned image by its digest before the install does, for the live upgrade measurement.
#
# public.ecr.aws limits anonymous pulls per address by bytes ("toomanyrequests: Data limit exceeded"), and
# GitHub runners share addresses. The install's own pull gives up after four attempts, and a limited address
# then failed whole runs for over an hour. A digest names content, so the same bytes may come from a mirror:
# when the registry refuses, the image is pulled by the same digest from GitHub's registry or Docker Hub (a
# different digest is refused by the pull itself), and then pulled from public.ecr.aws again, which finds
# every layer present and fetches no layer data, only the manifest, and records the ECR repository digest
# that the installer's identity check (lab/image_identity.py) demands. Nothing is retagged by hand and no
# check is relaxed. Sourced helpers: ci_docker from deploy/ci-docker.sh (BASH_ENV).
#
#   lab/live-upgrade-pull.sh REFS_FILE        one repository@sha256:digest per line
set -u
refs=${1:?a file of image references}
log() { printf '%s\n' "$*"; }
pull() { ci_docker pull -q "$1" >/dev/null 2>"${RUNNER_TEMP:-/tmp}/pull.err"; }
failed=0
while read -r ref; do
  [ -n "$ref" ] || continue
  ok=false
  for attempt in 1 2 3; do
    if pull "$ref"; then ok=true; log "pulled $ref from its own registry"; break; fi
    log "pull of $ref failed (attempt $attempt): $(tail -c 160 "${RUNNER_TEMP:-/tmp}/pull.err")"
    sleep 20
  done
  if [ "$ok" = false ]; then
    repo=${ref%@*}; digest=${ref#*@}; name=${repo##*/}
    for mirror in "ghcr.io/supabase/$name" "docker.io/supabase/$name"; do
      if pull "$mirror@$digest"; then
        log "pulled $name by digest from $mirror"
        for again in 1 2 3 4 5; do
          if pull "$ref"; then ok=true; log "pulled $ref again: layers were present, ECR digest recorded"; break; fi
          log "ECR manifest pull failed (attempt $again): $(tail -c 160 "${RUNNER_TEMP:-/tmp}/pull.err")"
          sleep 30
        done
        [ "$ok" = true ] && break
      else
        log "mirror $mirror has no $digest: $(tail -c 120 "${RUNNER_TEMP:-/tmp}/pull.err")"
      fi
    done
  fi
  [ "$ok" = true ] || { log "could not pull $ref"; failed=1; }
done < "$refs"
exit "$failed"
