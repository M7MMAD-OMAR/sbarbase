"""Private transfer credential reservation and native effect uncertainty journal.

The caller supplies the immutable admitted identities and holds public runtime guards.
This module performs no native effects and never restores retired credentials. Keep
the context open across an effect: before_effect must be durable before action, and
after_effect accepts only a checked, credential-free observation. A pending effect
is uncertainty, not permission to repeat a native action without reconciliation.
"""
import copy
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import time
import uuid


MAX_BYTES = 128 * 1024
MAX_EFFECTS = 128
MAX_INTEGER = 9007199254740991
LOCK = '.transfer.lock'
CREDENTIAL_BYTES = {'auth': 32, 'rest': 32, 'storage': 32, 'jwt': 32,
                    'realtime': 32, 'realtime_api': 32, 'realtime_base': 64,
                    'realtime_enc': 8}
RESOURCE_KINDS = {'database', 'role', 'container', 'storage_tenant', 'runtime',
                  'project', 'inventory', 'directory'}
BINDING_FIELDS = {'operation', 'environment', 'runtime', 'project', 'source',
                  'destination', 'actor', 'installation', 'runtime_epoch',
                  'management_epoch', 'inventory_digest', 'daemon_digest',
                  'engine_id', 'database_oid', 'roles', 'routing_revision'}


class TransferJournalError(RuntimeError):
    """A fixed reason code, never credential contents or an OS exception."""


def _fail(reason):
    raise TransferJournalError(reason)


def _integer(value, maximum=MAX_INTEGER):
    return type(value) is int and 0 <= value <= maximum


def _digest(value):
    return type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _uuid(value):
    if type(value) is not str:
        return False
    try:
        return str(uuid.UUID(value)) == value and uuid.UUID(value).int != 0
    except ValueError:
        return False


def _oid(value):
    return (type(value) is str and re.fullmatch(r'[1-9][0-9]{0,9}', value) is not None
            and int(value) <= 4294967295)


def validate_binding(value):
    if type(value) is not dict or set(value) != BINDING_FIELDS:
        _fail('BINDING_INVALID')
    if not all(_uuid(value[key]) for key in ('operation', 'environment', 'project',
                                            'source', 'destination', 'actor', 'installation')):
        _fail('BINDING_INVALID')
    runtime = value['runtime']
    if type(runtime) is not str or re.fullmatch(r'e_[0-9a-f]{24}', runtime) is None:
        _fail('BINDING_INVALID')
    if value['source'] == value['destination']:
        _fail('BINDING_INVALID')
    if not all(_integer(value[key]) for key in ('runtime_epoch', 'management_epoch', 'routing_revision')):
        _fail('BINDING_INVALID')
    if value['runtime_epoch'] < 1:
        _fail('BINDING_INVALID')
    if not all(_digest(value[key]) for key in ('inventory_digest', 'daemon_digest', 'engine_id')):
        _fail('BINDING_INVALID')
    roles = value['roles']
    required = {runtime + '_' + kind for kind in ('auth', 'rest', 'storage')}
    allowed = required | {runtime + '_' + kind for kind in ('realtime', 'developer', 'studio')}
    if (not _oid(value['database_oid']) or type(roles) is not dict
            or not required <= set(roles) <= allowed or not all(_oid(oid) for oid in roles.values())
            or len(set(roles.values())) != len(roles)):
        _fail('BINDING_INVALID')
    return copy.deepcopy(value)


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def _material_digest(credentials):
    return hashlib.sha256(_encode(credentials)).hexdigest()


def _credentials(value):
    if type(value) is not dict or set(value) != set(CREDENTIAL_BYTES) | {'developer'}:
        _fail('MATERIAL_INVALID')
    for name, size in CREDENTIAL_BYTES.items():
        if type(value[name]) is not str or re.fullmatch('[0-9a-f]{' + str(size * 2) + '}', value[name]) is None:
            _fail('MATERIAL_INVALID')
    if type(value['developer']) is not str or re.fullmatch(r'[A-Za-z0-9_-]{32}', value['developer']) is None:
        _fail('MATERIAL_INVALID')


def _step(value):
    # Short step identifiers only, never free-form diagnostics or command text.
    if type(value) is not str or re.fullmatch(r'[a-z][a-z_]{0,47}(?:\.[0-9]{1,3})?', value) is None:
        _fail('EFFECT_INVALID')


def _resource(value, binding):
    if type(value) is not dict or not {'resource_kind', 'resource_id'} <= set(value):
        _fail('OBSERVATION_INVALID')
    kind, identity = value['resource_kind'], value['resource_id']
    if type(kind) is not str or kind not in RESOURCE_KINDS or type(identity) is not str:
        _fail('OBSERVATION_INVALID')
    if kind == 'database':
        valid = identity == binding['database_oid']
    elif kind == 'role':
        valid = identity in binding['roles']
    elif kind == 'container':
        valid = _digest(identity)
    elif kind == 'project':
        valid = identity == binding['project']
    else:
        valid = identity == binding['runtime']
    if not valid:
        _fail('RESOURCE_BINDING_DIFFERS')


