"""Prepare exact public application bytes on a fresh disposable Ubuntu guest.

This fixture requires the actor's exact published code and an original public
bundle digest. It prepares Docker, pinned images and the application build. It
does not create operator credentials or assert installation, restore or
production acceptance. It never reads a workstation cache or uses guest APT.
"""
import argparse
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import select
import signal
import stat
import subprocess
import sys
import tarfile
import time
import uuid
import zipfile

MAX_BYTES = 8 << 30
CHUNK = 65536
MAX_LOG_BYTES = 1 << 20
RETAINED_COMMANDS = []
BUN_SHA = 'a8f9ebd1770ddc8e55dab7a68d4ec1ec1eebf374bb97cc65cf2c3cb373fc6791'
SOURCE_COMMIT = '1c4449ff4c530029096453d32fc12c2d73de93f1'


def require(value, message):
    if not value:
        raise RuntimeError(message)


def canonical(name):
    require(type(name) is str and name and not name.startswith('/')
            and len(name.encode()) <= 255 and not any(c in name for c in ('\x00', '\n', '\r'))
            and all(p not in ('', '.', '..') for p in name.split('/')), 'Public member path refused')
    return name


def digest(value):
    require(type(value) is str and len(value) == 64
            and all(c in '0123456789abcdef' for c in value), 'Exact SHA256 required')
    return value


def tick(deadline):
    require(time.monotonic() < deadline, 'Original guest preparation deadline exhausted')


def write_member(incoming, destination, size, expected, deadline):
    require(type(size) is int and 0 <= size <= MAX_BYTES, 'Finite member size required')
    tick(deadline)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tick(deadline)
    observed = hashlib.sha256()
    count = 0
    tick(deadline)
    with destination.open('xb') as output:
        tick(deadline)
        while count < size:
            tick(deadline)
            block = incoming.read(min(CHUNK, size - count))
            tick(deadline)
            require(bool(block), 'Original public member truncated')
            observed.update(block)
            require(output.write(block) == len(block), 'Original public output write incomplete')
            tick(deadline)
            count += len(block)
    tick(deadline)
    require(observed.hexdigest() == digest(expected), 'Original member SHA256 differs')


def unpack_bundle(path, output, source_sha, deadline):
    with tarfile.open(path, 'r:') as archive:
        first = archive.next()
        require(first is not None and first.name == 'manifest.json' and first.isfile()
                and 0 < first.size <= 1 << 20, 'Exact first public manifest required')
        document = json.loads(archive.extractfile(first).read())
        require(set(document) == {'schema', 'source_sha256', 'members', 'scope'}
                and document['schema'] == 1 and document['source_sha256'] == source_sha
                and document['scope'] == 'public byte aggregation only', 'Public manifest differs')
        members = document['members']
        require(type(members) is dict and 1 <= len(members) <= 20000, 'Bounded public member set required')
        seen = set()
        for member in archive:
            tick(deadline)
            name = canonical(member.name)
            require(name in members and name not in seen and member.isfile()
                    and not member.linkname, 'Duplicate, special or unknown public member')
            row = members[name]
            require(set(row) == {'bytes', 'sha256'} and row['bytes'] == member.size,
                    'Public member record differs')
            write_member(archive.extractfile(member), output / name,
                         member.size, row['sha256'], deadline)
            seen.add(name)
        require(seen == set(members), 'Incomplete public bundle')
    tick(deadline)
    return document


def unpack_source(public, output, deadline):
    manifest = json.loads((public / 'source-manifest.json').read_bytes())
    require(manifest['commit'] == SOURCE_COMMIT and len(manifest['members']) == 1690,
            'Exact published application source required')
    seen = set()
    prefix = 'sbarbase-' + SOURCE_COMMIT + '/'
    with tarfile.open(public / 'source.tar.gz', 'r:gz') as archive:
        for member in archive:
            tick(deadline)
            if member.isdir():
                require(member.name.startswith(prefix.rstrip('/')), 'Source directory prefix differs')
                continue
            require(member.name.startswith(prefix), 'Source archive prefix differs')
            name = canonical(member.name[len(prefix):])
            require(member.isfile() and name not in seen and name in manifest['members'],
                    'Unknown or duplicate published source file')
            row = manifest['members'][name]
            require(row['bytes'] == member.size and row['mode'] in ('100644', '100755'),
                    'Published source shape differs')
            target = output / name
            write_member(archive.extractfile(member), target, member.size, row['sha256'], deadline)
            os.chmod(target, 0o755 if row['mode'] == '100755' else 0o644)
            tick(deadline)
            seen.add(name)
    require(seen == set(manifest['members']), 'Published source incomplete')
    tick(deadline)
    return manifest


