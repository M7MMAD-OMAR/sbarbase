"""Prepare the fixed runtime private namespace without following or repairing paths."""
import os
from pathlib import Path
import stat


class PrivateDirectoryRefusal(RuntimeError):
    """Directory preparation exposes only a fixed public refusal."""


def _identity(info):
    return info.st_dev, info.st_ino


def _current(edges):
    for parent, name, child, private in edges:
        observed = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(observed.st_mode) or _identity(observed) != _identity(os.fstat(child)):
            raise PrivateDirectoryRefusal('Runtime private directory identity changed')
        if private and (observed.st_uid != os.geteuid() or stat.S_IMODE(observed.st_mode) != 0o700):
            raise PrivateDirectoryRefusal('Runtime private directory ownership or mode refused')


def prepare(root):
    """Create or validate .secrets/upstream beneath an existing anchored checkout."""
    root = Path(root)
    if not root.is_absolute() or root == Path('/') or any(part in ('.', '..') for part in root.parts):
        raise PrivateDirectoryRefusal('Runtime private checkout path refused')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptors, edges = [], []
    try:
        parent = os.open('/', flags)
        descriptors.append(parent)
        for name in root.parts[1:]:
            child = os.open(name, flags, dir_fd=parent)
            descriptors.append(child)
            edges.append((parent, name, child, False))
            parent = child
        _current(edges)
        for name in ('.secrets', 'upstream'):
            _current(edges)
            try:
                os.mkdir(name, mode=0o700, dir_fd=parent)
            except FileExistsError:
                pass
            child = os.open(name, flags, dir_fd=parent)
            descriptors.append(child)
            info = os.fstat(child)
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != 0o700):
                raise PrivateDirectoryRefusal('Runtime private directory ownership or mode refused')
            edges.append((parent, name, child, True))
            _current(edges)
            parent = child
        return root / '.secrets' / 'upstream'
    except OSError:
        raise PrivateDirectoryRefusal('Runtime private directory unavailable') from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
