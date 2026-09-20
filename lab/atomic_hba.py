"""Complete-file HBA replacement with prepared-request revision checks."""
import hashlib
import re
import uuid
from dataclasses import dataclass

SCRIPT = r'''set -eu
target=/etc/postgresql/pg_hba.conf
directory=/etc/postgresql
[ -f "$target" ] && [ ! -L "$target" ]
temporary=$(mktemp "$directory/.sbarbase-hba.XXXXXXXX")
trap 'rm -f -- "$temporary"' EXIT HUP INT TERM
cat > "$temporary"
[ "$(wc -c < "$temporary")" -eq "$1" ]
computed=$(sha256sum "$temporary")
[ "${computed%% *}" = "$2" ]
chmod "$(stat -c %a "$target")" "$temporary"
chown "$(stat -c %u:%g "$target")" "$temporary"
sync "$temporary"
mv -f -- "$temporary" "$target"
sync "$directory"
'''


# Keep the lock inode stable across every managed writer and interruption.
CAS_SCRIPT=SCRIPT.replace('temporary=$(mktemp',"""umask 077
[ ! -L "$directory/.sbarbase-hba.lock" ]
exec 9>> "$directory/.sbarbase-hba.lock"
flock -n 9 || { printf 'HBA writer busy\n' >&2; exit 73; }
current=$(sha256sum "$target")
[ "${current%% *}" = "$3" ] || { printf 'HBA revision changed\n' >&2; exit 74; }
temporary=$(mktemp""",1)


@dataclass(frozen=True)
class Prepared:
    container_id: str
    expected_digest: str
    content: str


def prepare(docker,container,content):
    if not isinstance(content,str) or not content.endswith('\n') or '\x00' in content:
        raise ValueError('HBA content must be complete text')
    identifier=docker('inspect','--format','{{.Id}}',container).stdout.strip()
    if not re.fullmatch(r'[a-f0-9]{64}',identifier):raise RuntimeError('HBA container identity unavailable')
    observed=docker('exec',identifier,'sha256sum','/etc/postgresql/pg_hba.conf').stdout.split()
    if len(observed)!=2 or not re.fullmatch(r'[a-f0-9]{64}',observed[0]):raise RuntimeError('HBA revision unavailable')
    return Prepared(identifier,observed[0],'# sbarbase-hba-revision: '+str(uuid.uuid4())+'\n'+content)


def apply(docker,prepared):
    if (not isinstance(prepared,Prepared) or not re.fullmatch(r'[a-f0-9]{64}',prepared.container_id)
            or not re.fullmatch(r'[a-f0-9]{64}',prepared.expected_digest)):
        raise ValueError('Invalid prepared HBA identity')
    payload=prepared.content.encode('utf-8')
    # Never recapture expected state or retry here, including lost acknowledgment.
    docker('exec','-i',prepared.container_id,'sh','-c',CAS_SCRIPT,'sbarbase-hba',str(len(payload)),
           hashlib.sha256(payload).hexdigest(),prepared.expected_digest,data=prepared.content)


def replace(docker,container,content):
    apply(docker,prepare(docker,container,content))
