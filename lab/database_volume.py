"""Which Docker volume holds the installation's database (upstream currency P3, part 1).

The runtime named it as a constant, ``sbarbase-durable-pgdata``. A rebuild of the cluster
(docs/engineering/WHOLE-CLUSTER-RESTORE.md) lands on a new volume, because retained volumes are
never removed, and a generation migration (lab/hba_migration.py, a ``new_volume`` intent) then
moves the database container onto it. This private record names the volume the runtime mounts:
absent, it is the constant, so an installation that never moved changes nothing; present, it must
be readable, private and well formed, or the runtime refuses to start rather than guess.

The record changes only through publish(), which the generation migration calls once the
replacement on the new volume is in place and before its own record is removed.
"""
import json
import os
import re
import stat
from pathlib import Path

import run as lab

NAME = 'database-volume.json'
DEFAULT = 'sbarbase-durable-pgdata'
# The constant, or a rebuilt cluster's volume: the runtime mounts only volumes it owns
# (io.sbarbase.owner=durable-upstream), so a rebuild creates its volume under this name and owner.
VOLUME = re.compile(r'sbarbase-durable-pgdata(-[a-f0-9]{12})?')
LIMIT = 4096


class VolumeError(RuntimeError):
    pass


def valid(volume):
    return isinstance(volume, str) and VOLUME.fullmatch(volume) is not None


def rebuilt(volume):
    """A rebuilt cluster's volume, the only kind a move goes onto: never the constant, which after a
    move holds the kept, outdated original."""
    return valid(volume) and volume != DEFAULT


def current(state):
    """The volume the database container must mount."""
    path = Path(state) / NAME
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return DEFAULT
    except OSError:
        raise VolumeError('The database volume record cannot be read; it needs review') from None
    try:
        with os.fdopen(descriptor, 'r', encoding='utf-8') as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() \
                    or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_size > LIMIT:
                raise VolumeError('The database volume record must be a small private file of this account')
            value = json.loads(source.read(LIMIT + 1))
    except (OSError, ValueError):
        raise VolumeError('The database volume record cannot be read; it needs review') from None
    if not isinstance(value, dict) or set(value) != {'version', 'volume'} or type(value['version']) is not int \
            or value['version'] != 1 \
            or not valid(value['volume']):
        raise VolumeError('The database volume record is malformed; it needs review')
    return value['volume']


def publish(state, volume):
    """Name ``volume`` as the database volume, whole: written under a temporary name, synced and
    renamed. Publishing the volume already named changes nothing."""
    if not valid(volume):
        raise VolumeError('Not a database volume name')
    state = Path(state)
    if current(state) == volume and (state / NAME).exists():
        return volume
    lab.atomic(state / NAME, {'version': 1, 'volume': volume})
    if current(state) != volume:
        raise VolumeError('The database volume record changed before use')
    return volume
