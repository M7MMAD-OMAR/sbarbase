"""Conservative read-only inventory for PostgreSQL 17.11 migration planning.

This inventory is not an upgrade authorization. In particular, pgcrypto may have
stored ciphertext in columns whose type or origin cannot be determined here.
"""

import json
import re

CANDIDATE_IMAGE = 'sha256:7374d196da7e301512cc5329e18040e0f7bb856287b323777cdadecccbc6c7e4'
DATABASES = """SELECT json_agg(json_build_object('name',datname,'connect',datallowconn,
 'template',datistemplate) ORDER BY datname)::text FROM pg_database;"""

QUERY = """SELECT json_build_object(
 'ltree_indexes', (SELECT count(*) FROM pg_index i
   JOIN LATERAL unnest(i.indclass::oid[]) WITH ORDINALITY k(opclass,pos) ON true
   JOIN pg_opclass o ON o.oid=k.opclass JOIN pg_type t ON t.oid=o.opcintype
   WHERE k.pos <= i.indnkeyatts AND t.typname IN ('ltree','_ltree')),
 'float_gist_indexes', (SELECT count(*) FROM pg_index i
   JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_am a ON a.oid=c.relam
   JOIN LATERAL unnest(i.indclass::oid[]) WITH ORDINALITY k(opclass,pos) ON true
   JOIN pg_opclass o ON o.oid=k.opclass JOIN pg_type t ON t.oid=o.opcintype
   WHERE a.amname='gist' AND k.pos <= i.indnkeyatts AND t.typname IN ('float4','float8')),
 'custom_estimators', (SELECT count(*) FROM pg_operator o
   JOIN pg_namespace n ON n.oid=o.oprnamespace
   WHERE n.nspname NOT IN ('pg_catalog','information_schema')
   AND ((o.oprrest <> 0 AND o.oprrest::oid >= 10000)
        OR (o.oprjoin <> 0 AND o.oprjoin::oid >= 10000))
   AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid='pg_operator'::regclass
                   AND d.objid=o.oid AND d.deptype='e')),
 'pgcrypto', EXISTS (SELECT 1 FROM pg_extension WHERE extname='pgcrypto'))::text;"""


def refusals(inventory):
    """Refuse possible hazards, including cases needing application knowledge."""
    keys = {'ltree_indexes', 'float_gist_indexes', 'custom_estimators', 'pgcrypto'}
    if not isinstance(inventory, dict) or set(inventory) != keys:
        raise ValueError('PostgreSQL migration inventory is incomplete')
    for key in keys - {'pgcrypto'}:
        if type(inventory[key]) is not int or inventory[key] < 0:
            raise ValueError('PostgreSQL migration inventory is invalid')
    if type(inventory['pgcrypto']) is not bool:
        raise ValueError('PostgreSQL migration inventory is invalid')
    messages = []
    if inventory['ltree_indexes']:
        messages.append('ltree indexes require operator review and remediation')
    if inventory['float_gist_indexes']:
        messages.append('float GiST indexes require operator review and remediation')
    if inventory['custom_estimators']:
        messages.append('custom operator estimators require operator review and remediation')
    if inventory['pgcrypto']:
        messages.append('pgcrypto ciphertext requires operator review for legacy ciphers')
    return messages


def inspect_cluster(docker, container):
    """Inventory every connectable database, refusing incomplete observations."""
    def query(database, sql):
        result = docker('exec', '-i', container, 'psql', '-X', '-qAt', '-v',
                        'ON_ERROR_STOP=1', '-U', 'supabase_admin', '-d', database,
                        data='BEGIN READ ONLY;\n' + sql + '\nCOMMIT;\n', check=False)
        if result.returncode:
            raise RuntimeError('PostgreSQL migration inventory could not be read')
        try:
            return json.loads(result.stdout)
        except (ValueError, TypeError):
            raise RuntimeError('PostgreSQL migration inventory is malformed') from None

    databases = query('postgres', DATABASES)
    if not isinstance(databases, list) or not databases:
        raise RuntimeError('PostgreSQL database inventory is incomplete')
    names = set()
    for entry in databases:
        if (not isinstance(entry, dict) or set(entry) != {'name', 'connect', 'template'}
                or not isinstance(entry['name'], str)
                or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', entry['name'])
                or type(entry['connect']) is not bool or type(entry['template']) is not bool
                or entry['name'] in names):
            raise RuntimeError('PostgreSQL database inventory is malformed')
        names.add(entry['name'])
        if not entry['connect']:
            if entry['name'] == 'template0' and entry['template']:
                continue
            raise RuntimeError('PostgreSQL database cannot be inventoried')
        try:
            issues = refusals(query(entry['name'], QUERY))
        except ValueError:
            raise RuntimeError('PostgreSQL migration inventory is malformed') from None
        if issues:
            raise RuntimeError('PostgreSQL 17.11 migration refused: ' + '; '.join(issues))
    if 'postgres' not in names or 'template0' not in names:
        raise RuntimeError('PostgreSQL database inventory is incomplete')
