"""The cluster level of a backup run: the database image and the roles the runtime manages.

A backup run (lab/backup.py create all) dumps each database; roles live in the cluster, outside
every dump. Rebuilding the cluster from a run (upstream currency P3,
docs/engineering/WHOLE-CLUSTER-RESTORE.md) provisions the logins again with the runtime's own
code, and this record is what the rebuilt cluster is checked against: which managed roles
existed, their attributes, login state, connection limit, role-wide settings and the roles they
are granted with each grant's INHERIT, SET and ADMIN options, the parameter privileges granted to
them (GRANT SET ON PARAMETER, which no dump carries), and the image the cluster ran. Passwords
are never read or recorded.

Only the roles the runtime manages are listed: the per-environment logins
(``<e>_auth``, ``_rest``, ``_storage``, ``_studio``, ``_realtime``, ``_developer``) and the
cluster roles ``storage_control``, ``management_auth`` and ``supabase_realtime_admin``. Roles
the image creates come back with the image; a role created by hand is not recorded.

Each kind has fixed attributes, the ones the runtime gives it (Realtime alone may replicate;
Studio and the developer login alone bypass row security; only the developer and Realtime logins
inherit; none is a superuser; supabase_realtime_admin never logs in). A role that breaks its
kind's rule is listed under ``refusals``, and so is an inventory that could not be taken: the run
itself still completes, and the rebuild refuses such a record.
"""
import json
import re

ENVIRONMENT_ROLE = re.compile(r'(e_[a-f0-9]{24})_(auth|rest|storage|studio|realtime|developer)')
CLUSTER_ROLES = ('storage_control', 'management_auth', 'supabase_realtime_admin')
ATTRIBUTES = ('rolsuper', 'rolinherit', 'rolcreaterole', 'rolcreatedb', 'rolreplication', 'rolbypassrls')
_BASE = dict.fromkeys(ATTRIBUTES, False)
# Login state is not part of a rule: Studio, the developer login and Realtime open and close.
RULES = {
    'auth': _BASE, 'rest': _BASE, 'storage': _BASE,
    'studio': {**_BASE, 'rolbypassrls': True},
    'developer': {**_BASE, 'rolinherit': True, 'rolbypassrls': True},
    'realtime': {**_BASE, 'rolinherit': True, 'rolreplication': True},
    'storage_control': _BASE, 'management_auth': _BASE,
    'supabase_realtime_admin': _BASE,
}
NEVER_LOGIN = {'supabase_realtime_admin'}
ROLE_FIELDS = {'name', 'kind', 'environment', *ATTRIBUTES, 'rolcanlogin', 'rolconnlimit', 'member_of', 'settings'}
GRANT_FIELDS = {'role', 'inherit', 'set', 'admin'}
RECORD_FIELDS = {'version', 'image', 'roles', 'parameters', 'refusals'}

ROLE_NAMES = r"""(r.rolname ~ '^e_[a-f0-9]{24}_(auth|rest|storage|studio|realtime|developer)$'
   OR r.rolname IN ('storage_control', 'management_auth', 'supabase_realtime_admin'))"""
QUERY = r"""
SELECT json_build_object(
 'roles', (SELECT coalesce(json_agg(json_build_object(
  'name', r.rolname,
  'rolsuper', r.rolsuper, 'rolinherit', r.rolinherit, 'rolcreaterole', r.rolcreaterole,
  'rolcreatedb', r.rolcreatedb, 'rolreplication', r.rolreplication, 'rolbypassrls', r.rolbypassrls,
  'rolcanlogin', r.rolcanlogin, 'rolconnlimit', r.rolconnlimit,
  'member_of', coalesce((SELECT json_agg(json_build_object('role', p.rolname, 'inherit', m.inherit_option,
                                                           'set', m.set_option, 'admin', m.admin_option)
                                         ORDER BY p.rolname)
                         FROM pg_auth_members m JOIN pg_roles p ON p.oid = m.roleid WHERE m.member = r.oid), '[]'::json),
  'settings', coalesce((SELECT to_json(s.setconfig) FROM pg_db_role_setting s
                        WHERE s.setrole = r.oid AND s.setdatabase = 0), '[]'::json)
 ) ORDER BY r.rolname), '[]'::json) FROM pg_roles r WHERE """ + ROLE_NAMES + r"""),
 'parameters', (SELECT coalesce(json_agg(json_build_object('parameter', a.parname, 'grantee', r.rolname,
                                                           'privilege', x.privilege_type)
                                         ORDER BY a.parname, r.rolname, x.privilege_type), '[]'::json)
                FROM pg_parameter_acl a, aclexplode(a.paracl) x JOIN pg_roles r ON r.oid = x.grantee
                WHERE """ + ROLE_NAMES + r""")
);
"""


def classify(name):
    """(kind, environment) of a managed role name, or None."""
    if name in CLUSTER_ROLES:
        return name, None
    match = ENVIRONMENT_ROLE.fullmatch(name)
    return (match.group(2), match.group(1)) if match else None


