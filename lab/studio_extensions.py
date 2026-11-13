"""Database-local PGMQ prerequisites and a verified root-owned upstream hook."""
import hashlib
import json
import re
from pathlib import Path

PGMQ_SCRIPT = '/etc/postgresql-custom/extension-custom-scripts/pgmq/after-create.sql'
SOURCE = Path(__file__).resolve().parent / 'postgres/pgmq-after-create.sql'


def prepare_sql(environment):
    """Prepare the fixed schema and reconcile only the former hosted-role ownership.

    Runs under the existing bounded, pg_catalog-pinned platform provisioner.
    Existing queues are retained. No extension is activated automatically.
    """
    if not re.fullmatch(r'e_[a-f0-9]{24}', environment):
        raise ValueError('Invalid environment')
    developer = environment + '_developer'
    repair = SOURCE.read_text().replace("'", "''")
    return f"""DO $studio_extensions$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{developer}')
     AND EXISTS (SELECT 1 FROM pg_catalog.pg_available_extensions WHERE name = 'pgmq') THEN
    CREATE SCHEMA IF NOT EXISTS pgmq AUTHORIZATION {developer};
    GRANT USAGE, CREATE ON SCHEMA pgmq TO {developer};
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_extension e
               JOIN pg_catalog.pg_roles r ON r.oid = e.extowner
               WHERE e.extname = 'pgmq' AND r.rolname = 'postgres') THEN
      IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_extension e
                     JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace
                     WHERE e.extname = 'pgmq' AND e.extversion = '1.5.1' AND n.nspname = 'pgmq') THEN
        RAISE EXCEPTION 'Existing pgmq version or schema requires operator migration';
      END IF;
      EXECUTE '{repair}';
    END IF;
  END IF;
END $studio_extensions$;
"""


def install(docker, container, image, owner, source=SOURCE):
    """Atomically reconcile the hook without replacing the HBA-bound container.

    The daemon writes a root-owned 0444 file, then the exact bytes, permissions
    and unchanged container identity are read back. Tenant SQL cannot write it.
    """
    if not re.fullmatch(r'[a-f0-9]{64}', container):
        raise RuntimeError('Invalid database container identity')
    content = Path(source).read_bytes().decode('utf-8')
    expected = hashlib.sha256(content.encode()).hexdigest()

    def identity():
        rows = json.loads(docker('inspect', container).stdout)
        if len(rows) != 1:
            raise RuntimeError('Database container identity is unavailable')
        value = rows[0]
        if value.get('Id') != container or value.get('Image') != image or value.get('Config', {}).get('Labels', {}).get('io.sbarbase.owner') != owner:
            raise RuntimeError('Database container identity differs during hook installation')

    identity()
    write = '''set -eu
umask 077
scratch=$(mktemp "${1}.sbarbase.XXXXXX")
trap 'rm -f "$scratch"' EXIT
cat > "$scratch"
chown 0:0 "$scratch"
chmod 0444 "$scratch"
mv -f -- "$scratch" "$1"
'''
    docker('exec', '-i', '--user', '0', container, 'sh', '-c', write, 'sbarbase-pgmq', PGMQ_SCRIPT, data=content)
    observed = docker('exec', '--user', '0', container, 'sh', '-c',
                      'stat -c "%u:%g:%a" "$1"; sha256sum "$1"', 'sbarbase-pgmq', PGMQ_SCRIPT).stdout.splitlines()
    if len(observed) != 2 or observed[0] != '0:0:444' or observed[1].split() != [expected, PGMQ_SCRIPT]:
        raise RuntimeError('Installed PGMQ hook bytes or permissions differ')
    identity()
    return expected
