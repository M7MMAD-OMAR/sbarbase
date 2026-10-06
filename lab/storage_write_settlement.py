"""Durable source contract for settling an exact original Storage writer set.

No native authority is installed by this module. Its execution adapters are
explicit fixtures; their receipts cannot authorize lifecycle effects. Original
native observation and installed launch/publication authority remain required.
"""
import contextlib
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat


CONTRACT = 'storage-write-settlement-v1'
SOURCE = 'a88e8eea60b69ee629f99f17b21cdb6578a2272a'
IMAGE = 'sha256:c24fb33cc2fa38d0f9582a30312907fc56da333fb9ba0646833186197e4982f3'
HEX = r'[a-f0-9]{64}'
UUID = r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}'
REF = r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,254}'
CONTEXT = ('binding', 'operation', 'purpose', 'actor', 'management_epoch',
           'installation', 'daemon', 'namespace', 'generation', 'routing_revision')
FEATURES = ('standard', 'signed', 'tus', 'multipart', 'queue', 'redis', 's3_protocol', 'remote_s3')
PHASES = ('enrolled', 'fence-pending', 'fenced', 'stop-pending', 'stopped',
          'reconcile-pending', 'settled', 'effect-pending', 'effect-recorded')


class Refused(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Refused(message)


def closed(value, keys):
    require(type(value) is dict and set(value) == set(keys), 'Closed object fields differ')
    return value


def text(value, pattern=REF):
    require(type(value) is str and re.fullmatch(pattern, value) is not None,
            'Invalid identity text')
    return value


def integer(value, minimum=0):
    require(type(value) is int and minimum <= value <= 9007199254740991,
            'Invalid safe integer')
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'),
                      allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def exact(left, right):
    """Typed JSON identity, including bool/int and integer/float distinctions."""
    try:
        return canonical(left) == canonical(right)
    except (TypeError, ValueError, UnicodeError):
        raise Refused('Invalid typed identity material') from None


def ordered_inventory_digest(value):
    """Keep Catalog JSON insertion order; this is not the manifest hash codec."""
    require(type(value) is list and 0 < len(value) <= 100 and
            all(type(item) is dict for item in value), 'Invalid source inventory')
    def check(item, depth=0, parents=None):
        require(depth <= 100, 'Inventory nesting bound exceeded')
        parents = set() if parents is None else parents
        container = type(item) in (dict, list)
        if container:
            require(id(item) not in parents, 'Cyclic inventory')
            parents.add(id(item))
        if type(item) is dict:
            require(all(type(key) is str for key in item), 'Invalid JSON key')
            for key, child in item.items():
                try:
                    key.encode('utf-8')
                except UnicodeError:
                    raise Refused('Invalid inventory key encoding') from None
                check(child, depth + 1, parents)
        elif type(item) is list:
            for child in item:
                check(child, depth + 1, parents)
        elif type(item) is int:
            integer(item, -9007199254740991)
        else:
            require(item is None or type(item) in (str, bool), 'Unsupported inventory value')
        if container:
            parents.remove(id(item))
    check(value)
    def javascript_order(item):
        if type(item) is dict:
            # ECMAScript enumeration puts canonical uint32 array-index keys
            # first, even when the source object inserted them in another order.
            indexes = [key for key in item if re.fullmatch(r'0|[1-9][0-9]*', key)
                       and len(key) <= 10 and int(key) < 4294967295]
            indexes.sort(key=int)
            keys = indexes + [key for key in item if key not in indexes]
            return {key: javascript_order(item[key]) for key in keys}
        if type(item) is list:
            return [javascript_order(child) for child in item]
        return item
    try:
        data = json.dumps(javascript_order(value), ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, UnicodeError):
        raise Refused('Invalid inventory encoding') from None
    return hashlib.sha256(data).hexdigest()


def binding(value):
    closed(value, ('environment', 'runtime', 'epoch', 'coverage', 'placement',
                   'inventoryDigest', 'placementDigest'))
    text(value['environment'], UUID)
    text(value['runtime'], r'e_[a-f0-9]{24}')
    integer(value['epoch'])
    require(value['coverage'] in ('dedicated-resources', 'complete-shared-resources',
                                  'disposable-fixture', None), 'Invalid coverage')
    require(value['placement'] in ('legacy-shared', 'native-dedicated'), 'Invalid placement')
    for key in ('inventoryDigest', 'placementDigest'):
        text(value[key], HEX)
    return copy.deepcopy(value)


def validate_manifest(value):
    closed(value, (*CONTEXT, 'version', 'source', 'image', 'backend', 'volume', 'database',
                   'writers', 'features', 'launch_paths', 'sourceInventory', 'neighbors'))
    require(value['version'] == 1 and type(value['version']) is int, 'Unsupported manifest version')
    binding(value['binding'])
    text(value['operation'], UUID)
    text(value['installation'], UUID)
    text(value['actor'])
    text(value['daemon'], HEX)
    text(value['namespace'])
    integer(value['management_epoch'])
    integer(value['routing_revision'])
    integer(value['generation'], 1)
    require(value['purpose'] in ('transfer', 'purge', 'backup', 'restore', 'migration'), 'Invalid purpose')
    require(value['source'] == SOURCE and value['image'] == IMAGE, 'Original Storage pin differs')
    require(value['backend'] in ('file', 's3'), 'Unknown backend')
    require(ordered_inventory_digest(value['sourceInventory']) == value['binding']['inventoryDigest'],
            'Catalog ordered inventory differs')
    volume = closed(value['volume'], ('id', 'resource', 'root', 'device', 'inode', 'mount_id',
                                      'namespace', 'tenant_prefix'))
    text(volume['id'])
    text(volume['resource'], UUID)
    text(volume['root'], r'/[A-Za-z0-9_./-]{1,1023}')
    require(not any(part in ('', '.', '..') for part in volume['root'].split('/')[1:]), 'Unsafe root')
    require(volume['namespace'] == value['namespace'], 'Volume namespace differs')
    require(volume['tenant_prefix'] == value['binding']['runtime'], 'Foreign tenant prefix')
    for key in ('device', 'inode', 'mount_id'):
        integer(volume[key], 1)
    db = closed(value['database'], ('container', 'image', 'name', 'oid'))
    text(db['container'], HEX)
    text(db['image'], r'sha256:' + HEX)
    text(db['name'], r'[a-z][a-z0-9_]{0,62}')
    require(integer(db['oid'], 1) <= 4294967295, 'Invalid database OID')
    writers = value['writers']
    require(type(writers) is list and 0 < len(writers) <= 100, 'Incomplete writer inventory')
    ids = []
    for writer in writers:
        closed(writer, ('container', 'owner', 'image', 'config_sha256', 'role', 'started_at',
                        'mount_source', 'mount_destination', 'scope'))
        ids.append(text(writer['container'], HEX))
        text(writer['owner'])
        require(writer['image'] == IMAGE, 'Writer is not the pinned original Storage image')
        text(writer['config_sha256'], HEX)
        require(writer['role'] in ('api', 'worker', 'admin', 'helper'), 'Unknown writer role')
        text(writer['started_at'], r'[0-9T:.Z+-]{1,64}')
        require(writer['mount_source'] == volume['id'], 'Writer file volume differs')
        text(writer['mount_destination'], r'/[A-Za-z0-9_./-]{1,1023}')
        require(writer['scope'] == value['binding']['runtime'], 'Global or neighboring writer reach')
    require(len(ids) == len(set(ids)), 'Duplicate writer identity')
    closed(value['features'], FEATURES)
    require(all(type(item) is bool for item in value['features'].values()), 'Invalid feature flag')
    require(value['features']['standard'] and value['features']['signed'], 'Standard ingress missing')
    require(value['backend'] != 's3' or value['features']['remote_s3'], 'Remote backend not declared')
    require(not value['features']['remote_s3'] or value['backend'] == 's3', 'Remote feature/backend differs')
    paths = value['launch_paths']
    require(type(paths) is list and paths and all(type(item) is str for item in paths)
            and len(paths) == len(set(paths)), 'Launch path inventory missing')
    for path in paths:
        text(path)
    require(type(value['neighbors']) is list and all(type(item) is str for item in value['neighbors'])
            and len(value['neighbors']) == len(set(value['neighbors'])),
            'Invalid neighbor inventory')
    for neighbor in value['neighbors']:
        text(neighbor, HEX)
    return copy.deepcopy(value)


def observe_closed(manifest, value, token, stopped):
    """Check exact observation material, without treating it as native authority."""
    closed(value, ('manifest_sha256', 'binding', 'installation', 'daemon', 'namespace',
                   'generation', 'routing_revision', 'sourceInventory', 'volume', 'writers',
                   'database', 'effects', 'launch_paths', 'fence_token', 'ingress',
                   'unlisted_writers', 'neighbors'))
    require(value['manifest_sha256'] == digest(manifest), 'Observation manifest differs')
    for key in ('binding', 'installation', 'daemon', 'namespace', 'generation', 'routing_revision',
                'sourceInventory', 'volume', 'neighbors'):
        require(exact(value[key], manifest[key]), 'Current authority identity differs: ' + key)
    require(ordered_inventory_digest(value['sourceInventory']) == manifest['binding']['inventoryDigest'],
            'Live inventory differs')
    require(value['fence_token'] == token and value['ingress'] == 'closed', 'Ingress fence unavailable')
    require(value['launch_paths'] == manifest['launch_paths'], 'Launch or recreation path not fenced')
    require(value['unlisted_writers'] == [], 'Unlisted writer reach')
    actual = value['writers']
    require(type(actual) is list and len(actual) == len(manifest['writers']), 'Writer set differs')
    for declared, observed in zip(manifest['writers'], actual):
        closed(observed, (*declared, 'state', 'restart_policy', 'pids'))
        require(all(exact(observed[key], declared[key]) for key in declared), 'Writer identity or process incarnation changed')
        require(observed['restart_policy'] == 'no', 'Restart remains enabled')
        require(type(observed['pids']) is list, 'Invalid process inventory')
        for pid in observed['pids']:
            integer(pid, 1)
        require(observed['state'] in ('running', 'stopped'), 'Unknown process state')
        if stopped:
            require(observed['state'] == 'stopped' and observed['pids'] == [], 'Writer process has not ended')
    db = closed(value['database'], (*manifest['database'], 'writer_sessions', 'prepared'))
    require(all(exact(db[key], item) for key, item in manifest['database'].items()), 'Database identity differs')
    if stopped:
        require(db['writer_sessions'] == [] and db['prepared'] == [], 'Database writers remain unresolved')
    enabled = {key for key in ('tus', 'multipart', 'queue', 'redis', 's3_protocol', 'remote_s3')
               if manifest['features'][key]}
    require(type(value['effects']) is dict and set(value['effects']) == enabled, 'Enabled effect coverage differs')
    for name, effect in value['effects'].items():
        closed(effect, ('namespace', 'generation', 'inventory_sha256', 'pending', 'active', 'state'))
        require(exact(effect['namespace'], manifest['namespace']) and exact(effect['generation'], manifest['generation']),
                'Effect generation or namespace differs')
        text(effect['inventory_sha256'], HEX)
        require(type(effect['pending']) is list and type(effect['active']) is list, 'Invalid effect inventory')
        for entry in effect['pending'] + effect['active']:
            text(entry)
        require(effect['state'] == 'quarantined', 'Effect replay not fenced')
        if stopped:
            require(effect['active'] == [], 'Backend effects still active')
        # A remote provider may finish after local process exit. There is no
        # installed provider barrier in this version, so even a fixture stops here.
        require(name != 'remote_s3', 'Original remote provider settlement authority unavailable')
    return copy.deepcopy(value)


def reconciliation(manifest, material):
    closed(material, ('manifest_sha256', 'rows_sha256', 'files_sha256', 'metadata_sha256',
                      'effects_sha256', 'unresolved'))
    require(material['manifest_sha256'] == digest(manifest) and material['unresolved'] == [],
            'Native state remains unresolved')
    for key in ('rows_sha256', 'files_sha256', 'metadata_sha256', 'effects_sha256'):
        text(material[key], HEX)
    return copy.deepcopy(material)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON key')
        result[key] = value
    return result


class Journal:
    """Private descriptor-bound append journal with fsync and serialized effects.

    Invalid or incomplete records require explicit operator reconciliation.
    Their disappearance is never proof that an issued native effect did not run.
    """
    def __init__(self, root):
        self.root = Path(root)

    @contextlib.contextmanager
    def locked(self):
        require(self.root.is_absolute(), 'Journal root must be explicit and absolute')
        root = os.open('/', os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in self.root.parts[1:]:
                require(part not in ('', '.', '..'), 'Unsafe journal root')
                child = os.open(part, os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
                os.close(root)
                root = child
            info = os.fstat(root)
            require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
                    'Journal directory must be private and owned')
            lock = os.open('lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=root)
            try:
                self._private(lock)
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                data = os.open('journal.jsonl', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=root)
                try:
                    self._private(data)
                    os.fsync(root)
                    yield LockedJournal(data)
                finally:
                    os.close(data)
            finally:
                os.close(lock)
        except (OSError, ValueError) as error:
            raise Refused('Journal is unavailable or already owned') from error
        finally:
            os.close(root)

    @staticmethod
    def _private(fd):
        value = os.fstat(fd)
        require(stat.S_ISREG(value.st_mode) and value.st_nlink == 1 and
                value.st_uid == os.getuid() and stat.S_IMODE(value.st_mode) == 0o600,
                'Journal file must be private, regular and singly linked')


class LockedJournal:
    def __init__(self, fd):
        self.fd = fd
        self.records = []
        size = os.fstat(fd).st_size
        require(size <= 8 * 1024 * 1024, 'Journal bound exceeded')
        data = os.pread(fd, size, 0)
        require(not data or data.endswith(b'\n'), 'Interrupted journal append requires reconciliation')
        previous = '0' * 64
        for line in data.splitlines():
            try:
                record = json.loads(line, object_pairs_hook=_pairs)
                closed(record, ('sequence', 'previous', 'manifest_sha256', 'phase', 'details', 'sha256'))
                require(type(record['sequence']) is int and record['sequence'] == len(self.records) + 1,
                        'Journal sequence differs')
                require(record['previous'] == previous and record['phase'] in PHASES, 'Journal chain differs')
                text(record['manifest_sha256'], HEX)
                text(record['sha256'], HEX)
                require(type(record['details']) is dict, 'Invalid journal details')
                body = {key: item for key, item in record.items() if key != 'sha256'}
                require(digest(body) == record['sha256'], 'Journal checksum differs')
                self._validate_transition(record)
            except (ValueError, UnicodeError):
                raise Refused('Malformed settlement journal') from None
            self.records.append(record)
            previous = record['sha256']

    def append(self, manifest, phase, details):
        require(phase in PHASES, 'Invalid journal phase')
        record = {'sequence': len(self.records) + 1,
                  'previous': self.records[-1]['sha256'] if self.records else '0' * 64,
                  'manifest_sha256': digest(manifest), 'phase': phase, 'details': copy.deepcopy(details)}
        record['sha256'] = digest(record)
        self._validate_transition(record)
        # Record order preserves the Catalog ordered inventory. Its checksum
        # uses the independent sorted-key codec above.
        data = json.dumps(record, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode() + b'\n'
        require(os.fstat(self.fd).st_size + len(data) <= 8 * 1024 * 1024, 'Journal bound exceeded')
        os.lseek(self.fd, 0, os.SEEK_END)
        while data:
            count = os.write(self.fd, data)
            require(count > 0, 'Journal append made no progress')
            data = data[count:]
        os.fsync(self.fd)
        self.records.append(record)
        return copy.deepcopy(record)

    def _validate_transition(self, record):
        phase = record['phase']
        previous = self.records[-1]['phase'] if self.records else None
        allowed = {
            'enrolled': (None,), 'fence-pending': ('enrolled',), 'fenced': ('fence-pending',),
            'stop-pending': ('fenced', 'stop-pending'),
            'stopped': ('fenced', 'stop-pending', 'stopped', 'reconcile-pending', 'settled'),
            'reconcile-pending': ('stopped',), 'settled': ('reconcile-pending',),
            'effect-pending': ('settled',), 'effect-recorded': ('effect-pending',),
        }
        require(previous in allowed[phase], 'Illegal settlement phase transition')
        fields = {
            'enrolled': ('manifest',), 'fence-pending': (),
            'fenced': ('fence_token', 'observation'), 'stop-pending': ('fence_token',),
            'stopped': ('observation',), 'reconcile-pending': ('fence_token',),
            'settled': ('observation', 'reconciliation'),
            'effect-pending': ('effect', 'receipt_sha256'), 'effect-recorded': ('effect', 'outcome'),
        }
        details = closed(record['details'], fields[phase])
        if phase == 'enrolled':
            declared = validate_manifest(details['manifest'])
        else:
            declared = self.records[0]['details']['manifest']
        require(record['manifest_sha256'] == digest(declared), 'Journal manifest changed')
        fences = [item for item in self.records if item['phase'] == 'fenced']
        token = fences[-1]['details']['fence_token'] if fences else details.get('fence_token')
        if 'fence_token' in details:
            text(details['fence_token'], UUID)
            require(token == details['fence_token'], 'Journal fence token changed')
        if 'observation' in details:
            observe_closed(declared, details['observation'], token, phase != 'fenced')
        if phase == 'settled':
            material = closed(details['reconciliation'], ('manifest_sha256', 'rows_sha256', 'files_sha256',
                                                         'metadata_sha256', 'effects_sha256', 'unresolved'))
            require(material['manifest_sha256'] == record['manifest_sha256'] and material['unresolved'] == [],
                    'Journal reconciliation is unresolved')
            for key in ('rows_sha256', 'files_sha256', 'metadata_sha256', 'effects_sha256'):
                text(material[key], HEX)
        if 'effect' in details:
            text(details['effect'])
            if phase == 'effect-pending':
                text(details['receipt_sha256'], HEX)
            else:
                require(details['outcome'] == 'recorded' and
                        details['effect'] == self.records[-1]['details']['effect'], 'Effect outcome differs')


class FixtureAuthority:
    """Test protocol only. No subclass or callback earns native admission."""
    def fence(self, manifest):
        raise Refused('Installed launch and ingress fence authority unavailable')

    def observe(self, manifest):
        raise Refused('Original native writer inventory authority unavailable')

    def stop(self, manifest, token):
        raise Refused('Exact native writer settlement authority unavailable')

    def reconcile(self, manifest, token):
        raise Refused('Original native file/database reconciliation unavailable')


class SourceSettlement:
    """Execute recovery protocol in source fixtures, retaining uncertain effects."""
    def __init__(self, manifest, journal, authority=None):
        self.manifest = validate_manifest(manifest)
        self.journal = journal
        self.authority = authority if authority is not None else FixtureAuthority()
        require(isinstance(self.authority, FixtureAuthority), 'Untyped callback is not authority')

    def settle(self):
        with self.journal.locked() as journal:
            manifest = self.manifest
            require(all(item['manifest_sha256'] == digest(manifest) for item in journal.records),
                    'Journal belongs to a different exact operation or generation')
            if not journal.records:
                journal.append(manifest, 'enrolled', {'manifest': manifest})
            require(not any(item['phase'].startswith('effect-') for item in journal.records),
                    'An issued consumer effect requires explicit reconciliation')
            # Every restart reobserves the persistent fence and exact current
            # writer universe. No acknowledgement in the journal replaces it.
            fence_records = [item for item in journal.records if item['phase'] == 'fenced']
            if fence_records:
                token = text(fence_records[-1]['details']['fence_token'], UUID)
            elif any(item['phase'] == 'fence-pending' for item in journal.records):
                # The prior fence may have completed with a lost acknowledgement.
                # Recover its exact current incarnation without issuing it again.
                existing = self.authority.observe(copy.deepcopy(manifest))
                require(type(existing) is dict, 'Pending fence observations unavailable')
                token = text(existing.get('fence_token'), UUID)
                observe_closed(manifest, existing, token, False)
            else:
                journal.append(manifest, 'fence-pending', {})
                token = text(self.authority.fence(copy.deepcopy(manifest)), UUID)
            observed = observe_closed(manifest, self.authority.observe(copy.deepcopy(manifest)), token, False)
            if not fence_records:
                journal.append(manifest, 'fenced', {'fence_token': token, 'observation': observed})
            if not all(item['state'] == 'stopped' and item['pids'] == [] for item in observed['writers']):
                journal.append(manifest, 'stop-pending', {'fence_token': token})
                self.authority.stop(copy.deepcopy(manifest), token)
            observed = observe_closed(manifest, self.authority.observe(copy.deepcopy(manifest)), token, True)
            journal.append(manifest, 'stopped', {'observation': observed})
            journal.append(manifest, 'reconcile-pending', {'fence_token': token})
            material = reconciliation(manifest, self.authority.reconcile(copy.deepcopy(manifest), token))
            final = observe_closed(manifest, self.authority.observe(copy.deepcopy(manifest)), token, True)
            require(exact(final, observed), 'Authority changed during reconciliation')
            record = journal.append(manifest, 'settled', {'observation': final, 'reconciliation': material})
            return self._receipt(record, token)

    def _receipt(self, record, token):
        return {'version': 1, 'contract': CONTRACT, 'evidence': 'fixture',
                **{key: copy.deepcopy(self.manifest[key]) for key in CONTEXT},
                'manifest_sha256': digest(self.manifest),
                'reconciliation_sha256': digest(record['details']['reconciliation']),
                'journal_sha256': record['sha256'], 'sequence': record['sequence'], 'fence_token': token}

    def revalidate(self, receipt):
        require(type(receipt) is dict, 'Receipt must be a closed object')
        receipt = copy.deepcopy(receipt)
        with self.journal.locked() as journal:
            require(journal.records and journal.records[-1]['phase'] == 'settled', 'Settlement is no longer current')
            record = journal.records[-1]
            require(record['manifest_sha256'] == digest(self.manifest), 'Manifest changed')
            expected = self._receipt(record, receipt.get('fence_token'))
            require(exact(receipt, expected), 'Receipt differs from durable current journal')
            token = record['details']['observation']['fence_token']
            require(token == receipt['fence_token'], 'Fence incarnation differs')
            actual = observe_closed(self.manifest, self.authority.observe(copy.deepcopy(self.manifest)), token, True)
            require(exact(actual, record['details']['observation']), 'Current observations differ from settled record')
            material = reconciliation(self.manifest, self.authority.reconcile(copy.deepcopy(self.manifest), token))
            require(exact(material, record['details']['reconciliation']), 'Data changed after settlement')
            final = observe_closed(self.manifest, self.authority.observe(copy.deepcopy(self.manifest)), token, True)
            require(exact(final, actual), 'Authority changed during receipt revalidation')
            return digest(receipt)

    def record_fixture_effect(self, receipt, effect, execute):
        """Discriminating lost-ack fixture, never an installed mutation entry point.

        The lock spans observations, durable pending, effect and outcome. Pending
        is terminal for automatic retry. Native consumers need their own current
        Catalog CAS in the same final mutation authority, after any awaited work.
        """
        require(callable(execute), 'Fixture effect must be callable')
        require(type(receipt) is dict, 'Receipt must be a closed object')
        receipt = copy.deepcopy(receipt)
        text(effect)
        with self.journal.locked() as journal:
            require(journal.records and journal.records[-1]['phase'] == 'settled', 'Consumer effect not settled')
            record = journal.records[-1]
            token = record['details']['observation']['fence_token']
            require(exact(receipt, self._receipt(record, token)), 'Stale receipt')
            actual = observe_closed(self.manifest, self.authority.observe(copy.deepcopy(self.manifest)), token, True)
            require(exact(actual, record['details']['observation']), 'Fence or inventory changed before effect')
            material = reconciliation(self.manifest, self.authority.reconcile(copy.deepcopy(self.manifest), token))
            require(exact(material, record['details']['reconciliation']), 'Data changed before effect')
            final = observe_closed(self.manifest, self.authority.observe(copy.deepcopy(self.manifest)), token, True)
            require(exact(final, actual),
                    'Fence changed during effect validation')
            journal.append(self.manifest, 'effect-pending', {'effect': effect, 'receipt_sha256': digest(receipt)})
            # Exceptions, interruption and lost acknowledgement retain pending.
            result = execute()
            require(type(result) is dict and result == {'outcome': 'recorded'}, 'Ambiguous effect outcome')
            journal.append(self.manifest, 'effect-recorded', {'effect': effect, 'outcome': 'recorded'})
            return copy.deepcopy(result)


def verify_lifecycle_settlement(receipt, expected_binding, sourceInventory, currentAuthority=None):
    """Import-safe production seam. Native authority is deliberately uninstalled."""
    binding(expected_binding)
    require(ordered_inventory_digest(sourceInventory) == expected_binding['inventoryDigest'],
            'Current Catalog inventory digest differs')
    raise Refused('Installed original native Storage settlement verifier unavailable')
