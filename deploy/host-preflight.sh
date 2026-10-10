#!/bin/sh
# Read-only local-v1 capability admission. Never create missing host paths.
set -eu
set -f
LC_ALL=C
export LC_ALL
refuse() { printf '%s\n' "Docker profile refused [$1]: $2. Action: $3." >&2; exit 1; }
mode=host
case "$#:${1-}" in 0:) ;; 1:--runtime) mode=runtime ;; *) refuse invalid_arguments 'Unknown preflight arguments' 'Use host-preflight.sh or host-preflight.sh --runtime' ;; esac
for tool in docker timeout uname readlink stat findmnt sed awk cat dirname env tr; do
    command -v "$tool" >/dev/null 2>&1 || refuse prerequisite_missing "Required utility $tool is unavailable" 'Install the documented Docker and Linux utility prerequisites before retrying'
done
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
canonical() {
    case "$1" in /*) ;; *) refuse invalid_absolute_path 'A configured path is not absolute' 'Declare an existing absolute path' ;; esac
    case "$1" in /|//*|*:*|*\\*) refuse invalid_absolute_path 'A configured path cannot be used safely as a bind source' 'Use an existing absolute path without colon or backslash' ;; esac
    printf '%s' "$1" | LC_ALL=C awk '/[[:cntrl:]]/ {bad=1} END {exit bad}' || refuse invalid_absolute_path 'A configured path contains control characters' 'Use a plain absolute path'
    resolved=$(readlink -e -- "$1") || refuse configured_path_unavailable 'A configured path does not exist or cannot be resolved' 'Provision the path independently and retry; preflight never creates it'
    [ "$resolved" != / ] || refuse invalid_absolute_path 'A configured path resolves to the filesystem root' 'Declare a dedicated existing directory or socket'
    printf '%s\n' "$resolved"
}
case "${SBARBASE_DOCKER_PROFILE-local-v1}" in ''|local-v1) ;; *) refuse unknown_docker_profile 'Only local-v1 has a candidate contract' 'Select local-v1 or obtain evidence for another profile' ;; esac
case "${SBARBASE_CONTAINER-}" in ''|1) ;; *) refuse invalid_container_marker 'The controller marker is invalid' 'Use the declared host or controller environment' ;; esac
for value in "${DOCKER_CONTEXT-}" "${DOCKER_TLS-}" "${DOCKER_TLS_VERIFY-}" "${DOCKER_CERT_PATH-}" "${DOCKER_API_VERSION-}"; do
    [ -z "$value" ] || refuse docker_endpoint_override 'Docker context, TLS or API overrides conflict with local admission' 'Unset overrides and explicitly select the declared local socket'
done
root=$(canonical "${SBARBASE_DOCKER_DATA_ROOT-/var/lib/docker}")
[ -d "$root" ] || refuse docker_data_root_unavailable 'Docker data root is not an existing directory' 'Declare the existing daemon data root'
if [ "$mode" = runtime ] && [ "${SBARBASE_CONTAINER-}" = 1 ]; then
    socket=/var/run/docker.sock
    endpoint=unix:///var/run/docker.sock
else
    socket=$(canonical "${SBARBASE_DOCKER_SOCKET-/var/run/docker.sock}")
    endpoint=unix://$socket
fi
socket_type=$(stat -L -c %F -- "$socket" 2>/dev/null) || refuse docker_socket_unavailable 'The Docker socket is unavailable' 'Select an existing local Docker Unix socket'
[ "$socket_type" = socket ] || refuse local_unix_socket_required 'The endpoint source is not a Unix socket' 'Select the rootful local Docker Unix socket'
if [ -n "${DOCKER_HOST-}" ]; then
    case "$DOCKER_HOST" in unix:///*) requested=$(canonical "${DOCKER_HOST#unix://}");; *) refuse local_unix_socket_required 'The Docker endpoint is remote or malformed' 'Use the declared local Unix socket' ;; esac
    selected=$(canonical "$socket")
    [ "$requested" = "$selected" ] || refuse host_endpoint_mismatch 'DOCKER_HOST selects a different socket' 'Unset DOCKER_HOST or match the declared socket'
fi
[ "$(uname -s)" = Linux ] || refuse linux_host_required 'This host is not Linux' 'Use a Linux host within the candidate scope'
[ "$(uname -m)" = x86_64 ] || refuse architecture_unvalidated 'This architecture has no candidate acceptance evidence' 'Use Linux x86_64 or verify a separate experimental profile'
if [ "$mode" = host ]; then
    for value in "${COMPOSE_FILE-}" "${COMPOSE_PROFILES-}" "${COMPOSE_ENV_FILES-}" "${COMPOSE_DISABLE_ENV_FILE-}"; do
        [ -z "$value" ] || refuse compose_override 'Compose input overrides conflict with the pinned deployment' 'Unset Compose file, profile and env file overrides'
    done
    checkout=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P) || refuse checkout_unavailable 'The checkout cannot be resolved' 'Run the launcher from a complete checkout'
    declared_checkout=$(canonical "${SBARBASE_ROOT-$checkout}")
    [ "$declared_checkout" = "$checkout" ] && [ -f "$checkout/compose.yaml" ] || refuse checkout_mismatch 'The checkout root differs from the pinned Compose source' 'Set SBARBASE_ROOT to this existing checkout'
    project=${SBARBASE_COMPOSE_PROJECT-${COMPOSE_PROJECT_NAME-sbarbase}}
    printf '%s\n' "$project" | awk '/^[a-z0-9][a-z0-9_-]*$/ {good=1} END {exit !(good && NR==1)}' || refuse compose_project_invalid 'The Compose project name is malformed' 'Declare a lowercase project name using letters, digits, underscore or hyphen'
    [ -z "${COMPOSE_PROJECT_NAME-}" ] || [ "$COMPOSE_PROJECT_NAME" = "$project" ] || refuse compose_project_conflict 'Two Compose project inputs disagree' 'Use the same project name for both inputs'
    console=${SBARBASE_CONSOLE_PORT-8790}
    database=${SBARBASE_DATABASE_PORT-6543}
    for port in "$console" "$database"; do
        printf '%s\n' "$port" | awk '/^[0-9]+$/ {if (length($0)<=5 && $0+0>=1 && $0+0<=65535) good=1} END {exit !(good && NR==1)}' || refuse port_invalid 'A declared port is outside 1 to 65535' 'Use a valid console or database port'
    done
    [ "$(printf '%s\n' "$console" | awk '{print $0+0}')" != "$(printf '%s\n' "$database" | awk '{print $0+0}')" ] || refuse port_conflict 'Console and database ports conflict' 'Choose distinct ports'
    bind=${SBARBASE_DATABASE_BIND-127.0.0.1}
    printf '%s\n' "$bind" | awk -F. 'NF==4 {good=1; for (i=1;i<=4;i++) if ($i !~ /^[0-9]+$/ || length($i)>3 || $i+0>255) good=0} END {exit !(good && NR==1)}' || refuse database_bind_invalid 'The database bind address is not a plain IPv4 address' 'Declare an explicit IPv4 bind address'
    for url in "${SBARBASE_PUBLIC_URL-}" "${SBARBASE_RELEASE_SOURCE-}"; do
        [ -z "$url" ] || printf '%s\n' "$url" | awk '
        /^https?:\/\/[^[:space:]@?#]+$/ {
            authority=$0; sub(/^https?:\/\//,"",authority); sub(/\/.*/,"",authority)
            parts=split(authority,a,":"); host=a[1]
            if (parts>2 || host !~ /^[a-zA-Z0-9][a-zA-Z0-9.-]*$/ || length(host)>253) next
            if (parts==2 && (a[2] !~ /^[0-9]+$/ || length(a[2])>5 || a[2]+0<1 || a[2]+0>65535)) next
            labels=split(host,b,"."); valid=1
            for (j=1;j<=labels;j++) if (b[j] !~ /^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?$/ || length(b[j])>63) valid=0
            if (host ~ /^[0-9.]+$/) {if (labels!=4) valid=0; for (j=1;j<=labels;j++) if (b[j]+0>255) valid=0}
            if (valid) good=1
        } END {exit !(good && NR==1)}' || refuse public_url_invalid 'A declared public or release URL is malformed' 'Use an explicit http or https URL without credentials, query or fragment'
    done
    for spec in "${SBARBASE_BACKUP_HOUR-3}:0:23" "${SBARBASE_BACKUP_KEEP-7}:1:3650" "${SBARBASE_UPLOAD_LIMIT_MB-50}:1:5120"; do
        printf '%s\n' "$spec" | awk -F: 'NF==3 && $1 ~ /^[0-9]+$/ && length($1)<=4 && $1+0>=$2 && $1+0<=$3 {good=1} END {exit !(good && NR==1)}' || refuse deployment_value_invalid 'A backup or upload setting is outside its declared range' 'Use backup hour 0 to 23, retention 1 to 3650 and upload limit 1 to 5120 MiB'
    done
