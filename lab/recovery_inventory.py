"""Strict portable material inventory for complete installation recovery.

Validation describes required material. It does not establish native acceptance,
read retained state, allocate resources, or authorize a lifecycle effect.
"""
import hashlib
import json
import re
from pathlib import PurePosixPath
from urllib.parse import urlsplit

HASH = re.compile(r'[a-f0-9]{64}')
UUID = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}')
RUNTIME = re.compile(r'e_[a-f0-9]{24}')
SAFE_INTEGER = (1 << 53) - 1
MAX_HEADER = 4 * 1024 * 1024
FEATURE_MATERIALS = {
    'auth': {'auth-recovery-settings', 'runtime-secrets'},
    'rest': {'service-settings'},
    'storage': {'signing-key-state'},
    'realtime': {'extension-config', 'runtime-secrets'},
    'functions': {'functions-bundle', 'functions-secrets'},
    'vault': {'data-keys', 'extension-config'},
    'cron': {'cron-queue-subscription-state'},
    'queues': {'cron-queue-subscription-state'},
    'webhooks': {'external-effects', 'extension-config'},
    'logical_subscriptions': {'cron-queue-subscription-state'},
    's3': {'service-settings', 'runtime-secrets'},
    'external_object_store': {'external-store-inventory'},
    'tus': {'tus-resume-state'},
    'iceberg': {'iceberg-state', 'external-store-inventory'},
    'management_identity': {'managed-keys', 'catalog', 'auth-recovery-settings'},
}
INSTALLATION_MATERIALS = {'catalog', 'managed-keys', 'service-settings', 'runtime-secrets',
                          'auth-recovery-settings', 'image-locks', 'extension-config',
                          'external-effects', 'data-keys', 'shared-storage-manifest',
                          'shared-storage-metadata'}
ENVIRONMENT_MATERIALS = {'environment-manifest', 'application-database', 'storage-object-files'}
MATERIAL_KINDS = INSTALLATION_MATERIALS | ENVIRONMENT_MATERIALS | set().union(*FEATURE_MATERIALS.values()) | {'external-object-bytes', 'external-store-credentials'}
BINDING_KEYS = {'environment', 'runtime', 'epoch', 'coverage', 'placement',
                'inventoryDigest', 'placementDigest'}


class Refused(ValueError):
    """A nonsecret contract refusal."""


def exact(value, keys, reason):
    if type(value) is not dict or set(value) != set(keys):
        raise Refused(reason)


def integer(value, minimum=0):
    return type(value) is int and minimum <= value <= SAFE_INTEGER


def canonical(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=True, allow_nan=False).encode('ascii')
    except (TypeError, ValueError, UnicodeError):
        raise Refused('Nonportable JSON recovery inventory') from None
    if len(raw) > MAX_HEADER:
        raise Refused('Recovery inventory exceeds header budget')
    return raw


def catalog_inventory_digest(resources):
    """Match existing Catalog JSON.stringify resource order, not set hashing.

    Recovery-set hashing separately uses sorted object keys. A current trusted
    Catalog supplies its ordered resource snapshot at admission time.
    """
    try:
        raw = json.dumps(resources, ensure_ascii=False, separators=(',', ':'),
                         allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError):
        raise Refused('Invalid current catalog resource snapshot') from None
    return hashlib.sha256(raw).hexdigest()


def binding(value):
    exact(value, BINDING_KEYS, 'Complete source lifecycle binding required')
    if (not isinstance(value['environment'], str) or not UUID.fullmatch(value['environment'])
            or not isinstance(value['runtime'], str) or not RUNTIME.fullmatch(value['runtime'])
            or not integer(value['epoch'])
            or value['coverage'] not in ('complete-shared-resources', 'dedicated-resources')
            or value['placement'] not in ('legacy-shared', 'native-dedicated')
            or any(not isinstance(value[key], str) or not HASH.fullmatch(value[key])
                   for key in ('inventoryDigest', 'placementDigest'))):
        raise Refused('Source lifecycle binding is invalid or fixture-only')
    return value


