"""Complete-file HBA replacement. Not a stale-writer or service replay fence."""
import hashlib

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


def replace(docker,container,content):
    if not isinstance(content,str) or not content.endswith('\n') or '\x00' in content:
        raise ValueError('HBA content must be complete text')
    payload=content.encode('utf-8')
    docker('exec','-i',container,'sh','-c',SCRIPT,'sbarbase-hba',str(len(payload)),hashlib.sha256(payload).hexdigest(),data=content)