fi
probe() { env -i PATH="$PATH" LC_ALL=C timeout 15 docker --host "$endpoint" "$@"; }
# Fixed projection avoids a host JSON-parser dependency. A sentinel detects truncation.
format='{{.OSType}}{{println}}{{.Architecture}}{{println}}{{.OperatingSystem}}{{println}}{{.Name}}{{println}}{{.ID}}{{println}}{{.DockerRootDir}}{{println}}{{.CgroupVersion}}{{println}}{{.CgroupDriver}}{{println}}{{.Driver}}{{println}}{{range .SecurityOptions}}{{printf "%s|" .}}{{end}}{{println}}{{.MemoryLimit}}{{println}}{{.SwapLimit}}{{println}}{{.CPUCfsPeriod}}{{println}}{{.CPUCfsQuota}}{{println}}{{.CPUShares}}{{println}}{{.PidsLimit}}{{println}}{{range .DriverStatus}}{{index . 0}}={{index . 1}}{{printf "|"}}{{end}}{{println}}{{.ServerVersion}}{{println}}{{.DefaultRuntime}}{{println}}SBARBASE-PREFLIGHT-V1'
info=$(probe info --format "$format" 2>/dev/null) || refuse docker_probe_unavailable 'The daemon capability query failed or timed out' 'Check access to the declared running daemon and retry'
field() { printf '%s\n' "$info" | sed -n "${1}p"; }
[ "$(printf '%s\n' "$info" | awk 'END {print NR}')" = 20 ] && [ "$(field 20)" = SBARBASE-PREFLIGHT-V1 ] || refuse daemon_info_invalid 'The daemon returned a malformed or incomplete capability record' 'Use a Docker Engine exposing the documented info fields'
# Text projection fields must not carry hidden controls. Newlines are rejected by
# the fixed record count above; this check also catches tabs, CR and DEL.
for text_index in 1 2 3 4 6 7 8 9 10 17 18 19; do
    text_value=$(field "$text_index")
    plain_text=$(printf '%s' "$text_value" | tr -d '[:cntrl:]')
    [ "$plain_text" = "$text_value" ] || refuse daemon_info_invalid 'A daemon capability text field contains control characters' 'Repair daemon capability diagnostics before admission'
