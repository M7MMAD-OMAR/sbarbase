"""Conservative Linux host admission snapshot, not a capacity benchmark."""
from dataclasses import dataclass
from pathlib import Path
import json
import socket
import subprocess

MIB = 1024**2
GIB = 1024**3
MEMORY_RESERVE = 2*GIB
NEW_ENVIRONMENT_MEMORY = 512*MIB
DISK_RESERVE = 5*GIB
NEW_ENVIRONMENT_DISK = GIB
MIN_FREE_INODES = 10000


@dataclass(frozen=True)
class Snapshot:
    available_memory: int
    database_free_bytes: int
    objects_free_bytes: int
    database_free_inodes: int | None
    objects_free_inodes: int | None


def refusal(snapshot):
    values = (snapshot.available_memory, snapshot.database_free_bytes, snapshot.objects_free_bytes)
    inode_values = (snapshot.database_free_inodes, snapshot.objects_free_inodes)
    values += tuple(value for value in inode_values if value is not None)
    if any(type(value) is not int or value < 0 for value in values):
        return 'measurement_unavailable'
    if snapshot.available_memory < MEMORY_RESERVE+NEW_ENVIRONMENT_MEMORY:
        return 'memory_headroom'
    if min(snapshot.database_free_bytes, snapshot.objects_free_bytes) < DISK_RESERVE+NEW_ENVIRONMENT_DISK:
        return 'disk_headroom'
    if any(value is not None and value < MIN_FREE_INODES for value in inode_values):
        return 'inode_headroom'
    return None


def docker(*args):
    result = subprocess.run(['docker', *args], text=True, capture_output=True, timeout=10)
    if result.returncode:
        raise RuntimeError('Resource measurement unavailable')
    return result.stdout


def disk_free(container, path, inodes=False):
    # Inspect the filesystem actually backing each mounted runtime volume.
    output = docker('exec', container, 'df', '-Pi' if inodes else '-Pk', path)
    fields = output.splitlines()[-1].split()
    value = int(fields[3])
    if inodes and int(fields[1]) == 0 and value == 0:
        # Btrfs has no fixed inode pool; do not reinterpret this as exhaustion.
        magic = docker('exec', container, 'stat', '-f', '-c', '%t', path).strip().lower()
        if magic == '9123683e':
            return None
    return value if inodes else value*1024


def snapshot():
    info = json.loads(docker('info', '--format', '{{json .}}'))
    if info.get('Name') != socket.gethostname() or info.get('OSType') != 'linux':
        raise RuntimeError('Resource admission requires the native local Linux daemon')
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    fields = memory['MemAvailable'].split()
    if len(fields) != 2 or fields[1] != 'kB':
        raise RuntimeError('Memory measurement unavailable')
    return Snapshot(int(fields[0])*1024,
        disk_free('sbarbase-durable-db', '/var/lib/postgresql/data'),
        disk_free('sbarbase-durable-storage', '/tmp/storage-data'),
        disk_free('sbarbase-durable-db', '/var/lib/postgresql/data', True),
        disk_free('sbarbase-durable-storage', '/tmp/storage-data', True))


if __name__ == '__main__':
    try:
        current = snapshot()
        print(json.dumps({'snapshot': vars(current), 'refusal': refusal(current)}))
    except Exception:
        raise SystemExit('Resource measurement unavailable; no admission decision can be made.')