def _observation(value, binding, credentials, *, receipt=False):
    fields = {'resource_kind', 'resource_id', 'before_digest'}
    required = fields
    if receipt:
        fields = {'resource_kind', 'resource_id', 'outcome', 'observed_digest',
                  'signing_kid', 'checks', 'sessions', 'old_rejected',
                  'replacement_accepted', 'neighbor_unchanged'}
        required = {'resource_kind', 'resource_id', 'outcome'}
    if type(value) is not dict or not required <= set(value) <= fields:
        _fail('OBSERVATION_INVALID')
    _resource(value, binding)
    for key in ('before_digest', 'observed_digest'):
        if key in value and not _digest(value[key]):
            _fail('OBSERVATION_INVALID')
    if receipt:
        if value['outcome'] not in ('verified', 'blocked'):
            _fail('OBSERVATION_INVALID')
        if 'signing_kid' in value and (value['resource_kind'] != 'storage_tenant' or not _uuid(value['signing_kid'])):
            _fail('OBSERVATION_INVALID')
        if any(not _integer(value[key], 1000000) for key in ('checks', 'sessions') if key in value):
            _fail('OBSERVATION_INVALID')
        if any(type(value[key]) is not bool for key in ('old_rejected', 'replacement_accepted', 'neighbor_unchanged') if key in value):
            _fail('OBSERVATION_INVALID')
    # Reject accidentally copied reserved secrets, including in digest-shaped fields.
    serialized = _encode(value).decode('ascii')
    if any(secret in serialized for secret in credentials.values()):
        _fail('CREDENTIAL_IN_OBSERVATION')
    return copy.deepcopy(value)