done
daemon_id=$(field 5)
[ -n "$daemon_id" ] || refuse daemon_identity_missing 'The daemon identity is unavailable' 'Repair daemon diagnostics before admission'
printf '%s\n' "$daemon_id" | awk '/^[a-zA-Z0-9][a-zA-Z0-9:_-]*$/ {good=1} END {exit !(good && NR==1)}' || refuse daemon_identity_invalid 'The daemon identity is not a meaningful safe token' 'Repair daemon identity diagnostics before admission'
[ "$(field 1)" = linux ] || refuse linux_daemon_required 'The selected daemon does not run Linux' 'Use the local Linux Docker Engine'
[ "$(field 2)" = x86_64 ] || refuse daemon_architecture_unvalidated 'The daemon architecture is outside the candidate scope' 'Use a Linux x86_64 Docker Engine'
operating_system=$(field 3)
printf '%s\n' "$operating_system" | awk 'NF>0 && $0 !~ /<no value>|<nil>|^null$/ {good=1} END {exit !(good && NR==1)}' || refuse daemon_info_invalid 'The daemon OS observation is missing or a placeholder' 'Repair daemon OS diagnostics before admission'
case "$operating_system" in *Desktop*|*desktop*) refuse docker_desktop_unvalidated 'Desktop is outside the candidate scope' 'Use a rootful Linux Docker Engine' ;; esac
if [ "$mode" = host ]; then
    [ "$(field 4)" = "$(uname -n)" ] || refuse daemon_host_mismatch 'The daemon node name does not match this host' 'Select the daemon on this host; remote endpoints are refused'
