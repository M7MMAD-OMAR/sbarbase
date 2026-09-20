"""Bounded encrypted recovery envelope for local rehearsal, not streaming backup."""
import base64
import json
import secrets
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
