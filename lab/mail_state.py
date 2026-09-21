"""Non secret mail state, one entry per environment, for the platform console.

The runtime owns this file; the control plane copies the non secret fields into
the catalog. Nothing here authenticates, so nothing here is a credential: the
password lives only in the environment's 0600 mail configuration file, which is
the only place it is written and the only place it is read from.
"""
import json
import os
import time
from pathlib import Path

import mail_config

STATES = mail_config.STATES
FILE = 'mail-state.json'
STATE = Path(__file__).resolve().parents[1] / '.lab' / 'upstream'


def _atomic(path, value):
    """Replace the file in one step, the way the runtime does everywhere else."""
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w') as handle:
        json.dump(value, handle, sort_keys=True, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def summary(state, mail, at=None):
    """The recorded entry: the state, the non secret view, and when it changed."""
    entry = {'state': state, 'credentials': 'none' if mail is None else 'set',
             'at': int(time.time() if at is None else at)}
    if mail is not None:
        entry.update(mail_config.summarize(mail))
    return entry


def record(runtime_id, state, mail, directory=None, at=None):
    """Record one environment's mail state and return the entry written."""
    if state not in STATES:
        raise ValueError('Unknown mail state: ' + state)
    if not runtime_id:
        raise ValueError('A mail state entry needs an environment runtime identifier')
    path = (Path(directory) if directory else STATE) / FILE
    current = json.loads(path.read_text()) if path.exists() else {}
    entry = summary(state, mail, at)
    current[runtime_id] = entry
    _atomic(path, current)
    return entry


def read(directory=None):
    """Every recorded entry, or an empty mapping when nothing was recorded yet."""
    path = (Path(directory) if directory else STATE) / FILE
    return json.loads(path.read_text()) if path.exists() else {}