fi
daemon_root=$(canonical "$(field 6)")
[ "$daemon_root" = "$root" ] || refuse docker_data_root_mismatch 'The declared data root differs from the daemon data root' 'Set SBARBASE_DOCKER_DATA_ROOT to the existing daemon root'
[ "$(field 7)" = 2 ] || refuse cgroup_v2_required 'The daemon does not expose cgroup v2' 'Use a host with cgroup v2 and the required controllers'
case "$(field 8)" in systemd|cgroupfs) ;; *) refuse cgroup_driver_unvalidated 'The daemon cgroup driver is unknown' 'Use a documented cgroup v2 driver' ;; esac
driver=$(field 9)
case "$driver" in overlay2|overlayfs|btrfs|zfs|vfs) ;; ''|*' '*|*'<'*|*'>'*|*[!a-zA-Z0-9_-]*) refuse daemon_info_invalid 'The daemon storage driver field is malformed' 'Repair Docker capability diagnostics';; *) refuse admission_unproven 'This storage driver has no capability evidence for admission' 'Provide the driver capability evidence and an owned acceptance fixture';; esac
security=$(field 10)
case "$security" in *'|') ;; *) refuse daemon_security_options_invalid 'The security capability record is malformed' 'Repair Docker capability diagnostics' ;; esac
seen_app=0; seen_sec=0; seen_cg=0
oldifs=$IFS; IFS='|'; set -- $security; IFS=$oldifs
for option do
    case "$option" in name=apparmor|name=apparmor,profile=default) [ "$seen_app" = 0 ] || refuse daemon_security_options_invalid 'Duplicate AppArmor security option' 'Repair daemon diagnostics'; seen_app=1;;
        name=seccomp,profile=builtin) [ "$seen_sec" = 0 ] || refuse daemon_security_options_invalid 'Duplicate seccomp security option' 'Repair daemon diagnostics'; seen_sec=1;;
        name=cgroupns) [ "$seen_cg" = 0 ] || refuse daemon_security_options_invalid 'Duplicate cgroup namespace security option' 'Repair daemon diagnostics'; seen_cg=1;;
        *) refuse security_profile_unvalidated 'A daemon security option differs from the candidate contract' 'Use rootful Docker with builtin seccomp and cgroup namespaces; other profiles need separate evidence';;
    esac
done
[ "$seen_sec:$seen_cg" = 1:1 ] || refuse security_profile_unvalidated 'Required builtin seccomp or cgroup namespace support is absent' 'Enable the declared Docker security profile before retrying'
index=11
for feature in memory_limit swap_limit cpu_cfs_period cpu_cfs_quota cpu_shares pids_limit; do
    case "$(field "$index")" in true) ;; false) refuse resource_feature_unavailable "Docker resource feature $feature is disabled" 'Enable CPU, memory, swap and pids limits' ;; *) refuse daemon_info_invalid 'A daemon resource feature is not a boolean' 'Repair Docker capability diagnostics' ;; esac
    index=$((index + 1))