def unpack_cache(public, output, deadline):
    manifest = json.loads((public / 'cache-manifest.json').read_bytes())
    require(type(manifest) is dict and len(manifest) <= 20000, 'Finite portable public cache required')
    seen = set()
    pending = []
    with tarfile.open(public / 'cache.tar', 'r:') as archive:
        for member in archive:
            tick(deadline)
            name = canonical(member.name)
            require(name in manifest and name not in seen, 'Unknown or duplicate public cache member')
            row = manifest[name]
            target = output / name
            if row['kind'] == 'directory':
                require(member.isdir(), 'Public cache directory differs')
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
            elif row['kind'] == 'file':
                require(member.isfile() and member.size == row['bytes'], 'Public cache file differs')
                write_member(archive.extractfile(member), target, member.size, row['sha256'], deadline)
                require(type(row['mode']) is int and row['mode'] & ~0o777 == 0,
                        'Public cache file mode refused')
                os.chmod(target, row['mode'])
            elif row['kind'] == 'symlink':
                require(member.issym() and member.linkname == row['target']
                        and not member.linkname.startswith('/'), 'Portable cache symlink differs')
                resolved = (target.parent / member.linkname).resolve(strict=False)
                require(resolved.is_relative_to(output.resolve()), 'Portable cache symlink escapes')
                pending.append((target, member.linkname, resolved))
            else:
                raise RuntimeError('Public cache special file refused')
            seen.add(name)
    require(seen == set(manifest), 'Portable public cache incomplete')
    for target, link, resolved in pending:
        require(resolved.is_dir() and not resolved.is_symlink(), 'Portable cache target differs')
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.symlink(link, target)
        tick(deadline)
    tick(deadline)


class OriginalLog:
    def __init__(self):
        self.fd = self.anchor = None
        self.identity = None
        self.state = self.anchor_state = 'absent'

    def acquire(self, path, deadline):
        tick(deadline)
        self.fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        self.state = 'acquired'
        tick(deadline)
        self.anchor = os.dup(self.fd)
        self.anchor_state = 'acquired'
        tick(deadline)
        value = os.fstat(self.fd)
        self.identity = (value.st_dev, value.st_ino, value.st_mode, value.st_uid)
        self.state = self.anchor_state = 'owned'
        self.current(deadline)

    def current(self, deadline):
        tick(deadline)
        require(self.state == self.anchor_state == 'owned', 'Original raw log pair required')
        for fd in (self.fd, self.anchor):
            value = os.fstat(fd)
            require((value.st_dev, value.st_ino, value.st_mode, value.st_uid) == self.identity
                    and fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_WRONLY,
                    'Original raw log descriptor slot changed')
            tick(deadline)
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        require(libc.syscall(312, os.getpid(), os.getpid(), 0, self.fd, self.anchor) == 0,
                'Original raw log open file description changed')
        tick(deadline)

    @property
    def closed(self):
        return self.state == self.anchor_state == 'released'

    def close(self, deadline):
        if self.closed:
            return
        self.current(deadline)
        self.state = 'uncertain'
        os.close(self.fd)
        self.state = 'released'
        tick(deadline)
        value = os.fstat(self.anchor)
        require((value.st_dev, value.st_ino, value.st_mode, value.st_uid) == self.identity,
                'Original raw log anchor changed before retirement')
        self.anchor_state = 'uncertain'
        os.close(self.anchor)
        self.anchor_state = 'released'
        tick(deadline)


def child_limits():
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_LOG_BYTES, MAX_LOG_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    signal.signal(signal.SIGXFSZ, signal.SIG_DFL)


