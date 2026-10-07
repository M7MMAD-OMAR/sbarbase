"""Bounded output and owned transient-unit collection for transfer check wrappers."""
import os
import selectors
import shutil
import stat
import subprocess
import time
import uuid
import tempfile

MAX_OUTPUT = 128 * 1024


def retain_source_output(label, stdout, stderr):
    """Retain source-fixture diagnostics privately, never native credentials."""
    directory = os.environ.get('SB06_SOURCE_EVIDENCE_DIR')
    if directory is None:
        directory = tempfile.mkdtemp(prefix='sb06-transfer-source-')
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise RuntimeError('Private source evidence directory refused')
        if label not in ('python-units', 'unit', 'types', 'history'):
            raise RuntimeError('Source diagnostic label refused')
        for channel, content in (('stdout', stdout), ('stderr', stderr)):
            data = content.encode('utf-8') if isinstance(content, str) else bytes(content)
            if len(data) > MAX_OUTPUT:
                raise RuntimeError('Source diagnostics exceeded boundary')
            name = label + '-' + uuid.uuid4().hex + '.' + channel + '.log'
            output = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=descriptor)
            try:
                offset = 0
                while offset < len(data):
                    written = os.write(output, data[offset:])
                    if written < 1:
                        raise RuntimeError('Source diagnostics write refused')
                    offset += written
                os.fsync(output)
            finally:
                os.close(output)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def bounded_check(root, command, *, kind, memory, service_seconds):
    launcher, controller = shutil.which('systemd-run'), shutil.which('systemctl')
    if launcher is None or controller is None or kind not in ('unit', 'types', 'history'):
        raise RuntimeError('Transfer verification resource boundary unavailable')
    unit = 'sb06-transfer-' + kind + '-' + uuid.uuid4().hex[:12]
    argv = [launcher, '--user', '--quiet', '--wait', '--pipe', '--unit=' + unit,
            '-p', 'MemoryMax=' + memory, '-p', 'CPUQuota=50%', '-p', 'TasksMax=128',
            '-p', 'RuntimeMaxSec=' + str(service_seconds), '--working-directory=' + str(root), *command]
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    selector = selectors.DefaultSelector()
    channels = {'stdout': bytearray(), 'stderr': bytearray()}
    deadline, total = time.monotonic() + service_seconds + 10, 0
    try:
        for name, stream in (('stdout', process.stdout), ('stderr', process.stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError('Transfer verification collection timed out')
            for key, _events in selector.select(min(remaining, 1)):
                chunk = os.read(key.fd, 16384)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                total += len(chunk)
                if total > MAX_OUTPUT:
                    raise RuntimeError('Transfer verification output exceeded boundary')
                channels[key.data].extend(chunk)
        returncode = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        stdout, stderr = channels['stdout'].decode('utf-8', errors='replace'), channels['stderr'].decode('utf-8', errors='replace')
        retain_source_output(kind, channels['stdout'], channels['stderr'])
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)
    except BaseException:
        # Stop only this newly allocated owned unit. A failed stop never yields
        # successful evidence, and RuntimeMaxSec remains the service backstop.
        try:
            retain_source_output(kind, channels['stdout'], channels['stderr'])
        except (OSError, RuntimeError):
            pass
        try:
            subprocess.run([controller, '--user', 'stop', unit], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
        process.kill()
        process.wait(timeout=5)
        raise
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            stream.close()