def _file_metadata(value):
    return (value.st_dev, value.st_ino, value.st_uid, value.st_mode, value.st_nlink,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _identity(value):
    return value.st_dev, value.st_ino, value.st_uid, value.st_mode


def _private_file(value):
    if (not stat.S_ISREG(value.st_mode) or stat.S_IMODE(value.st_mode) != 0o600
            or value.st_uid != os.geteuid() or value.st_nlink != 1):
        _fail('FILE_METADATA_REFUSED')


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _fail('RECORD_INVALID')
        result[key] = value
    return result


class TransferJournal:
    """One held installation lock, anchored private directory and immutable reservation.

    directory must already exist, be absolute, owned by the running uid and mode700.
    A durable publication marker or candidate left by interruption requires explicit
    filesystem reconciliation. Opening acquires the lock even with unresolved
    publication evidence; only reconcile_publication can then inspect/adopt it.
    Unknown evidence is never discarded or regenerated by this API.
    """
    def __init__(self, directory, binding):
        self._binding = validate_binding(binding)
        self.name = self.binding['operation'] + '.' + self.binding['runtime'] + '.json'
        self.pending_prefix = '.' + self.name + '.pending-'
        self.marker = '.' + self.name + '.publication'
        self.fds, self.anchors = [], []
        self.directory_fd = self.lock_fd = None
        self.poisoned = False
        self.trusted_material_digest = None
        try:
            if type(directory) is not str or not directory.startswith('/') or len(directory) > 4096:
                _fail('DIRECTORY_INVALID')
            parts = directory[1:].split('/')
            if any(not part or part in ('.', '..') or '\x00' in part for part in parts):
                _fail('DIRECTORY_INVALID')
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            current = os.open('/', flags)
            self.fds.append(current)
            for part in parts:
                parent = current
                current = os.open(part, flags, dir_fd=parent)
                self.fds.append(current)
                witness = _identity(os.fstat(current))
                self.anchors.append((parent, part, current, witness))
            self.directory_fd = current
            value = os.fstat(current)
            if value.st_uid != os.geteuid() or stat.S_IMODE(value.st_mode) != 0o700 or value.st_nlink < 2:
                _fail('DIRECTORY_POLICY_REFUSED')
            self._directory()
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
            try:
                self.lock_fd = os.open(LOCK, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=current)
            except FileExistsError:
                self.lock_fd = os.open(LOCK, flags, dir_fd=current)
            _private_file(os.fstat(self.lock_fd))
            self.lock_identity = _identity(os.fstat(self.lock_fd))
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                _fail('JOURNAL_BUSY')
            self._boundary(allow_publication=True)
        except BaseException as error:
            self.close()
            if isinstance(error, (OSError, ValueError)):
                raise TransferJournalError('JOURNAL_UNAVAILABLE') from None
            raise

    def __enter__(self):
        return self

    @property
    def binding(self):
        return copy.deepcopy(self._binding)

    def __exit__(self, *_unused):
        self.close()

    def close(self):
        failed = False
        descriptors = ([self.lock_fd] if self.lock_fd is not None else []) + list(reversed(self.fds))
        self.lock_fd = self.directory_fd = None
        self.fds = []
        for descriptor in descriptors:
            try:
                os.close(descriptor)
            except OSError:
                failed = True
        if failed:
            _fail('CLOSE_RECONCILIATION_REQUIRED')

    def _directory(self):
        if self.directory_fd is None:
            _fail('JOURNAL_CLOSED')
        for parent, name, descriptor, witness in self.anchors:
            if (_identity(os.fstat(descriptor)) != witness
                    or _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != witness):
                _fail('DIRECTORY_BINDING_CHANGED')
        value = os.fstat(self.directory_fd)
        if value.st_uid != os.geteuid() or stat.S_IMODE(value.st_mode) != 0o700 or value.st_nlink < 2:
            _fail('DIRECTORY_POLICY_REFUSED')

    def _boundary(self, *, allow_publication=False):
        if self.poisoned and not allow_publication:
            _fail('PUBLICATION_RECONCILIATION_REQUIRED')
        self._directory()
        if self.lock_fd is None:
            _fail('JOURNAL_CLOSED')
        opened = os.fstat(self.lock_fd)
        bound = os.stat(LOCK, dir_fd=self.directory_fd, follow_symlinks=False)
        _private_file(opened)
        if _identity(opened) != self.lock_identity or _identity(bound) != self.lock_identity:
            _fail('LOCK_BINDING_CHANGED')
        entries = os.listdir(self.directory_fd)
        if len(entries) > 4096:
            _fail('PUBLICATION_RECONCILIATION_REQUIRED')
        if not allow_publication and (self.marker in entries or any(name.startswith(self.pending_prefix) for name in entries)):
            _fail('PUBLICATION_RECONCILIATION_REQUIRED')
        return entries

    def _validate(self, record):
        fields = {'schema', 'binding', 'credentials', 'material_digest', 'created_at', 'revision', 'effects'}
        if type(record) is not dict or set(record) != fields or type(record['schema']) is not int or record['schema'] != 1:
            _fail('RECORD_INVALID')
        if validate_binding(record['binding']) != self.binding:
            _fail('BINDING_DIFFERS')
        _credentials(record['credentials'])
        if record['material_digest'] != _material_digest(record['credentials']):
            _fail('MATERIAL_DIFFERS')
        if not _integer(record['created_at']) or not _integer(record['revision']) or type(record['effects']) is not list or len(record['effects']) > MAX_EFFECTS:
            _fail('RECORD_INVALID')
        names, pending = set(), False
        for effect in record['effects']:
            if type(effect) is not dict or set(effect) != {'name', 'state', 'metadata', 'receipt', 'material_digest'}:
                _fail('RECORD_INVALID')
            _step(effect['name'])
            if any(secret in effect['name'] for secret in record['credentials'].values()):
                _fail('CREDENTIAL_IN_OBSERVATION')
            if effect['name'] in names or pending or effect['state'] not in ('pending', 'verified'):
                _fail('RECORD_INVALID')
            names.add(effect['name'])
            if effect['material_digest'] != record['material_digest']:
                _fail('MATERIAL_DIFFERS')
            _observation(effect['metadata'], self.binding, record['credentials'])
            receipt = effect['receipt']
            if receipt is not None:
                _observation(receipt, self.binding, record['credentials'], receipt=True)
                if any(receipt[key] != effect['metadata'][key] for key in ('resource_kind', 'resource_id')):
                    _fail('RESOURCE_BINDING_DIFFERS')
            if effect['state'] == 'verified' and (receipt is None or receipt['outcome'] != 'verified'):
                _fail('RECORD_INVALID')
            if effect['state'] == 'pending' and receipt is not None and receipt['outcome'] != 'blocked':
                _fail('RECORD_INVALID')
            pending = effect['state'] == 'pending'
        return record

    def _read(self, *, missing=False):
        self._boundary()
        record, witness = self._read_leaf(self.name, missing=missing)
        self._boundary()
        return (self._validate(record), witness) if record is not None else (None, None)

    def _read_leaf(self, name, *, missing=False):
        self._boundary(allow_publication=True)
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=self.directory_fd)
        except FileNotFoundError:
            if missing:
                return None, None
            _fail('RESERVATION_MISSING')
        try:
            before = os.fstat(descriptor)
            _private_file(before)
            if not 1 <= before.st_size <= MAX_BYTES:
                _fail('RECORD_INVALID')
            data = os.pread(descriptor, MAX_BYTES + 1, 0)
            record = json.loads(data, object_pairs_hook=_pairs)
            after = os.fstat(descriptor)
            bound = os.stat(name, dir_fd=self.directory_fd, follow_symlinks=False)
            if _file_metadata(before) != _file_metadata(after) or _file_metadata(after) != _file_metadata(bound):
                _fail('FILE_BINDING_CHANGED')
            self._boundary(allow_publication=True)
            return record, _file_metadata(after)
        except (ValueError, UnicodeError, RecursionError):
            raise TransferJournalError('RECORD_INVALID') from None
        finally:
            os.close(descriptor)

    def _publish(self, record, previous):
        self._validate(record)
        self._boundary()
        data = _encode(record)
        if len(data) > MAX_BYTES:
            _fail('RECORD_TOO_LARGE')
        candidate = self.pending_prefix + secrets.token_hex(16)
        old = None
        if previous is not None:
            old, old_witness = self._read()
            if old_witness != previous:
                _fail('FILE_BINDING_CHANGED')
        self._transition(old, record)
        manifest = {'schema': 2, 'binding_digest': hashlib.sha256(_encode(self.binding)).hexdigest(),
                    'candidate': candidate, 'previous_record_digest': self._record_digest(old) if old is not None else None,
                    'previous_revision': old['revision'] if old is not None else None,
                    'previous_state': {key: copy.deepcopy(value) for key, value in old.items() if key != 'credentials'} if old is not None else None,
                    'proposed_record_digest': self._record_digest(record),
                    'proposed_revision': record['revision'], 'material_digest': record['material_digest']}
        marker_data = _encode(manifest)
        if len(marker_data) > MAX_BYTES:
            _fail('RECORD_TOO_LARGE')
        descriptors = []
        try:
            marker_fd = os.open(self.marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=self.directory_fd)
            descriptors.append(marker_fd)
            _private_file(os.fstat(marker_fd))
            self._write_all(marker_fd, marker_data)
            os.fsync(marker_fd)
            os.fsync(self.directory_fd)
            candidate_fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=self.directory_fd)
            descriptors.append(candidate_fd)
            _private_file(os.fstat(candidate_fd))
            self._write_all(candidate_fd, data)
            os.fsync(candidate_fd)
            os.fsync(self.directory_fd)
            self._directory()
            bound = os.stat(candidate, dir_fd=self.directory_fd, follow_symlinks=False)
            if _file_metadata(bound) != _file_metadata(os.fstat(candidate_fd)):
                _fail('FILE_BINDING_CHANGED')
            try:
                current = os.stat(self.name, dir_fd=self.directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                if previous is not None:
                    _fail('FILE_BINDING_CHANGED')
            else:
                if previous is None or _file_metadata(current) != previous:
                    _fail('FILE_BINDING_CHANGED')
            os.replace(candidate, self.name, src_dir_fd=self.directory_fd, dst_dir_fd=self.directory_fd)
            os.fsync(self.directory_fd)
            self._directory()
            published = os.stat(self.name, dir_fd=self.directory_fd, follow_symlinks=False)
            if _file_metadata(published) != _file_metadata(os.fstat(candidate_fd)):
                _fail('FILE_BINDING_CHANGED')
            marker_bound = os.stat(self.marker, dir_fd=self.directory_fd, follow_symlinks=False)
            if _file_metadata(marker_bound) != _file_metadata(os.fstat(marker_fd)):
                _fail('FILE_BINDING_CHANGED')
            os.unlink(self.marker, dir_fd=self.directory_fd)
            os.fsync(self.directory_fd)
            self._boundary()
            published = os.stat(self.name, dir_fd=self.directory_fd, follow_symlinks=False)
            if _file_metadata(published) != _file_metadata(os.fstat(candidate_fd)):
                _fail('FILE_BINDING_CHANGED')
        except BaseException:
            self.poisoned = True
            # Retain marker and candidate on uncertainty. Never remove secret material
            # that may be the only durable reservation for an interrupted operation.
            raise TransferJournalError('PUBLICATION_RECONCILIATION_REQUIRED') from None
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    @staticmethod
    def _write_all(descriptor, data):
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if type(written) is not int or not 1 <= written <= len(data) - offset:
                _fail('WRITE_REFUSED')
            offset += written

    @staticmethod
    def _record_digest(record):
        return hashlib.sha256(_encode(record)).hexdigest()

    def _transition(self, old, proposed):
        """Only the exact reservation, append-pending or receipt transition is publishable."""
        if old is None:
            if proposed['revision'] != 0 or proposed['effects']:
                _fail('PUBLICATION_TRANSITION_INVALID')
            return
        if any(old[key] != proposed[key] for key in ('binding', 'credentials', 'material_digest', 'created_at')) or proposed['revision'] != old['revision'] + 1:
            _fail('PUBLICATION_TRANSITION_INVALID')
        before, after = old['effects'], proposed['effects']
        if len(after) == len(before) + 1:
            valid = after[:-1] == before and after[-1]['state'] == 'pending' and after[-1]['receipt'] is None and not any(item['state'] == 'pending' for item in before)
        elif len(after) == len(before) and before:
            valid = (after[:-1] == before[:-1] and before[-1]['state'] == 'pending'
                     and after[-1]['receipt'] is not None
                     and all(before[-1][key] == after[-1][key] for key in ('name', 'metadata', 'material_digest')))
        else:
            valid = False
        if not valid:
            _fail('PUBLICATION_TRANSITION_INVALID')

    def _manifest(self, value):
        fields = {'schema', 'binding_digest', 'candidate', 'previous_record_digest', 'previous_revision', 'previous_state',
                  'proposed_record_digest', 'proposed_revision', 'material_digest'}
        if type(value) is not dict or set(value) != fields or type(value['schema']) is not int or value['schema'] != 2:
            _fail('PUBLICATION_MANIFEST_INVALID')
        if value['binding_digest'] != hashlib.sha256(_encode(self.binding)).hexdigest():
            _fail('BINDING_DIFFERS')
        candidate = value['candidate']
        if type(candidate) is not str or not candidate.startswith(self.pending_prefix) or re.fullmatch(r'[0-9a-f]{32}', candidate[len(self.pending_prefix):]) is None:
            _fail('PUBLICATION_MANIFEST_INVALID')
        if not all(_digest(value[key]) for key in ('binding_digest', 'proposed_record_digest', 'material_digest')) or not _integer(value['proposed_revision']):
            _fail('PUBLICATION_MANIFEST_INVALID')
        if value['previous_record_digest'] is None:
            if value['previous_revision'] is not None or value['previous_state'] is not None or value['proposed_revision'] != 0:
                _fail('PUBLICATION_MANIFEST_INVALID')
        elif not _digest(value['previous_record_digest']) or not _integer(value['previous_revision']) or value['proposed_revision'] != value['previous_revision'] + 1:
            _fail('PUBLICATION_MANIFEST_INVALID')
        elif type(value['previous_state']) is not dict or set(value['previous_state']) != {'schema', 'binding', 'material_digest', 'created_at', 'revision', 'effects'}:
            _fail('PUBLICATION_MANIFEST_INVALID')
        return value

    def _previous_generation(self, manifest, credentials):
        if manifest['previous_state'] is None:
            return None
        old = {**copy.deepcopy(manifest['previous_state']), 'credentials': copy.deepcopy(credentials)}
        self._validate(old)
        if self._record_digest(old) != manifest['previous_record_digest'] or old['revision'] != manifest['previous_revision']:
            _fail('PUBLICATION_PREVIOUS_DIFFERS')
        return old

    def _witness(self, name, expected):
        self._boundary(allow_publication=True)
        try:
            actual = os.stat(name, dir_fd=self.directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            if expected is None:
                return
            _fail('FILE_BINDING_CHANGED')
        if expected is None or _file_metadata(actual) != expected:
            _fail('FILE_BINDING_CHANGED')
        _private_file(actual)

    def _sync_leaf(self, name, expected):
        self._witness(name, expected)
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=self.directory_fd)
        try:
            if _file_metadata(os.fstat(descriptor)) != expected:
                _fail('FILE_BINDING_CHANGED')
            os.fsync(descriptor)
            self._witness(name, expected)
            if _file_metadata(os.fstat(descriptor)) != expected:
                _fail('FILE_BINDING_CHANGED')
        finally:
            os.close(descriptor)

    def _adopt_candidate(self, manifest, marker_witness, target_witness, candidate_witness):
        """Hold the exact candidate inode through sync and atomic rename."""
        name = manifest['candidate']
        self._witness(name, candidate_witness)
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=self.directory_fd)
        try:
            if _file_metadata(os.fstat(descriptor)) != candidate_witness:
                _fail('FILE_BINDING_CHANGED')
            os.fsync(descriptor)
            self._witness(name, candidate_witness)
            os.fsync(self.directory_fd)
            # The directory sync can yield to a substituting writer. Check both
            # the held inode and its name again, before using the rename source.
            self._witness(name, candidate_witness)
            if _file_metadata(os.fstat(descriptor)) != candidate_witness:
                _fail('FILE_BINDING_CHANGED')
            self._witness(self.name, target_witness)
            self._witness(self.marker, marker_witness)
            os.replace(name, self.name, src_dir_fd=self.directory_fd, dst_dir_fd=self.directory_fd)
            after = os.fstat(descriptor)
            _private_file(after)
            after_metadata = _file_metadata(after)
            # Rename may change ctime, but no other admitted candidate property.
            if after_metadata[:-1] != candidate_witness[:-1]:
                _fail('FILE_BINDING_CHANGED')
            self._witness(self.name, after_metadata)
            adopted, published_witness = self._read_leaf(self.name)
            if published_witness != after_metadata or _file_metadata(os.fstat(descriptor)) != after_metadata:
                _fail('FILE_BINDING_CHANGED')
            if self._record_digest(adopted) != manifest['proposed_record_digest']:
                _fail('PUBLICATION_RECORD_DIFFERS')
            return published_witness
        finally:
            os.close(descriptor)

    def _aborted_receipt(self, target, entries):
        """Replay bounded matching abort history after marker/response loss."""
        prefix = '.' + self.name + '.aborted-'
        names = sorted(name for name in entries if name.startswith(prefix))
        if len(names) > MAX_EFFECTS:
            _fail('PUBLICATION_EVIDENCE_AMBIGUOUS')
        fields = {'schema', 'outcome', 'binding_digest', 'manifest_digest', 'previous_record_digest',
                  'proposed_record_digest', 'proposed_revision', 'material_digest'}
        matches, witnesses, manifests = [], [], set()
        for name in names:
            if re.fullmatch(r'[0-9a-f]{32}\.json', name[len(prefix):]) is None:
                _fail('PUBLICATION_ABORT_RECEIPT_DIFFERS')
            receipt, witness = self._read_leaf(name)
            if (type(receipt) is not dict or set(receipt) != fields
                    or type(receipt['schema']) is not int or receipt['schema'] != 1
                    or receipt['outcome'] != 'publication_aborted'
                    or not all(_digest(receipt[key]) for key in fields - {'schema', 'outcome', 'proposed_revision'})
                    or not _integer(receipt['proposed_revision']) or receipt['proposed_revision'] < 1
                    or receipt['binding_digest'] != hashlib.sha256(_encode(self.binding)).hexdigest()
                    or receipt['material_digest'] != target['material_digest']):
                _fail('PUBLICATION_ABORT_RECEIPT_DIFFERS')
            serialized = _encode(receipt).decode('ascii')
            if any(secret in serialized for secret in target['credentials'].values()):
                _fail('CREDENTIAL_IN_OBSERVATION')
            if receipt['manifest_digest'] in manifests:
                _fail('PUBLICATION_ABORT_RECEIPT_DIFFERS')
            manifests.add(receipt['manifest_digest'])
            witnesses.append((name, witness))
            if receipt['previous_record_digest'] == self._record_digest(target):
                if receipt['proposed_revision'] != target['revision'] + 1:
                    _fail('PUBLICATION_ABORT_RECEIPT_DIFFERS')
                matches.append(receipt)
            elif receipt['proposed_revision'] > target['revision']:
                _fail('PUBLICATION_ABORT_RECEIPT_DIFFERS')
        return matches, witnesses

    def _publication_status(self, history):
        result = self.snapshot()
        if history:
            result['publications'] = copy.deepcopy(history)
            if len(history) == 1:
                result['publication'] = copy.deepcopy(history[0])
        return result

    def reconcile_publication(self, *, effect_started, expected_material_digest):
        """Complete only a schema2 uniquely proven filesystem publication.

        No native calls or receipt creation occur. Pending effects stay pending.
        Existing verified observations are preserved, never reverified here.
        An unknown initial digest is allowed only before effects for revision0.
        Public continuity is mandatory for every later generation. The result
        is credential-free and must be persisted publicly before any native use.
        """
        try:
            if type(effect_started) is not bool or expected_material_digest is not None and not _digest(expected_material_digest):
                _fail('PUBLIC_STATE_INVALID')
            if effect_started and expected_material_digest is None:
                _fail('PUBLIC_MATERIAL_REQUIRED')
            entries = self._boundary(allow_publication=True)
            candidates = [name for name in entries if name.startswith(self.pending_prefix)]
            manifest, marker_witness = self._read_leaf(self.marker, missing=True)
            target, target_witness = self._read_leaf(self.name, missing=True)
            if target is not None:
                self._validate(target)
            if manifest is None:
                if candidates:
                    _fail('PUBLICATION_EVIDENCE_AMBIGUOUS')
                if target is None:
                    _fail('RESERVATION_MISSING')
                self._public_continuity(target, effect_started, expected_material_digest)
                history, abort_witnesses = self._aborted_receipt(target, entries)
                if abort_witnesses and expected_material_digest is None:
                    _fail('PUBLIC_MATERIAL_REQUIRED')
                for name, witness in abort_witnesses:
                    self._sync_leaf(name, witness)
                self._sync_leaf(self.name, target_witness)
                os.fsync(self.directory_fd)
                self._witness(self.name, target_witness)
                for name, witness in abort_witnesses:
                    self._witness(name, witness)
                abort_prefix = '.' + self.name + '.aborted-'
                if {name for name in self._boundary(allow_publication=True) if name.startswith(abort_prefix)} != {name for name, _witness in abort_witnesses}:
                    _fail('PUBLICATION_EVIDENCE_AMBIGUOUS')
                self.poisoned = False
                self.trusted_material_digest = target['material_digest']
                return self._publication_status(history)
            manifest = self._manifest(manifest)
            candidate_name = manifest['candidate']
            if candidates not in ([], [candidate_name]):
                _fail('PUBLICATION_EVIDENCE_AMBIGUOUS')
            proposed, candidate_witness = self._read_leaf(candidate_name, missing=True)
            if proposed is not None:
                self._validate(proposed)
                if self._record_digest(proposed) != manifest['proposed_record_digest']:
                    _fail('PUBLICATION_RECORD_DIFFERS')
                if target is None:
                    if manifest['previous_record_digest'] is not None:
                        _fail('PUBLICATION_PREVIOUS_DIFFERS')
                elif (self._record_digest(target) != manifest['previous_record_digest']
                      or target['revision'] != manifest['previous_revision']):
                    _fail('PUBLICATION_PREVIOUS_DIFFERS')
                self._transition(target, proposed)
            else:
                if (target is not None and manifest['previous_record_digest'] is not None
                        and self._record_digest(target) == manifest['previous_record_digest']
                        and target['revision'] == manifest['previous_revision']
                        and target['material_digest'] == manifest['material_digest']):
                    if expected_material_digest is None:
                        _fail('PUBLIC_MATERIAL_REQUIRED')
                    self._public_continuity(target, effect_started, expected_material_digest)
                    return self._abort_publication(manifest, marker_witness, target, target_witness)
                if target is None or self._record_digest(target) != manifest['proposed_record_digest']:
                    _fail('PUBLICATION_GENERATION_MISSING')
                proposed = target
                # A vanished candidate is valid only after the intended atomic rename.
                self._witness(candidate_name, None)
            if proposed['revision'] != manifest['proposed_revision'] or proposed['material_digest'] != manifest['material_digest']:
                _fail('PUBLICATION_RECORD_DIFFERS')
            self._transition(self._previous_generation(manifest, proposed['credentials']), proposed)
            self._public_continuity(proposed, effect_started, expected_material_digest)
            self._witness(self.marker, marker_witness)
            if candidate_witness is not None:
                target_witness = self._adopt_candidate(manifest, marker_witness, target_witness, candidate_witness)
            self._sync_leaf(self.name, target_witness)
            os.fsync(self.directory_fd)
            self._witness(self.name, target_witness)
            self._witness(self.marker, marker_witness)
            # Unknown candidates created during reconciliation must remain blockers.
            current = self._boundary(allow_publication=True)
            if any(name.startswith(self.pending_prefix) for name in current):
                _fail('PUBLICATION_EVIDENCE_AMBIGUOUS')
            os.unlink(self.marker, dir_fd=self.directory_fd)
            os.fsync(self.directory_fd)
            self._witness(self.name, target_witness)
            self.poisoned = False
            self.trusted_material_digest = proposed['material_digest']
            return self.snapshot()
        except (OSError, ValueError, UnicodeError, RecursionError):
            self.poisoned = True
            raise TransferJournalError('PUBLICATION_RECONCILIATION_REQUIRED') from None
        except TransferJournalError:
            self.poisoned = True
            raise

    def _abort_publication(self, manifest, marker_witness, target, target_witness):
        """Persist that a proposed filesystem generation was not installed.

        This preserves every prior native uncertainty and receipt. It does not
        say a native effect failed, succeeded or is safe to repeat.
        """
        name = '.' + self.name + '.aborted-' + manifest['candidate'][-32:] + '.json'
        receipt = {'schema': 1, 'outcome': 'publication_aborted',
                   'binding_digest': manifest['binding_digest'],
                   'manifest_digest': self._record_digest(manifest),
                   'previous_record_digest': manifest['previous_record_digest'],
                   'proposed_record_digest': manifest['proposed_record_digest'],
                   'proposed_revision': manifest['proposed_revision'],
                   'material_digest': manifest['material_digest']}
        self._witness(self.marker, marker_witness)
        if self._previous_generation(manifest, target['credentials']) != target:
            _fail('PUBLICATION_PREVIOUS_DIFFERS')
        self._witness(manifest['candidate'], None)
        self._sync_leaf(self.name, target_witness)
        existing, witness = self._read_leaf(name, missing=True)
        if existing is None:
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=self.directory_fd)
            try:
                _private_file(os.fstat(descriptor))
                self._write_all(descriptor, _encode(receipt))
                os.fsync(descriptor)
                witness = _file_metadata(os.fstat(descriptor))
                self._witness(name, witness)
            finally:
                os.close(descriptor)
        elif existing != receipt:
            _fail('PUBLICATION_ABORT_RECEIPT_DIFFERS')
        self._sync_leaf(name, witness)
        os.fsync(self.directory_fd)
        self._witness(self.name, target_witness)
        self._witness(name, witness)
        self._witness(self.marker, marker_witness)
        if any(item.startswith(self.pending_prefix) for item in self._boundary(allow_publication=True)):
            _fail('PUBLICATION_EVIDENCE_AMBIGUOUS')
        os.unlink(self.marker, dir_fd=self.directory_fd)
        os.fsync(self.directory_fd)
        self._witness(self.name, target_witness)
        self.poisoned = False
        self.trusted_material_digest = target['material_digest']
        history, witnesses = self._aborted_receipt(target, self._boundary(allow_publication=True))
        for name, witness in witnesses:
            self._witness(name, witness)
        return self._publication_status(history)

    @staticmethod
    def _public_continuity(record, effect_started, expected):
        if expected is None:
            if effect_started or record['effects'] or record['revision'] != 0:
                _fail('PUBLIC_MATERIAL_REQUIRED')
        elif expected != record['material_digest']:
            _fail('MATERIAL_DIFFERS')

    def reserve(self, *, effect_started, expected_material_digest=None):
        """Return private material; caller must never log this result.

        effect_started comes from durable public operation state, not a guessed
        native outcome. The durable public material digest is mandatory once
        effects may have started; never derive that expectation from this file.
        A known reservation digest also forbids regeneration. Before/after calls
        require reserve to establish this held context's material continuity.
        """
        try:
            if type(effect_started) is not bool or expected_material_digest is not None and not _digest(expected_material_digest):
                _fail('PUBLIC_STATE_INVALID')
            if effect_started and expected_material_digest is None:
                _fail('PUBLIC_MATERIAL_REQUIRED')
            record, _previous = self._read(missing=True)
            if record is None:
                if effect_started or expected_material_digest is not None:
                    _fail('POST_EFFECT_MATERIAL_MISSING')
                credentials = {name: secrets.token_hex(size) for name, size in CREDENTIAL_BYTES.items()}
                credentials['developer'] = secrets.token_urlsafe(24)
                record = {'schema': 1, 'binding': copy.deepcopy(self.binding), 'credentials': credentials,
                          'material_digest': _material_digest(credentials), 'created_at': time.time_ns() // 1000000,
                          'revision': 0, 'effects': []}
                self._publish(record, None)
            if record['effects'] and expected_material_digest is None:
                _fail('PUBLIC_MATERIAL_REQUIRED')
            if expected_material_digest is not None and expected_material_digest != record['material_digest']:
                _fail('MATERIAL_DIFFERS')
            self.trusted_material_digest = record['material_digest']
            return copy.deepcopy(record['credentials'])
        except (OSError, ValueError):
            raise TransferJournalError('RESERVATION_UNAVAILABLE') from None

    def before_effect(self, name, metadata):
        """Persist uncertainty before native action. Existing pending state stays pending."""
        try:
            _step(name)
            record, previous = self._read()
            self._material_continuity(record)
            if any(secret in name for secret in record['credentials'].values()):
                _fail('CREDENTIAL_IN_OBSERVATION')
            metadata = _observation(metadata, self.binding, record['credentials'])
            existing = next((effect for effect in record['effects'] if effect['name'] == name), None)
            if existing is not None:
                if existing['metadata'] != metadata:
                    _fail('EFFECT_BINDING_DIFFERS')
                return copy.deepcopy(existing)
            if any(effect['state'] == 'pending' for effect in record['effects']):
                _fail('NATIVE_EFFECT_PENDING')
            if len(record['effects']) >= MAX_EFFECTS:
                _fail('EFFECT_LIMIT')
            effect = {'name': name, 'state': 'pending', 'metadata': metadata, 'receipt': None,
                      'material_digest': record['material_digest']}
            record['effects'].append(effect)
            record['revision'] += 1
            self._publish(record, previous)
            return copy.deepcopy(effect)
        except (OSError, ValueError):
            raise TransferJournalError('EFFECT_UNAVAILABLE') from None

    def after_effect(self, name, receipt):
        """Settle only a matching pending effect with its verified native observation.

        A blocked observation remains pending. Verification means the caller has
        reconciled the original native effect, not merely received an HTTP success.
        """
        try:
            _step(name)
            record, previous = self._read()
            self._material_continuity(record)
            receipt = _observation(receipt, self.binding, record['credentials'], receipt=True)
            effect = next((effect for effect in record['effects'] if effect['name'] == name), None)
            if effect is None:
                _fail('EFFECT_NOT_REQUESTED')
            if any(receipt[key] != effect['metadata'][key] for key in ('resource_kind', 'resource_id')):
                _fail('EFFECT_BINDING_DIFFERS')
            if effect['state'] == 'verified':
                if effect['receipt'] != receipt:
                    _fail('SETTLED_RECEIPT_DIFFERS')
                return copy.deepcopy(effect)
            effect['receipt'] = receipt
            if receipt['outcome'] == 'verified':
                effect['state'] = 'verified'
            record['revision'] += 1
            self._publish(record, previous)
            return copy.deepcopy(effect)
        except (OSError, ValueError):
            raise TransferJournalError('EFFECT_UNAVAILABLE') from None

    def snapshot(self):
        """Bounded public metadata only. No secret values or private paths."""
        try:
            record, _previous = self._read()
            return {key: copy.deepcopy(record[key]) for key in
                    ('schema', 'binding', 'material_digest', 'created_at', 'revision', 'effects')}
        except (OSError, ValueError):
            raise TransferJournalError('JOURNAL_UNAVAILABLE') from None

    def _material_continuity(self, record):
        if self.trusted_material_digest is None:
            _fail('PUBLIC_MATERIAL_REQUIRED')
        if self.trusted_material_digest != record['material_digest']:
            _fail('MATERIAL_DIFFERS')