def wait_original_group_terminal(leader, deadline):
    # Keep the original direct leader unreaped while observing its numeric group.
    # Its retained identity prevents reuse of this group number during the scan.
    while True:
        tick(deadline)
        live = False
        count = 0
        with os.scandir('/proc') as entries:
            for entry in entries:
                tick(deadline)
                if not entry.name.isdigit():
                    continue
                count += 1
                require(count <= 65536, 'Finite original process group scan required')
                try:
                    with open('/proc/' + entry.name + '/stat', 'rb') as stream:
                        raw = stream.read(8193)
                    tick(deadline)
                except FileNotFoundError:
                    continue
                require(len(raw) <= 8192, 'Finite original process stat required')
                fields = raw.rsplit(b') ', 1)[1].split()
                require(len(fields) >= 20, 'Original process group stat shape differs')
                if int(fields[2]) == leader and int(entry.name) != leader:
                    live = live or fields[0] not in (b'Z', b'X')
        tick(deadline)
        status = os.waitid(os.P_PID, leader, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        tick(deadline)
        if not live and status is not None:
            require(status.si_pid == leader, 'Original unreaped group leader changed')
            return
        time.sleep(min(0.01, max(0.0, deadline-time.monotonic())))
        tick(deadline)


def command(args, cwd, env, deadline, label, area, *, cleanup_deadline):
    tick(deadline)
    tick(cleanup_deadline)
    work = min(deadline, time.monotonic() + 300.0)
    whole = min(cleanup_deadline, work + 5.0)
    require(signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL,
            'Original direct-child raw wait ownership required')
    owner = {'child': None, 'pidfd': None, 'pidfd_identity': None,
             'pidfd_state': 'absent', 'reaped': False, 'settled': False, 'logs': []}
    RETAINED_COMMANDS.append(owner)
    original = None
    cleanup = []
    paths = [area / (label + '.' + kind) for kind in ('stdout', 'stderr')]
    try:
        for path in paths:
            stream = OriginalLog()
            owner['logs'].append(stream)
            stream.acquire(path, work)
            tick(work)
        out, err = (stream.fd for stream in owner['logs'])
        try:
            tick(work)
            owner['child'] = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=out, stderr=err, close_fds=True, start_new_session=True,
                preexec_fn=child_limits)
            # Retain the actual original Popen before checks or pidfd acquisition.
            tick(work)
            owner['pidfd'] = os.pidfd_open(owner['child'].pid)
            owner['pidfd_state'] = 'acquired'
            value = os.fstat(owner['pidfd'])
            owner['pidfd_identity'] = (value.st_dev, value.st_ino, value.st_mode)
            owner['pidfd_state'] = 'owned'
            poll = select.poll()
            poll.register(owner['pidfd'], select.POLLIN)
            while True:
                tick(work)
                status = os.waitid(os.P_PID, owner['child'].pid,
                                   os.WEXITED | os.WNOHANG | os.WNOWAIT)
                tick(work)
                if status is not None:
                    require(status.si_pid == owner['child'].pid,
                            'Original direct-child terminal PID differs')
                    require(status.si_code == os.CLD_EXITED and status.si_status == 0,
                            'Guest public preparation stage failed: ' + label)
                    break
                events = poll.poll(max(1, min(100, int((work-time.monotonic()) * 1000))))
                tick(work)
                require(all(fd == owner['pidfd'] and flags & select.POLLIN
                            and not flags & (select.POLLERR | select.POLLNVAL)
                            for fd, flags in events), 'Original child pidfd poll refused')
        except BaseException as cause:
            original = cause
        finally:
            # WNOWAIT has not reaped the leader, protecting its original numeric
            # process-group identity. Kill the whole original command group
            # before direct-child reap, including a successful exited leader's
            # remaining group members. Escaped systemd services require the
            # separately admitted entire original VM lifetime and retirement.
            if owner['child'] is not None:
                try:
                    tick(whole)
                    status = os.waitid(os.P_PID, owner['child'].pid,
                                       os.WEXITED | os.WNOHANG | os.WNOWAIT)
                    tick(whole)
                    if status is None:
                        require(os.getsid(owner['child'].pid) == owner['child'].pid,
                                'Original command session identity changed')
                    else:
                        require(status.si_pid == owner['child'].pid,
                                'Original unreaped command leader required')
                    for action in (signal.SIGTERM, signal.SIGKILL):
                        try:
                            os.killpg(owner['child'].pid, action)
                        except ProcessLookupError:
                            pass
                        tick(whole)
                    wait_original_group_terminal(owner['child'].pid, whole)
                    owner['child'].wait(timeout=max(0.001, whole-time.monotonic()))
                    owner['reaped'] = True
                    tick(whole)
                except BaseException as cause:
                    cleanup.append(cause)
            if owner['pidfd'] is not None:
                try:
                    tick(whole)
                    value = os.fstat(owner['pidfd'])
                    require(owner['pidfd_state'] == 'owned'
                            and (value.st_dev, value.st_ino, value.st_mode) == owner['pidfd_identity'],
                            'Original command pidfd slot changed')
                    owner['pidfd_state'] = 'uncertain'
                    os.close(owner['pidfd'])
                    owner['pidfd_state'] = 'released'
                    tick(whole)
                except BaseException as cause:
                    cleanup.append(cause)
    except BaseException as cause:
        if original is None:
            original = cause
        else:
            cleanup.append(cause)
    finally:
        for stream in owner['logs']:
            try:
                tick(whole)
                stream.close(whole)
                tick(whole)
            except BaseException as cause:
                cleanup.append(cause)
        owner['settled'] = (not cleanup and all(stream.closed for stream in owner['logs'])
                            and (owner['child'] is None or owner['reaped']))
        if owner['settled']:
            RETAINED_COMMANDS.remove(owner)
    tick(whole)
    require(all(path.stat().st_size <= MAX_LOG_BYTES for path in paths),
            'Original raw guest diagnostic byte bound exceeded')
    if original is not None or cleanup:
        raise BaseExceptionGroup('Guest public command failed or custody remains retained',
                                 ([original] if original is not None else []) + cleanup)
    tick(deadline)