def host(value):
    exact(value, {'host', 'daemon', 'installation'}, 'Explicit host and installation identity required')
    if any(not isinstance(value[key], str) or not value[key] or len(value[key]) > 256
           or '\x00' in value[key] for key in value):
        raise Refused('Invalid host identity')
    if not UUID.fullmatch(value['installation']):
        raise Refused('Invalid installation identity')
    return value


def logical_name(value):
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise Refused('Invalid recovery member name')
    if PurePosixPath(value).is_absolute() or any(part in ('', '.', '..') for part in value.split('/')):
        raise Refused('Recovery member escapes portable root')
    return value


def text_identity(value):
    return isinstance(value, str) and bool(value) and not any(ord(c) < 32 for c in value)


def owned_path(value):
    if (not text_identity(value) or not value.startswith('/') or value == '/'
            or str(PurePosixPath(value)) != value or '..' in value.split('/')):
        raise Refused('Absolute canonical owned directory identity required')


def file_identity(value):
    exact(value, {'id', 'resource', 'device', 'inode', 'marker'}, 'Exact enrolled file identity required')
    owned_path(value['id'])
    if (not isinstance(value['resource'], str) or not UUID.fullmatch(value['resource'])
            or any(not integer(value[k], 1) for k in ('device', 'inode'))
            or value['marker'] != '.sbarbase-lifecycle-owner.json'):
        raise Refused('Invalid enrolled file identity')


def shared_identity(value, runtime):
    exact(value, {'engine', 'database', 'roles', 'tenant', 'files'}, 'Complete shared database identity required')
    engine, database, tenant = (value[k] for k in ('engine', 'database', 'tenant'))
    exact(engine, {'id', 'owner', 'daemon'}, 'Shared engine identity required')
    exact(database, {'name', 'oid', 'ownerOid'}, 'Database OID identity required')
    if (not isinstance(engine['id'], str) or not HASH.fullmatch(engine['id'])
            or not text_identity(engine['owner']) or engine['owner'] in ('recovery-target', 'restore-target')
            or not text_identity(engine['daemon']) or database['name'] != runtime
            or any(not integer(database[k], 1) for k in ('oid', 'ownerOid'))):
        raise Refused('Invalid shared engine or database identity')
    if not isinstance(value['roles'], list):
        raise Refused('Complete shared role identities required')
    names, oids = set(), set()
    allowed = {runtime + '_' + k for k in ('auth', 'rest', 'storage', 'studio', 'developer')}
    for role in value['roles']:
        exact(role, {'name', 'oid', 'login'}, 'Role OID and login identity required')
        if (role['name'] not in allowed or role['name'] in names or not integer(role['oid'], 1)
                or role['oid'] in oids or type(role['login']) is not bool):
            raise Refused('Invalid shared role identity')
        names.add(role['name']); oids.add(role['oid'])
    if not {runtime + '_' + k for k in ('auth', 'rest', 'storage')} <= names:
        raise Refused('Shared database service roles are missing')
    exact(tenant, {'database', 'oid', 'id', 'rowDigest', 'writers'}, 'Shared tenant identity required')
    if (tenant['database'] != 'storage_metadata' or tenant['id'] != runtime or not integer(tenant['oid'], 1)
            or not isinstance(tenant['rowDigest'], str) or not HASH.fullmatch(tenant['rowDigest'])
            or not isinstance(tenant['writers'], list) or not tenant['writers']
            or any(not isinstance(cid, str) or not HASH.fullmatch(cid) for cid in tenant['writers'])
            or len(set(tenant['writers'])) != len(tenant['writers'])):
        raise Refused('Invalid shared tenant or writer identity')
    file_identity(value['files'])
    if not value['files']['id'].endswith('/' + runtime):
        raise Refused('Shared tenant files belong to a different runtime')