done
controllers=$(cat /sys/fs/cgroup/cgroup.controllers 2>/dev/null) || refuse cgroup_controllers_unavailable 'The cgroup controller list is unreadable' 'Expose the host cgroup v2 hierarchy'
for controller in cpu memory pids io; do
    case " $controllers " in *" $controller "*) ;; *) refuse cgroup_controller_missing "The $controller controller is absent" 'Enable the required cgroup v2 controllers before retrying' ;; esac
done
filesystem=$(findmnt -n -T "$root" -o FSTYPE 2>/dev/null) || refuse filesystem_probe_unavailable 'The data root filesystem query failed' 'Make the existing local mount visible'
options=$(findmnt -n -T "$root" -o OPTIONS 2>/dev/null) || refuse filesystem_probe_unavailable 'The data root mount options query failed' 'Make the existing local mount visible'
plain_options=$(printf '%s' "$options" | tr -d '[:cntrl:]')
[ "$plain_options" = "$options" ] || refuse filesystem_probe_invalid 'The mount options record contains control characters' 'Repair mount diagnostics'
access=unknown
case ",$options," in *,ro,*) access=ro;; esac
case ",$options," in *,rw,*) [ "$access" = unknown ] || refuse filesystem_probe_invalid 'The data root mount has conflicting access modes' 'Repair mount diagnostics'; access=rw;; esac
[ "$access" != unknown ] || refuse filesystem_probe_invalid 'The data root mount has no explicit access mode' 'Repair mount diagnostics before admission'
if [ "$mode" = runtime ] && [ "${SBARBASE_CONTAINER-}" = 1 ]; then
    [ "$access" = ro ] || refuse controller_data_root_access_mismatch 'The controller data root mount is not read only' 'Use the declared read only controller data root bind mount'
else
    [ "$access" = rw ] || refuse filesystem_readonly 'The daemon data root is mounted read only' 'Use a writable existing daemon data root'
fi
case "$filesystem" in
    ext4|btrfs) ;;
    xfs) if [ "$driver" = overlay2 ]; then
        case "$(field 17)" in *'Supports d_type=false|'*) refuse xfs_dtype_unavailable 'XFS directory entry type support is disabled' 'Use XFS ftype=1 for overlay2';; *'Supports d_type=true|'*) ;; *) refuse xfs_dtype_unproven 'XFS directory entry type support is not proven' 'Supply Docker evidence for XFS ftype=1 before using overlay2';; esac
    fi;;
    tmpfs|ramfs|overlay|overlayfs|nfs|nfs4|cifs|smb3|9p|virtiofs|fuse.sshfs|ceph|glusterfs) refuse filesystem_capability_unavailable 'The data root is transient, layered, remote or shared rather than a local durable mount' 'Use an existing local durable block backed filesystem';;
    '') refuse filesystem_probe_invalid 'The data root filesystem record is empty' 'Repair mount diagnostics';;
    *) refuse admission_unproven 'This filesystem has no required capability evidence for admission' 'Provide filesystem persistence and metadata capability evidence with an owned acceptance fixture';;
esac
# Match resource_policy: prefer the mount SOURCE, including Btrfs subvolume syntax,
# then resolve the mount device numbers when no source block device is visible.
source=$(findmnt -no SOURCE --target "$root" 2>/dev/null) || refuse io_device_unavailable 'The data root source query failed' 'Expose a local block backed data root'
plain_source=$(printf '%s' "$source" | tr -d '[:cntrl:]')
[ "$plain_source" = "$source" ] || refuse filesystem_probe_invalid 'The source device record contains control characters' 'Repair mount diagnostics'
source=$(printf '%s\n' "$source" | sed 's/\[.*//;s/^[[:space:]]*//;s/[[:space:]]*$//')
device=
case "$source" in /dev/*)
    node=$(readlink -m -- "$source") || refuse io_device_unavailable 'The source device path is not resolvable' 'Expose a local block device or its sysfs identity'
    block_name=${node##*/}
    device=$(readlink -e -- "/sys/class/block/$block_name" 2>/dev/null) || device=
    ;;