class DeadlineInput:
    def __init__(self, fd, deadline):
        self.fd, self.deadline = fd, deadline
        self.poll = select.poll()
        self.poll.register(fd, select.POLLIN)

    def read(self, size):
        require(type(size) is int and 0 < size <= CHUNK, 'Finite public stdin read required')
        while True:
            tick(self.deadline)
            events = self.poll.poll(max(1, min(100, int((self.deadline-time.monotonic()) * 1000))))
            tick(self.deadline)
            if events:
                require(all(fd == self.fd and not flags & (select.POLLERR | select.POLLNVAL)
                            for fd, flags in events), 'Original public stdin poll refused')
                raw = os.read(self.fd, size)
                tick(self.deadline)
                return raw


class DeadlineReader:
    def __init__(self, stream, deadline):
        self.stream, self.deadline = stream, deadline

    def read(self, size=-1):
        tick(self.deadline)
        require(0 <= size <= CHUNK, 'Finite original archive read required')
        raw = self.stream.read(size)
        tick(self.deadline)
        return raw


class DeadlineWriter:
    def __init__(self, stream, deadline):
        self.stream, self.deadline = stream, deadline
        self.count = 0

    def write(self, data):
        require(self.count + len(data) <= MAX_BYTES, 'Finite OCI TAR output bound exceeded')
        view = memoryview(data)
        while view:
            tick(self.deadline)
            size = self.stream.write(view[:CHUNK])
            tick(self.deadline)
            require(type(size) is int and 0 < size <= len(view[:CHUNK]),
                    'Original OCI archive write incomplete')
            self.count += size
            view = view[size:]
        return len(data)