def resources(values, runtime=None):
    if not isinstance(values, list) or not values:
        raise Refused('Exact enrolled resource inventory required')
    seen, resource_ids = set(), set()
    common = {'kind', 'id', 'resource', 'installation', 'runtime'}
    for item in values:
        if not isinstance(item, dict) or not common <= set(item):
            raise Refused('Resource identity is incomplete')
        if (item['kind'] not in ('container', 'volume', 'directory', 'shared-database')
                or not isinstance(item['id'], str) or not item['id']
                or not isinstance(item['resource'], str) or not UUID.fullmatch(item['resource'])
                or not isinstance(item['installation'], str) or not UUID.fullmatch(item['installation'])
                or not isinstance(item['runtime'], str) or not RUNTIME.fullmatch(item['runtime'])
                or (runtime is not None and item['runtime'] != runtime)):
            raise Refused('Resource identity differs from its runtime')
        identity = (item['kind'], item['id'])
        if identity in seen or item['resource'] in resource_ids:
            raise Refused('Duplicate physical resource identity')
        seen.add(identity); resource_ids.add(item['resource'])
        if item['kind'] == 'container':
            exact(item, common | ({'service'} if 'service' in item else set()) | ({'legacy'} if 'legacy' in item else set()), 'Closed container identity required')
            if not HASH.fullmatch(item['id']) or ('service' in item and item['service'] not in ('auth', 'rest', 'database', 'storage')):
                raise Refused('Immutable container and service identity required')
            if 'legacy' in item:
                legacy = item['legacy']
                exact(legacy, {'owner', 'image', 'createdAt', 'configDigest', 'endpoint', 'databaseEngine', 'databaseOid'}, 'Exact legacy service identity required')
                if (item.get('service') not in ('auth', 'rest') or not text_identity(legacy['owner'])
                        or legacy['owner'] in ('recovery-target', 'restore-target')
                        or not isinstance(legacy['image'], str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', legacy['image'])
                        or any(not isinstance(legacy[k], str) or not HASH.fullmatch(legacy[k]) for k in ('configDigest', 'databaseEngine'))
                        or not integer(legacy['databaseOid'], 1) or not text_identity(legacy['createdAt'])
                        or not text_identity(legacy['endpoint'])):
                    raise Refused('Invalid immutable legacy service identity')
        elif item['kind'] == 'volume':
            exact(item, common | {'createdAt'}, 'Exact volume creation identity required')
            if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}', item['id']) or not text_identity(item['createdAt']):
                raise Refused('Invalid immutable volume identity')
        elif item['kind'] == 'directory':
            exact(item, common | {'device', 'inode', 'marker'}, 'Exact directory identity required')
            file_identity({k: item[k] for k in ('id', 'resource', 'device', 'inode', 'marker')})
        else:
            exact(item, common | {'identity'}, 'Exact shared database identity required')
            if item['id'] != item['runtime']:
                raise Refused('Shared database runtime identity differs')
            shared_identity(item['identity'], item['runtime'])
    for item in values:
        if item['kind'] == 'shared-database':
            files = item['identity']['files']
            if not any(r['kind'] == 'directory' and r['runtime'] == item['runtime'] and r['installation'] == item['installation']
                       and all(r[k] == files[k] for k in files) for r in values):
                raise Refused('Shared database requires exact enrolled tenant files')
    canonical(values)
    return values


def coverage(values, bound):
    resources(values, bound['runtime'])
    containers = [r for r in values if r['kind'] == 'container']
    shared = [r for r in values if r['kind'] == 'shared-database']
    if bound['placement'] == 'legacy-shared' and bound['coverage'] == 'complete-shared-resources':
        if len(shared) != 1 or not all(any(r.get('service') == k for r in containers) for k in ('auth', 'rest')):
            raise Refused('Complete shared resource coverage required')
    elif bound['placement'] == 'native-dedicated' and bound['coverage'] == 'dedicated-resources':
        if (shared or len(containers) != 4 or not any(r['kind'] == 'volume' for r in values)
                or not any(r['kind'] == 'directory' for r in values)
                or not all(sum(r.get('service') == k for r in containers) == 1 for k in ('database', 'auth', 'rest', 'storage'))):
            raise Refused('Complete dedicated resource coverage required')
    else:
        raise Refused('Placement and resource coverage differ')


