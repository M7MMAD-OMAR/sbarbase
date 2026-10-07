#!/usr/bin/env python3
"""Exercise a disposable CLI terminal without exporting its private transcript."""
import base64
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import termios
import time


def code(secret):
    key = base64.b32decode(secret + '=' * ((8 - len(secret) % 8) % 8))
    digest = hmac.new(key, struct.pack('>Q', int(time.time()) // 30), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return str((struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7fffffff) % 1000000).zfill(6)


def main():
    config = json.loads(Path(sys.argv[1]).read_text())
    master, slave = pty.openpty()
    initial = termios.tcgetattr(slave)
    checks = []
    transcript = b''
    channels = {'stdout': b'', 'stderr': b''}
    sent_password = sent_code = False
    interrupted = False
    secret = verification = None
    scenario = config.get('scenario', 'success')
    environment = dict(os.environ)
    if scenario == 'stty-failure':
        private_bin = Path(config['root']) / 'terminal-tools'
        private_bin.mkdir(mode=0o700, exist_ok=True)
        stub = private_bin / 'stty'
        stub.write_text('#!/bin/sh\nif [ "$3" = "-echo" ]; then\n /usr/bin/stty "$@"\n exit 1\nfi\nexec /usr/bin/stty "$@"\n')
        stub.chmod(0o700)
        environment['PATH'] = str(private_bin) + os.pathsep + environment.get('PATH', '')

    def terminal():
        os.setsid()
        if not scenario.startswith('headless-'):
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

    credentials = ['--operator-file', sys.argv[1]] if scenario.endswith('operator-file') else ['--email', config['email']]
    password_pipe = scenario.endswith('password-stdin')
    if password_pipe:
        credentials.append('--password-stdin')
    process = subprocess.Popen([shutil.which('bun') or 'bun', config['script'],
                                *config.get('command', ['add-environment', config['project'], config['name']]), *credentials],
                               cwd=config['root'], env=environment, stdin=subprocess.PIPE if password_pipe else slave,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=terminal, pass_fds=(slave,))
    if password_pipe:
        process.stdin.write(config['password'].encode() + b'\n')
        process.stdin.close()
    sent_password = password_pipe or scenario.endswith('operator-file')
    os.close(slave)
    streams = {master: 'terminal', process.stdout.fileno(): 'stdout', process.stderr.fileno(): 'stderr'}
    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            for descriptor in select.select(list(streams), [], [], .1)[0]:
                try:
                    data = os.read(descriptor, 4096)
                except OSError:
                    data = b''
                if not data:
                    streams.pop(descriptor, None)
                    continue
                channel = streams[descriptor]
                if channel == 'terminal':
                    transcript += data
                else:
                    channels[channel] += data
            if not sent_password and b'Password: ' in transcript:
                checks.append({'name': 'actual CLI terminal disables password echo', 'ok': not bool(termios.tcgetattr(master)[3] & termios.ECHO)})
                os.write(master, config['password'].encode() + b'\n')
                sent_password = True
            match = re.search(rb'Enter this key in your authenticator app:\s+([A-Z2-7]+)', transcript)
            if match:
                secret = match.group(1).decode()
            if sent_password and not sent_code and not interrupted and secret and b'Authenticator code: ' in transcript:
                checks.append({'name': 'actual CLI terminal disables TOTP echo', 'ok': not bool(termios.tcgetattr(master)[3] & termios.ECHO)})
                if scenario == 'prompt-cancel':
                    os.kill(process.pid, signal.SIGINT)
                    interrupted = True
                else:
                    verification = code(secret)
                    os.write(master, verification.encode() + b'\n')
                    sent_code = True
            if scenario == 'verify-cancel' and sent_code and not interrupted and Path(config['marker']).exists():
                os.kill(process.pid, signal.SIGINT)
                interrupted = True
                acknowledgement = Path(config['marker'] + '.cancelled')
                acknowledgement.write_text('interruption delivered\n')
                acknowledgement.chmod(0o600)
            if process.poll() is not None:
                break
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        result = process.wait(timeout=3)
        while streams and select.select(list(streams), [], [], .1)[0]:
            descriptor = select.select(list(streams), [], [], .1)[0][0]
            try:
                data = os.read(descriptor, 4096)
            except OSError:
                data = b''
            if not data:
                streams.pop(descriptor, None)
                continue
            channel = streams[descriptor]
            if channel == 'terminal':
                transcript += data
            else:
                channels[channel] += data
        successful = scenario.startswith('success')
        expected = result == 0 and sent_code if successful else result != 0
        private_values = [config['password'].encode()] + ([secret.encode()] if secret else []) + ([verification.encode()] if verification else [])
        checks.extend([
            {'name': 'actual CLI terminal ' + scenario + ' has expected exit', 'ok': expected},
            {'name': 'actual CLI private setup display clears on ' + scenario, 'ok': scenario == 'stty-failure' or scenario.startswith('headless-') or (b'\x1b[?1049h' in transcript and b'\x1b[2J\x1b[H\x1b[?1049l' in transcript)},
            {'name': 'actual CLI terminal restores original echo state', 'ok': termios.tcgetattr(master)[3] == initial[3]},
            {'name': 'actual CLI terminal never echoes password or typed TOTP', 'ok': config['password'].encode() not in transcript and (not verification or verification.encode() not in transcript)},
            {'name': 'actual CLI stdout and stderr contain no authentication secrets', 'ok': all(value not in channels['stdout'] + channels['stderr'] for value in private_values)},
        ])
        if scenario in ('prompt-cancel', 'verify-cancel'):
            checks.append({'name': 'actual CLI ' + scenario + ' delivered interruption', 'ok': interrupted})
        if scenario == 'verify-cancel':
            checks.append({'name': 'actual CLI cancellation follows original native verification', 'ok': sent_code and Path(config['marker']).exists()})
    except Exception:
        checks.append({'name': 'actual CLI terminal completed within bounded fixture', 'ok': False})
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
        os.close(master)
    print(json.dumps({'checks': checks, 'total': len(checks), 'failed': sum(not check['ok'] for check in checks),
                      'skipped': 0, 'exit_code': process.returncode,
                      'capacity_refused': b'environment limit' in channels['stderr']}))


if __name__ == '__main__':
    main()
