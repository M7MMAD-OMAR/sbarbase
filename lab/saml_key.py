"""The SAML signing key of one environment's Auth.

The pinned Auth refuses to start with SAML on and no key (lab/auth-names-check.py: "SAML private key not in PKCS#1
format"). The console makes the key when SAML is first turned on for an environment, in

    .secrets/upstream/<environment runtime id>-saml.json

mode 0600, as {"version": 1, "private_key": <base64 of the PKCS#1 DER key>}. It is kept when SAML is turned off
and on again, because identity providers hold the matching certificate and a new key would break every trust
already set up. It never leaves this file and the Auth container's environment, and the console never returns
it. This module only reads it.
"""
import base64
import binascii
import json
import os
import re
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / '.secrets' / 'upstream'
RUNTIME = re.compile(r'e_[a-f0-9]{24}')
MAX_BYTES = 16384


class SamlKeyError(RuntimeError):
    """Names the field that failed, never a value."""


def path(e, directory=None):
    if not RUNTIME.fullmatch(e):
        raise SamlKeyError('runtime')
    return Path(directory or DIRECTORY) / f'{e}-saml.json'


def load(e, directory=None):
    """The base64 key, or None when the environment has none. A file that exists and is not valid raises."""
    file = path(e, directory)
    if not os.path.lexists(file):
        return None
    status = file.lstat()
    if not stat.S_ISREG(status.st_mode) or status.st_mode & 0o077 or status.st_size > MAX_BYTES:
        raise SamlKeyError('file')
    try:
        document = json.loads(file.read_text())
        key = document['private_key']
        if document.get('version') != 1 or not isinstance(key, str) or len(base64.b64decode(key, validate=True)) < 600:
            raise SamlKeyError('private_key')
    except (ValueError, KeyError, TypeError, binascii.Error) as error:
        raise SamlKeyError('file') from error
    return key