esac
if [ -z "$device" ]; then
    numbers=$(findmnt -rno MAJ:MIN --target "$root" 2>/dev/null) || refuse io_device_unavailable 'The data root block device query failed' 'Use a local block backed data root'
    if ! printf '%s\n' "$numbers" | awk '/^[1-9][0-9]*:[0-9]+$/ {good=1} END {exit !(good && NR==1)}'; then
        if printf '%s\n' "$numbers" | awk '/^[0-9]+:[0-9]+$/ && length($0)<=21 {good=1} END {exit !(good && NR==1)}'; then
            printf '%s\n' "local-v1 mount block-device numbers: $numbers" >&2
        fi
        refuse io_device_unavailable 'The data root has no unambiguous block device' 'Expose the source block device or valid mount device numbers'
    fi
    device=$(readlink -e -- "/sys/dev/block/$numbers") || refuse io_device_unavailable 'The block device is missing from sysfs' 'Expose the host block device sysfs entries'
fi
[ "$(stat -L -c %F -- "$device" 2>/dev/null)" = directory ] || refuse io_device_unavailable 'The sysfs block device is not resolvable' 'Expose the host block device sysfs entries'
# Runtime IO policy limits the whole disk when the mount is on a partition.
partition_type=$(stat -L -c %F -- "$device/partition" 2>/dev/null) || partition_type=absent
case "$partition_type" in 'regular file') device=$(dirname -- "$device");; absent) ;; *) refuse io_device_unavailable 'The sysfs partition marker is malformed' 'Expose the host block device sysfs entries';; esac
block_name=${device##*/}
whole=$(readlink -e -- "/sys/class/block/$block_name") || refuse io_device_unavailable 'The whole block device cannot be resolved by the runtime' 'Expose the matching host whole disk sysfs entry'
[ "$whole" = "$device" ] && [ "$(stat -L -c %F -- "$whole" 2>/dev/null)" = directory ] || refuse io_device_unavailable 'The whole block device sysfs identity is ambiguous' 'Use a data root with a resolvable whole block device'
runtime=$(field 19)
case "$runtime" in runc) ;; ''|*[!a-zA-Z0-9_.-]*) refuse daemon_info_invalid 'The default OCI runtime field is missing or malformed' 'Repair Docker capability diagnostics';; *) refuse admission_unproven 'The default OCI runtime differs from runc' 'Verify the alternative runtime profile independently before admission';; esac
version=$(field 18)
printf '%s\n' "$version" | awk -F. '/^[0-9]+\.[0-9]+\.[0-9]+([+-][a-zA-Z0-9._-]+)?$/ {if ($1>=25) good=1} END {exit !(good && NR==1)}' || refuse daemon_version_unvalidated 'Docker Engine version is missing, malformed or older than 25' 'Use the documented modern Engine prerequisite; newer versions still require acceptance evidence'
if [ "$mode" = host ]; then
    compose=$(probe compose version --short 2>/dev/null) || refuse compose_probe_unavailable 'Docker Compose is unavailable or its version query failed' 'Install the Docker Compose plugin'
    printf '%s\n' "$compose" | awk -F. '/^v?[0-9]+\.[0-9]+\.[0-9]+([+-][a-zA-Z0-9._-]+)?$/ {sub(/^v/,"",$1); if ($1>2 || ($1==2 && $2>=20)) good=1} END {exit !(good && NR==1)}' || refuse compose_version_unvalidated 'Compose version is malformed or older than the declared 2.20 prerequisite' 'Use the documented Compose v2 plugin'
fi
printf '%s\n' 'local-v1 preflight passed; supported-profile acceptance unproven; production unproven'