def external(value):
    exact(value, {'runtime', 'feature', 'policy', 'store', 'objects', 'credentials_material'}, 'Explicit scoped external object-store policy required')
    if not isinstance(value['runtime'], str) or not RUNTIME.fullmatch(value['runtime']) or value['feature'] not in ('storage', 'iceberg'):
        raise Refused('Invalid object-store runtime or feature scope')
    if value['policy'] not in ('included-file-bytes', 'authenticated-independent-copy', 'immutable-external-dependency'):
        raise Refused('Unadmitted external object-store policy')
    if not isinstance(value['objects'], list):
        raise Refused('External object version inventory required')
    if value['policy'] == 'included-file-bytes':
        if value['store'] is not None or value['objects'] or value['credentials_material'] is not None:
            raise Refused('File backend cannot conceal an external dependency')
        return value
    exact(value['store'], {'provider', 'endpoint', 'bucket'}, 'Immutable external store identity required')
    if any(not isinstance(v, str) or not v for v in value['store'].values()):
        raise Refused('External store identity is incomplete')
    try:
        address = urlsplit(value['store']['endpoint'])
        port = address.port
    except ValueError:
        raise Refused('Malformed immutable external store address') from None
    if (address.scheme != 'https' or not address.hostname or address.username or address.password
            or (port is not None and not 1 <= port <= 65535)
            or address.query or address.fragment or any(ord(c) < 33 for c in value['store']['endpoint'])):
        raise Refused('Credential-free immutable HTTPS store address required')
    logical_name(value['credentials_material'])
    seen = set()
    for item in value['objects']:
        exact(item, {'key', 'version', 'bytes', 'sha256', 'material'}, 'External version identity required')
        if (not isinstance(item['key'], str) or not item['key']
                or not isinstance(item['version'], str) or not item['version']
                or not integer(item['bytes']) or not isinstance(item['sha256'], str)
                or not HASH.fullmatch(item['sha256'])):
            raise Refused('Unbound external object bytes or version')
        identity = (item['key'], item['version'])
        if identity in seen:
            raise Refused('Duplicate external object version')
        seen.add(identity)
        if value['policy'] == 'authenticated-independent-copy':
            logical_name(item['material'])
        elif item['material'] is not None:
            raise Refused('External dependency cannot impersonate an included copy')
    return value