def write_images_archive(images, target, deadline):
    tick(deadline)
    with target.open('xb') as output:
        tick(deadline)
        writer = DeadlineWriter(output, deadline)
        with tarfile.open(fileobj=writer, mode='w|', format=tarfile.USTAR_FORMAT) as archive:
            for base, directories, files in os.walk(images, followlinks=False):
                tick(deadline)
                for name in sorted(directories + files):
                    tick(deadline)
                    path = Path(base) / name
                    before = path.lstat()
                    relative = canonical(str(path.relative_to(images)))
                    info = tarfile.TarInfo(relative)
                    if stat.S_ISDIR(before.st_mode):
                        info.type, info.mode = tarfile.DIRTYPE, 0o700
                        archive.addfile(info)
                    else:
                        require(stat.S_ISREG(before.st_mode), 'Original OCI special file refused')
                        info.size, info.mode = before.st_size, 0o600
                        with path.open('rb') as incoming:
                            archive.addfile(info, DeadlineReader(incoming, deadline))
                        tick(deadline)
                        require(path.lstat() == before, 'Original OCI archive input changed')
                    tick(deadline)
        tick(deadline)
    tick(deadline)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--bytes', type=int, required=True)
    parser.add_argument('--source-sha256', required=True)
    parser.add_argument('--seconds', type=float, required=True)
    args = parser.parse_args()
    digest(args.sha256)
    digest(args.source_sha256)
    require(0 < args.bytes <= MAX_BYTES, 'Finite exact bundle length required')
    require(0.0 < args.seconds <= 1800.0, 'Caller clipped guest execution budget required')
    require(os.getresuid() == (0, 0, 0) and platform.system() == 'Linux'
            and platform.machine() == 'x86_64', 'Fixed disposable Ubuntu amd64 fixture required')
    marker = json.loads(Path('/etc/sbarbase-isolated-guest.json').read_bytes())
    require(set(marker) == {'schema', 'run', 'guest', 'profile'} and marker['schema'] == 1
            and marker['profile'] == 'disposable-offline-preparation', 'Original guest seed marker required')
    require(str(uuid.UUID(marker['run'])) == marker['run']
            and str(uuid.UUID(marker['guest'])) == marker['guest'], 'Original public guest identifiers required')
    os.umask(0o077)
    require(args.seconds > 10.0, 'Original guest cleanup reserve required')
    whole = time.monotonic() + args.seconds
    deadline = whole - 10.0
    base = Path('/var/lib/sbarbase-offline')
    if not base.exists():
        base.mkdir(mode=0o700)
    require(not base.is_symlink() and stat.S_IMODE(base.stat().st_mode) == 0o700
            and base.stat().st_uid == 0, 'Original isolated guest public directory required')
    area = base / marker['run']
    area.mkdir(mode=0o700)
    public = area / 'public'
    public.mkdir(mode=0o700)
    spool = area / 'bundle.tar'
    incoming = DeadlineInput(sys.stdin.fileno(), deadline)
    write_member(incoming, spool, args.bytes, args.sha256, deadline)
    require(not incoming.read(1), 'Unexpected trailing public bundle bytes')
    unpack_bundle(spool, public, args.source_sha256, deadline)
    require(hashlib.sha256((public / 'source.tar.gz').read_bytes()).hexdigest() == args.source_sha256,
            'Original application archive differs')
    tick(deadline)
    application = area / 'application'
    application.mkdir(mode=0o700)
    unpack_source(public, application, deadline)
    cache = area / 'cache'
    cache.mkdir(mode=0o700)
    unpack_cache(public, cache, deadline)
    binaries = area / 'bin'
    binaries.mkdir(mode=0o700)
    with zipfile.ZipFile(public / 'bun.zip') as archive:
        row = archive.getinfo('bun-linux-x64-baseline/bun')
        require(row.file_size == 91802480, 'Pinned public Bun size differs')
        write_member(archive.open(row), binaries / 'bun', row.file_size, BUN_SHA, deadline)
    tick(deadline)
    os.chmod(binaries / 'bun', 0o755)
    tick(deadline)
    home = area / 'home'
    home.mkdir(mode=0o700)
    env = {'PATH': str(binaries) + ':/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': str(home),
           'LANG': 'C.UTF-8', 'BUN_INSTALL_CACHE_DIR': str(cache), 'npm_config_userconfig': '/dev/null',
           'PYTHONDONTWRITEBYTECODE': '1', 'DOCKER_HOST': 'unix:///var/run/docker.sock'}
    debs = sorted((public / 'packages').glob('*.deb'))
    require(len(debs) == 5 and all(p.is_file() and not p.is_symlink() for p in debs),
            'Five exact authenticated Docker packages required')
    require(not Path('/var/lib/docker').exists() and not Path('/var/lib/containerd').exists(),
            'Fresh disposable guest daemon storage required')
    command(['/usr/bin/dpkg', '-i', *map(str, debs)], area, env, deadline, 'docker-packages', area,
            cleanup_deadline=whole)
    command(['/usr/bin/systemctl', 'start', 'docker'], area, env, deadline, 'docker-start', area,
            cleanup_deadline=whole)
    image_archive = area / 'images.tar'
    write_images_archive(public / 'images', image_archive, deadline)
    command(['/usr/bin/docker', 'load', '--platform=linux/amd64', '-i', str(image_archive)],
            area, env, deadline, 'image-load', area, cleanup_deadline=whole)
    command(['/usr/bin/docker', 'load', '--platform=linux/amd64', '-i', str(public / 'verifier-oci.tar')],
            area, env, deadline, 'verifier-load', area, cleanup_deadline=whole)
    command([str(binaries / 'bun'), 'install', '--offline', '--frozen-lockfile'],
            application, env, deadline, 'offline-install', area, cleanup_deadline=whole)
    command([str(binaries / 'bun'), 'run', 'build:ui'], application, env, deadline, 'ui-build', area,
            cleanup_deadline=whole)
    command(['/usr/bin/python3', 'lab/console_build_check.py', '--verify-only'],
            application, env, deadline, 'ui-verify', area, cleanup_deadline=whole)
    command(['/usr/bin/python3', 'lab/install_server.py', 'check'],
            application, env, deadline, 'application-preflight', area, cleanup_deadline=whole)
    result = {'scope': 'fresh isolated guest public prerequisites and preflight only',
              'application': str(application), 'bun': str(binaries / 'bun'),
              'source_commit': SOURCE_COMMIT, 'offline_dependencies': True,
              'supabase_installed': False, 'full_restore_tested': False, 'production_accepted': False}
    tick(deadline)
    with (area / 'preparation.json').open('x') as output:
        output.write(json.dumps(result, indent=2) + '\n')
        tick(deadline)
    tick(deadline)
    print(json.dumps(result), flush=True)
    tick(deadline)


if __name__ == '__main__':
    main()
