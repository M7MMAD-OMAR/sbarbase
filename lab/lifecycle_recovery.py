#!/usr/bin/env python3
"""Private lifecycle receipt boundary for the canonical SB-05 recovery admission API."""
import argparse
from contextlib import closing
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
from uuid import uuid4

MAX_RECEIPT_BYTES = 4 * 1024 * 1024


def publish_exact(parent, temporary, name):
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise ValueError('Atomic exclusive receipt publication unavailable')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(temporary), parent, os.fsencode(name), 1) != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))


def parent_fd(value):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or not path.name:
        raise ValueError('Explicit safe absolute receipt path required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd, path.name
    except BaseException:
        os.close(fd)
        raise


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate receipt field')
        result[key] = value
    return result


def private_receipt(value):
    parent, name = parent_fd(value)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    finally:
        os.close(parent)
    with os.fdopen(fd, 'rb') as handle:
        before = os.fstat(handle.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or before.st_mode & 0o077 or before.st_nlink != 1
                or not 0 < before.st_size <= MAX_RECEIPT_BYTES):
            raise ValueError('Private bounded receipt ownership required')
        raw = handle.read(MAX_RECEIPT_BYTES + 1)
        after = os.fstat(handle.fileno())
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_uid', 'st_mode', 'st_nlink')
        if len(raw) != before.st_size or any(getattr(before, field) != getattr(after, field) for field in fields):
            raise ValueError('Receipt changed while reading')
    value = json.loads(raw, object_pairs_hook=unique_object)
    if type(value) is not dict:
        raise ValueError('Receipt object required')
    return value


def shared_api(checkout):
    root = Path(checkout)
    if not root.is_absolute() or any(part.is_symlink() for part in [root, *root.parents]):
        raise ValueError('Explicit trusted checkout required')
    for name in ('recovery_inventory', 'recovery_receipt'):
        path = root / 'lab' / (name + '.py')
        if not path.is_file() or any(part.is_symlink() for part in [path, *path.parents]):
            raise ValueError('Canonical SB-05 recovery admission API unavailable')
        previous = sys.modules.get(name)
        if previous is not None:
            if Path(previous.__file__) != path:
                raise ValueError('Recovery API checkout identity differs')
            continue
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return sys.modules['recovery_receipt']


def verify(checkout, receipt, binding, source_inventory):
    if binding.get('coverage') not in ('dedicated-resources', 'complete-shared-resources'):
        raise ValueError('Production admission refuses fixture evidence')
    module = shared_api(checkout)
    return module.verify_purge_receipt(receipt, binding, module.RegistryAcceptance(Path(checkout)), source_inventory)


def admission(checkout, receipt_path, binding, source_inventory):
    return verify(checkout, private_receipt(receipt_path), binding, source_inventory)


def catalog_binding(catalog_path, environment):
    catalog = Path(catalog_path)
    if not catalog.is_absolute() or any(part.is_symlink() for part in [catalog, *catalog.parents]):
        raise ValueError('Explicit catalog identity required')
    with closing(sqlite3.connect('file:' + str(catalog) + '?mode=ro', uri=True)) as db:
        row = db.execute('SELECT runtime,epoch,inventory,coverage,state FROM environment_lifecycle WHERE environment=?', (environment,)).fetchone()
        if row is None or row[4] != 'deleted' or not row[2]:
            raise ValueError('Retained environment inventory required')
        route = db.execute('SELECT revision,maintenance,placement FROM runtime_routing WHERE runtime=?', (row[0],)).fetchone()
    routing = {'revision': route[0] if route else 0, 'maintenance': bool(route[1]) if route else False,
               'placement': json.loads(route[2]) if route and route[2] else None}
    placement = 'native-dedicated' if routing['placement'] and routing['placement'].get('profile') == 'native-dedicated' else 'legacy-shared'
    # Catalog identities retain the exact existing JSON.stringify property ordering.
    identity = lambda value: hashlib.sha256(json.dumps(value, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    resources = json.loads(row[2])
    binding = {'environment': environment, 'runtime': row[0], 'epoch': row[1], 'coverage': row[3], 'placement': placement,
               'inventoryDigest': identity(resources), 'placementDigest': identity(routing)}
    return binding, resources


def prepare(checkout, catalog_path, environment, receipt_source, receipt_path):
    binding, resources = catalog_binding(catalog_path, environment)
    receipt = private_receipt(receipt_source)
    digest = verify(checkout, receipt, binding, resources)
    parent, name = parent_fd(receipt_path)
    temporary = '.lifecycle-receipt-' + str(uuid4()) + '.pending'
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(receipt, handle, separators=(',', ':'), ensure_ascii=True)
            handle.flush()
            os.fsync(handle.fileno())
        refreshed_binding, refreshed_resources = catalog_binding(catalog_path, environment)
        if refreshed_binding != binding or refreshed_resources != resources or verify(checkout, receipt, binding, resources) != digest:
            raise ValueError('Recovery admission changed during publication')
        # Linux RENAME_NOREPLACE publishes complete bytes without overwriting a known receipt.
        publish_exact(parent, temporary, name)
        os.fsync(parent)
        return digest
    finally:
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', required=True)
    parser.add_argument('--receipt', required=True)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--catalog')
    parser.add_argument('--environment')
    parser.add_argument('--receipt-source')
    args = parser.parse_args()
    try:
        if args.prepare:
            if not args.catalog or not args.environment or not args.receipt_source:
                raise ValueError('Exact Catalog and canonical SB-05 receipt source required')
            result = prepare(args.checkout, args.catalog, args.environment, args.receipt_source, args.receipt)
        else:
            raw = sys.stdin.buffer.read(MAX_RECEIPT_BYTES + 1)
            if len(raw) > MAX_RECEIPT_BYTES:
                raise ValueError('Admission request exceeds bound')
            request = json.loads(raw, object_pairs_hook=unique_object)
            if type(request) is not dict or set(request) != {'binding', 'sourceInventory'}:
                raise ValueError('Exact current Catalog admission snapshot required')
            result = admission(args.checkout, args.receipt, request['binding'], request['sourceInventory'])
        if not isinstance(result, str) or len(result) != 64 or any(c not in '0123456789abcdef' for c in result):
            raise ValueError('Canonical verifier receipt identity unavailable')
        print(json.dumps({'receipt': result}))
        return 0
    except Exception:
        print(json.dumps({'receipt': None}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