def _validate_manifest(value):
    exact(value, {'schema', 'format', 'source_host', 'source', 'environments', 'materials',
                  'features', 'external_stores', 'secrets'}, 'Complete installation recovery manifest required')
    if value['schema'] != 1 or type(value['schema']) is not int or value['format'] != 'sbarbase-installation-recovery-v1':
        raise Refused('Unsupported recovery set format')
    host(value['source_host'])
    exact(value['source'], {'algorithm', 'sha256', 'files'}, 'Public source identity required')
    if (value['source']['algorithm'] != 'sbarbase-public-source-v1'
            or not isinstance(value['source']['sha256'], str) or not HASH.fullmatch(value['source']['sha256'])
            or not integer(value['source']['files'], 1)):
        raise Refused('Invalid public source identity')
    if not isinstance(value['environments'], list) or not value['environments']:
        raise Refused('Published application inventory is empty')
    runtime_ids, environment_ids, resource_ids, physical_ids = set(), set(), set(), set()
    for item in value['environments']:
        exact(item, {'binding', 'resources'}, 'Environment resource binding required')
        bound = binding(item['binding'])
        coverage(item['resources'], bound)
        if catalog_inventory_digest(item['resources']) != bound['inventoryDigest']:
            raise Refused('Environment inventory digest contradicts included resources')
        if bound['runtime'] in runtime_ids or bound['environment'] in environment_ids:
            raise Refused('Duplicate application identity')
        if any(r['installation'] != value['source_host']['installation'] for r in item['resources']):
            raise Refused('Source resource belongs to a different installation')
        for r in item['resources']:
            if r['resource'] in resource_ids or (r['kind'], r['id']) in physical_ids:
                raise Refused('Installation runtimes alias an enrolled resource')
            resource_ids.add(r['resource']); physical_ids.add((r['kind'], r['id']))
            if r['kind'] == 'shared-database' and r['identity']['engine']['daemon'] != value['source_host']['daemon']:
                raise Refused('Shared engine daemon differs from source host')
        runtime_ids.add(bound['runtime']); environment_ids.add(bound['environment'])
    if not isinstance(value['materials'], list) or not value['materials']:
        raise Refused('Recovery materials are absent')
    members, scopes = {}, set()
    for item in value['materials']:
        exact(item, {'name', 'kind', 'runtime', 'bytes', 'sha256'}, 'Recovery material identity required')
        logical_name(item['name'])
        if (not isinstance(item['kind'], str) or item['kind'] not in MATERIAL_KINDS or not integer(item['bytes'])
                or not isinstance(item['sha256'], str) or not HASH.fullmatch(item['sha256'])
                or item['runtime'] not in runtime_ids | {None}):
            raise Refused('Invalid recovery material binding')
        identity = (item['kind'], item['runtime'])
        if item['name'] in members or (identity in scopes and item['kind'] != 'external-object-bytes'):
            raise Refused('Duplicate recovery material')
        members[item['name']] = item; scopes.add(identity)
    if not {(k, None) for k in INSTALLATION_MATERIALS} <= scopes:
        raise Refused('Required installation material is missing')
    for runtime in runtime_ids:
        if not {(k, runtime) for k in ENVIRONMENT_MATERIALS} <= scopes:
            raise Refused('Required application database or object material is missing')
    if not isinstance(value['features'], list):
        raise Refused('Complete native feature inventory required')
    feature_ids = set()
    for item in value['features']:
        exact(item, {'id', 'runtime', 'enabled', 'materials'}, 'Scoped feature inventory record required')
        identity = (item['id'], item['runtime'])
        if (not isinstance(item['id'], str) or item['id'] not in FEATURE_MATERIALS
                or identity in feature_ids or type(item['enabled']) is not bool
                or (item['runtime'] is not None and item['runtime'] not in runtime_ids)
                or (item['id'] == 'management_identity') != (item['runtime'] is None)):
            raise Refused('Unknown, duplicate or ambiguous native capability')
        feature_ids.add(identity)
        if not isinstance(item['materials'], list) or any(not isinstance(n, str) for n in item['materials']) or len(set(item['materials'])) != len(item['materials']):
            raise Refused('Invalid feature material references')
        if any(name not in members for name in item['materials']):
            raise Refused('Feature material is absent from encrypted set')
        if item['enabled']:
            if any(members[n]['runtime'] != item['runtime'] for n in item['materials']):
                raise Refused('Feature material belongs to a different runtime scope')
            kinds = {members[name]['kind'] for name in item['materials']}
            if not FEATURE_MATERIALS[item['id']] <= kinds:
                raise Refused('Enabled capability lacks required recovery material')
        elif item['materials']:
            raise Refused('Disabled capability has unclassified retained state')
    required_features = {(key, runtime) for key in FEATURE_MATERIALS if key != 'management_identity' for runtime in runtime_ids}
    required_features.add(('management_identity', None))
    feature_map = {(i['id'], i['runtime']): i for i in value['features']}
    required_enabled = {('management_identity', None)} | {(key, runtime) for key in ('auth', 'rest', 'storage') for runtime in runtime_ids}
    if feature_ids != required_features or any(not feature_map[key]['enabled'] for key in required_enabled):
        raise Refused('Complete original application feature coverage required')
    if not isinstance(value['external_stores'], list):
        raise Refused('Complete runtime object-store policy inventory required')
    expected_stores = {(r, 'storage') for r in runtime_ids} | {(r, 'iceberg') for r in runtime_ids if feature_map[('iceberg', r)]['enabled']}
    store_ids, copy_names = set(), set()
    for policy in value['external_stores']:
        external(policy)
        identity = (policy['runtime'], policy['feature'])
        if identity not in expected_stores or identity in store_ids:
            raise Refused('Unknown or duplicate object-store scope')
        store_ids.add(identity)
        is_external = policy['policy'] != 'included-file-bytes'
        if policy['feature'] == 'storage' and is_external != feature_map[('external_object_store', policy['runtime'])]['enabled']:
            raise Refused('External store policy contradicts enabled native state')
        if is_external:
            credential = members.get(policy['credentials_material'])
            if credential is None or credential['kind'] != 'external-store-credentials' or credential['runtime'] != policy['runtime']:
                raise Refused('Scoped external store credential material required')
        for obj in policy['objects']:
            if obj['material'] is None:
                continue
            member = members.get(obj['material'])
            if (member is None or member['kind'] != 'external-object-bytes' or member['runtime'] != policy['runtime']
                    or member['bytes'] != obj['bytes'] or member['sha256'] != obj['sha256'] or member['name'] in copy_names):
                raise Refused('Independent object copy does not bind exact scoped member bytes')
            copy_names.add(member['name'])
    if store_ids != expected_stores:
        raise Refused('Object-store policy omits a recovered runtime')
    if {m['name'] for m in members.values() if m['kind'] == 'external-object-bytes'} != copy_names:
        raise Refused('External object bytes lack an exact version inventory')
    if not isinstance(value['secrets'], list) or not value['secrets']:
        raise Refused('Secret preservation and rotation inventory required')
    identifiers = set()
    for item in value['secrets']:
        exact(item, {'id', 'policy', 'material'}, 'Classified secret reference required')
        if (not isinstance(item['id'], str) or not item['id'] or item['id'] in identifiers
                or item['policy'] not in ('preserve-data-key', 'rewrap-data-key', 'rotate-platform-access', 'rebind-application-setting')
                or item['material'] not in members):
            raise Refused('Unknown or missing secret preservation policy')
        identifiers.add(item['id'])
    classified = {item['id']: item['policy'] for item in value['secrets']}
    mandatory_rotate = {'postgres-root', 'storage-admin', 'storage-control', 'management-session'}
    mandatory_rotate.update('runtime:' + runtime + ':' + kind for runtime in runtime_ids
                            for kind in ('auth-db', 'rest-db', 'storage-db', 'jwt'))
    if any(classified.get(key) != 'rotate-platform-access' for key in mandatory_rotate):
        raise Refused('Platform access credential rotation inventory is incomplete')
    preserved = {'storage-url-signing:' + runtime for runtime in runtime_ids}
    if any(classified.get(key) != 'preserve-data-key' for key in preserved):
        raise Refused('Original URL signing material preservation is required')
    if classified.get('storage-data-encryption') not in ('preserve-data-key', 'rewrap-data-key'):
        raise Refused('Tenant ciphertext decryption material is required')
    for runtime in runtime_ids:
        if feature_map[('vault', runtime)]['enabled'] and classified.get('vault-root:' + runtime) not in ('preserve-data-key', 'rewrap-data-key'):
            raise Refused('Vault root data key is unavailable')
    secret_map = {i['id']: i for i in value['secrets']}
    for runtime in runtime_ids:
        names = {'runtime:' + runtime + ':' + k: ('runtime-secrets', runtime) for k in ('auth-db', 'rest-db', 'storage-db', 'jwt')}
        names['storage-url-signing:' + runtime] = ('signing-key-state', runtime)
        if feature_map[('vault', runtime)]['enabled']:
            names['vault-root:' + runtime] = ('data-keys', runtime)
        for name, wanted in names.items():
            member = members[secret_map[name]['material']]
            if (member['kind'], member['runtime']) != wanted:
                raise Refused('Classified secret material differs from its runtime and purpose')
    for name in ('postgres-root', 'storage-admin', 'storage-control', 'management-session', 'storage-data-encryption'):
        member = members[secret_map[name]['material']]
        wanted = 'data-keys' if name == 'storage-data-encryption' else 'runtime-secrets'
        if member['kind'] != wanted or member['runtime'] is not None:
            raise Refused('Installation secret material differs from its purpose')
    canonical(value)
    return value


def validate_manifest(value):
    try:
        return _validate_manifest(value)
    except (TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise Refused('Malformed recovery inventory') from None


def recovery_set_digest(manifest):
    """Portable metadata/member digest binding; never native acceptance."""
    validate_manifest(manifest)
    return hashlib.sha256(canonical(manifest)).hexdigest()
