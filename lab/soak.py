"""Soak sampler: memory, disk, logs, restarts and health of one installation over time.

Usage:
  /usr/bin/python3 lab/soak.py --minutes 60 [--interval 300] [--evidence PATH]

Every interval it records the host's available memory and swap, the load, each Sbarbase
container's measured memory and restart count, the free disk, the size of the runtime
state, the backups and the Docker volumes, the container logs, and whether the console,
every environment's Auth and REST answer. The check fails when a container restarted,
when a health probe failed, or when the host's available memory fell under 512 MiB. Growth
over the run is reported, not judged: a run of hours says nothing about a week.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / '.lab' / 'upstream'


def docker(*args):
    return subprocess.run(['docker', *args], capture_output=True, text=True, timeout=120).stdout


def meminfo():
    fields = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        name, value = line.split(':', 1)
        fields[name] = int(value.split()[0]) // 1024
    return {'available_mib': fields['MemAvailable'], 'swap_used_mib': fields['SwapTotal'] - fields['SwapFree']}


def mib(text):
    text = text.strip()
    for unit, factor in (('GiB', 1024), ('MiB', 1), ('KiB', 1 / 1024), ('GB', 1000), ('MB', 1), ('kB', 1 / 1000), ('B', 1 / 1048576)):
        if text.endswith(unit):
            return round(float(text[:-len(unit)]) * factor)
    return 0


def size_mib(path):
    total = 0
    for folder, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(folder, name)).st_size
            except OSError:
                pass
    return total // 1048576


def answers(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status < 500
    except urllib.error.HTTPError as error:
        return error.code < 500
    except Exception:
        return False


def sample():
    names = [n for n in docker('ps', '-a', '--format', '{{.Names}}').split() if n.startswith('sbarbase-')]
    stats = {}
    for line in docker('stats', '--no-stream', '--format', '{{.Name}}\t{{.MemUsage}}').splitlines():
        name, usage = line.split('\t')
        if name.startswith('sbarbase-'):
            stats[name] = mib(usage.split('/')[0])
    containers = {}
    logs = 0
    for item in json.loads(docker('inspect', *names) or '[]'):
        name = item['Name'].lstrip('/')
        log = item.get('LogPath') or ''
        containers[name] = {'running': item['State']['Running'], 'restarts': item['RestartCount'],
                            'used_mib': stats.get(name, 0)}
        try:
            logs += os.stat(log).st_size
        except OSError:
            pass
    endpoints = json.loads((STATE / 'endpoints.json').read_text()) if (STATE / 'endpoints.json').exists() else {}
    server = json.loads((STATE / 'server.json').read_text())['url'] if (STATE / 'server.json').exists() else None
    health = {'console': bool(server) and answers(server + '/')}
    for e, item in endpoints.items():
        health[e] = answers(item['auth'] + '/health') and answers(item['rest'] + '/')
    volumes = {}
    for line in docker('system', 'df', '-v', '--format', '{{json .Volumes}}').splitlines():
        for volume in json.loads(line or '[]') or []:
            if volume.get('Name', '').startswith('sbarbase'):
                volumes[volume['Name']] = volume.get('Size')
    disk = shutil.disk_usage('/')
    return {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'host': meminfo(),
            'load': Path('/proc/loadavg').read_text().split()[:3], 'disk_free_mib': disk.free // 1048576,
            'state_mib': size_mib(ROOT / '.lab' / 'upstream'), 'backups_mib': size_mib(ROOT / '.lab' / 'backups'),
            'container_logs_mib': logs // 1048576 if logs else None, 'volumes': volumes,
            'containers_used_mib': sum(c['used_mib'] for c in containers.values()), 'containers': containers,
            'health': health}


def main(argv=None):
    parser = argparse.ArgumentParser(description='soak sampler')
    parser.add_argument('--minutes', type=int, required=True)
    parser.add_argument('--interval', type=int, default=300)
    parser.add_argument('--evidence', default='docs/evidence/soak.json')
    args = parser.parse_args(argv)
    started = time.time()
    samples = []
    while True:
        samples.append(sample())
        last = samples[-1]
        print(f"{last['at']}  available {last['host']['available_mib']} MiB  containers {last['containers_used_mib']} MiB  "
              f"disk free {last['disk_free_mib']} MiB  healthy {all(last['health'].values())}", flush=True)
        if time.time() - started + args.interval > args.minutes * 60:
            break
        time.sleep(args.interval)
    first, last = samples[0], samples[-1]
    restarted = sorted(n for n, c in last['containers'].items()
                       if c['restarts'] > first['containers'].get(n, {}).get('restarts', 0))
    unhealthy = sorted({f"{s['at']} {name}" for s in samples for name, ok in s['health'].items() if not ok})
    low = min(s['host']['available_mib'] for s in samples)
    checks = [
        {'check': 'no Sbarbase container restarted during the soak', 'ok': not restarted, 'detail': ', '.join(restarted)},
        {'check': 'the console and every environment answered at every sample', 'ok': not unhealthy, 'detail': '; '.join(unhealthy[:10])},
        {'check': 'available host memory never fell under 512 MiB', 'ok': low >= 512, 'detail': f'lowest {low} MiB'},
    ]
    growth = {'containers_used_mib': last['containers_used_mib'] - first['containers_used_mib'],
              'disk_used_mib': first['disk_free_mib'] - last['disk_free_mib'],
              'state_mib': last['state_mib'] - first['state_mib'],
              'container_logs_mib': (last['container_logs_mib'] or 0) - (first['container_logs_mib'] or 0)}
    passed = all(c['ok'] for c in checks)
    Path(args.evidence).write_text(json.dumps({
        'check': 'soak', 'recorded': last['at'], 'passed': passed, 'minutes': round((time.time() - started) / 60),
        'interval_seconds': args.interval, 'growth': growth, 'checks': checks, 'samples': samples}, indent=2) + '\n')
    for c in checks:
        print(('ok:   ' if c['ok'] else 'FAIL: ') + c['check'] + (f"  {c['detail']}" if c['detail'] else ''))
    print(f'growth over the run: {growth}\nevidence: {args.evidence}')
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
