import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import tempfile
import uuid

root = Path(__file__).resolve().parent
unit = 'sbarbase-fd-probe-' + uuid.uuid4().hex + '.scope'
env = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'XDG_RUNTIME_DIR': '/run/user/' + str(os.getuid())}
held = []
child = None
pidfd = None
result = {'scope': unit, 'guest_launched': False, 'docker_used': False}

def identity(fd):
    s = os.fstat(fd)
    return [s.st_dev, s.st_ino, stat.S_IFMT(s.st_mode), s.st_rdev]

def control(*args):
    return subprocess.run(['/usr/bin/systemctl', '--user', *args], env=env,
                          capture_output=True, timeout=2)

try:
    with tempfile.TemporaryDirectory(prefix='inputs-', dir=root) as name:
        directory = Path(name)
        for index in range(3):
            p = directory / str(index)
            p.write_bytes(b'disposable descriptor fixture\n')
            p.chmod(0o600)
            held.append(os.open(p, (os.O_RDWR if index == 1 else os.O_RDONLY) | os.O_NOFOLLOW | os.O_CLOEXEC))
        for p in ('/usr/bin/python3', '/usr/bin/systemd-run'):
            held.append(os.open(os.path.realpath(p), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC))
        expected = [identity(fd) for fd in held]
        assert all(not os.get_inheritable(fd) for fd in held)
        executable_pin = hashlib.sha256(Path('/proc/self/fd/' + str(held[-1])).read_bytes()).hexdigest()
        assert executable_pin == 'f2e6b4cfc7e58b4aeccc81fb3521c92f28c1a17449caa8e20915d22ff0efe29c'
        program = ('import os,json,stat,select; fds=json.loads(os.environ["PROBE_FDS"]); '
                   'rows=[[os.fstat(f).st_dev,os.fstat(f).st_ino,stat.S_IFMT(os.fstat(f).st_mode),os.fstat(f).st_rdev] for f in fds]; '
                   'print(json.dumps({"pid":os.getpid(),"identities":rows,"cgroup":open("/proc/self/cgroup").read()}),flush=True); '
                   'assert select.select([0],[],[],8)[0]; assert os.read(0,1)==b""')
        child_env = dict(env, PROBE_FDS=json.dumps(held))
        argv = ['/usr/bin/systemd-run', '--user', '--scope', '--collect', '--quiet',
                '--no-ask-password', '--expand-environment=no', '--unit=' + unit,
                '--property=MemoryMax=256M', '--property=MemorySwapMax=0',
                '--property=CPUQuota=50%', '--property=TasksMax=16', '--property=RuntimeMaxSec=12s',
                '/proc/self/fd/' + str(held[-2]), '-c', program]
        child = subprocess.Popen(argv, executable='/proc/self/fd/' + str(held[-1]),
                                 pass_fds=tuple(held), close_fds=True, env=child_env,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        pidfd = os.pidfd_open(child.pid)
        child_start = Path('/proc/' + str(child.pid) + '/stat').read_text().rpartition(') ')[2].split()[19]
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            assert selector.select(7), 'Scoped helper did not report before deadline'
            line = child.stdout.readline(8193)
        assert len(line) <= 8192 and line.endswith(b'\n'), 'Bounded helper output required'
        observed = json.loads(line)
        assert observed['pid'] == child.pid, 'Scope must exec payload in exact Popen process'
        assert observed['identities'] == expected, 'Inherited five descriptors differ'
        assert unit in observed['cgroup'], 'Payload not in declared owned scope'
        show = control('show', unit, '--property=ActiveState,ControlGroup,MemoryMax,MemorySwapMax,TasksMax,CPUQuotaPerSecUSec')
        assert show.returncode == 0
        values = dict(row.split('=', 1) for row in show.stdout.decode().splitlines())
        assert values['ActiveState'] == 'active'
        assert values['MemoryMax'] == str(256 * 1024 * 1024)
        assert values['MemorySwapMax'] == '0' and values['TasksMax'] == '16'
        assert values['CPUQuotaPerSecUSec'] == '500ms'
        assert values['ControlGroup'].endswith('/' + unit)
        assert Path('/proc/' + str(child.pid) + '/stat').read_text().rpartition(') ')[2].split()[19] == child_start
        members = Path('/sys/fs/cgroup' + values['ControlGroup'] + '/cgroup.procs').read_text().split()
        assert str(child.pid) in members
        out, err = child.communicate(input=b'', timeout=5)
        assert not out and not err and child.returncode == 0
        closed = control('show', unit, '--property=ActiveState')
        assert closed.returncode != 0 or closed.stdout.strip() == b'ActiveState=inactive'
        result.update(passed=True, systemd_sha256=executable_pin, descriptors=5,
                      same_pid_exec=True, same_descriptor_identities=True,
                      resource_properties=values, child_exit=0, scope_inactive_or_absent=True,
                      retained_pidfd=True, cgroup_member_verified=True,
                      scope_limit='Owned ephemeral scope, Python payload only; no QEMU or guest acceptance')
finally:
    try:
        if child is not None and child.poll() is None:
            if pidfd is None:
                child.terminate()
            else:
                try:
                    signal.pidfd_send_signal(pidfd, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                if pidfd is None:
                    child.kill()
                else:
                    try:
                        signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                child.wait(timeout=3)
    finally:
        if pidfd is not None:
            os.close(pidfd)
        for fd in held:
            os.close(fd)

(root / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
(root / 'result.json').chmod(0o600)
print(json.dumps(result))