def _strings(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _grants(value):
    return isinstance(value, list) and all(
        isinstance(item, dict) and set(item) == GRANT_FIELDS and isinstance(item['role'], str)
        and all(item[key] is True or item[key] is False for key in ('inherit', 'set', 'admin')) for item in value)


def role_refusals(role):
    """What is wrong with one recorded role, as plain sentences; empty when it keeps its rule."""
    found = classify(role.get('name')) if isinstance(role.get('name'), str) else None
    if found is None:
        return [f"{role.get('name')!r} is not a role the runtime manages"]
    kind, _ = found
    rule = RULES[kind]
    problems = [f"{role['name']} has {key}={role.get(key)!r}, its kind keeps {rule[key]!r}"
                for key in ATTRIBUTES if role.get(key) is not rule[key]]
    if role.get('rolcanlogin') is not True and role.get('rolcanlogin') is not False:
        problems.append(f"{role['name']} has no login state")
    elif kind in NEVER_LOGIN and role['rolcanlogin']:
        problems.append(f"{role['name']} can log in")
    if type(role.get('rolconnlimit')) is not int:
        problems.append(f"{role['name']} has no connection limit")
    if not _grants(role.get('member_of')):
        problems.append(f"{role['name']} has malformed grants")
    if not _strings(role.get('settings')):
        problems.append(f"{role['name']} has malformed settings")
    return problems


def _parameters(value):
    return isinstance(value, list) and all(
        isinstance(item, dict) and set(item) == {'parameter', 'grantee', 'privilege'}
        and all(isinstance(item[key], str) for key in item) for item in value)


def from_rows(rows, image):
    """The record for what QUERY returned (its roles and parameter privileges) and the image."""
    roles, refusals = [], []
    for row in rows.get('roles') or [] if isinstance(rows, dict) else []:
        found = classify(row.get('name')) if isinstance(row, dict) and isinstance(row.get('name'), str) else None
        if found is None:
            refusals.append(f'unexpected row {row!r}')
            continue
        role = {**{key: row.get(key) for key in (*ATTRIBUTES, 'rolcanlogin', 'rolconnlimit')},
                'name': row['name'], 'kind': found[0], 'environment': found[1],
                'member_of': sorted(row.get('member_of') or [], key=lambda grant: str(grant.get('role'))
                                    if isinstance(grant, dict) else str(grant)),
                'settings': sorted(row.get('settings') or [], key=str)}
        refusals += role_refusals(role)
        roles.append(role)
    parameters = rows.get('parameters') if isinstance(rows, dict) else None
    if not _parameters(parameters):
        refusals.append('the parameter privileges are malformed')
        parameters = []
    return {'version': 1, 'image': image, 'roles': sorted(roles, key=lambda role: role['name']),
            'parameters': parameters, 'refusals': refusals}


def capture(sql, image):
    """Take the inventory with ``sql`` (lab/backup.py sql). Never raises: a failure is recorded."""
    try:
        rows = json.loads(sql(QUERY))
        if not isinstance(rows, dict) or not isinstance(rows.get('roles'), list):
            raise ValueError('not an inventory')
        return from_rows(rows, image)
    except Exception as error:  # noqa: BLE001: the backup run must not fail on this record
        return {'version': 1, 'image': image, 'roles': [], 'parameters': [],
                'refusals': [f'the cluster inventory could not be taken ({type(error).__name__})']}


def usable(record):
    """Every reason a rebuild must not rely on ``record``; empty when it can."""
    if not isinstance(record, dict) or record.get('version') != 1 or set(record) != RECORD_FIELDS:
        return ['the backup run has no well-formed cluster inventory']
    problems = []
    if not _strings(record['refusals']):
        problems.append('the cluster inventory refusals are malformed')
    else:
        problems += record['refusals']
    if not isinstance(record['image'], str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', record['image']):
        problems.append('the cluster inventory names no database image')
    if not _parameters(record['parameters']):
        problems.append('the parameter privileges are malformed')
    roles = record['roles']
    if not isinstance(roles, list):
        return problems + ['the cluster inventory lists no roles']
    seen = set()
    for role in roles:
        if not isinstance(role, dict) or set(role) != ROLE_FIELDS:
            problems.append('a recorded role is malformed')
            continue
        if not isinstance(role['name'], str):
            problems.append('a recorded role has no name')
            continue
        found = classify(role['name'])
        if found is not None and (role['kind'], role['environment']) != found:
            problems.append(f"{role['name']} is recorded under another kind or environment")
        if role['name'] in seen:
            problems.append(f"{role['name']} is recorded twice")
        seen.add(role['name'])
        problems += role_refusals(role)
    return problems


def take():
    """The inventory of the running cluster, for the backup run's installation manifest."""
    import subprocess
    import backup
    try:
        resolved = backup.resolve_image(backup.image_pin('distro-image.lock.json'))
    except Exception:  # noqa: BLE001: recorded as missing, never fatal to the run
        return capture(backup.sql, None)

    def sql(query):
        # Admitted once on the image just proved, then queried directly.
        container = backup.admit_database(resolved=resolved)
        result = subprocess.run(['docker', 'exec', '-i', container, 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1',
                                 '-U', 'supabase_admin', '-d', 'postgres'], input=query, capture_output=True,
                                text=True, timeout=600)
        if result.returncode:
            raise backup.BackupError('database statement failed')
        return result.stdout.strip()
    return capture(sql, resolved[1])
