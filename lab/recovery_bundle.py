"""Bounded encrypted recovery envelope for local rehearsal, not streaming backup."""
import base64
import json
import secrets
import sqlite3
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAX_PAYLOAD = 32 * 1024 * 1024
AAD = b'sbarbase-recovery-v2'


def seal(payload, key):
    raw = json.dumps(payload, separators=(',', ':')).encode()
    if len(raw) > MAX_PAYLOAD or len(key) != 32:
        raise ValueError('Invalid recovery payload size or key')
    nonce = secrets.token_bytes(12)
    return {'version': 2, 'nonce': base64.b64encode(nonce).decode(),
            'ciphertext': base64.b64encode(AESGCM(key).encrypt(nonce, raw, AAD)).decode()}


def open_bundle(envelope, key):
    if set(envelope) != {'version', 'nonce', 'ciphertext'} or envelope['version'] != 2 or len(key) != 32:
        raise ValueError('Invalid recovery envelope')
    if not isinstance(envelope['ciphertext'], str) or len(envelope['ciphertext']) > (MAX_PAYLOAD + 16) * 4 // 3 + 4:
        raise ValueError('Recovery envelope too large')
    nonce = base64.b64decode(envelope['nonce'], validate=True)
    ciphertext = base64.b64decode(envelope['ciphertext'], validate=True)
    if len(nonce) != 12:
        raise ValueError('Invalid recovery nonce')
    return json.loads(AESGCM(key).decrypt(nonce, ciphertext, AAD))


def catalog_ownership(catalog, runtime):
    """Which organization, project and environment own a runtime, read from the control catalog.

    Recorded in an export so a restore on another installation can re-link the environment to
    its project instead of arriving as a bare runtime. Read-only. None when the catalog is
    absent or does not know the runtime; the export still proceeds, and says so.
    """
    path = Path(catalog)
    if not path.is_file():
        return None
    with sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True) as database:
        row = database.execute(
            'SELECT o.id, o.name, p.id, p.name, e.id, e.name FROM provision_jobs j '
            'JOIN environments e ON e.id=j.environment JOIN projects p ON p.id=e.project '
            'JOIN organizations o ON o.id=p.organization WHERE j.runtime=?', (runtime,)).fetchone()
    if row is None:
        return None
    return {'organization': {'id': row[0], 'name': row[1]}, 'project': {'id': row[2], 'name': row[3]},
            'environment': {'id': row[4], 'name': row[5]}}
