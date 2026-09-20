"""Cgroup v2 pressure gate for the owned database and Storage containers."""
import json
import math
from resource_admission import docker

THRESHOLDS = {'cpu_some10': 50.0, 'io_full10': 20.0, 'memory_full10': 1.0}
CONTAINERS = ('sbarbase-durable-db', 'sbarbase-durable-storage')


def avg10(text, kind):
    matches = [line.split() for line in text.splitlines() if line.split() and line.split()[0] == kind]
    if len(matches) != 1:
        raise ValueError('Pressure measurement unavailable')
    fields = dict(part.split('=', 1) for part in matches[0][1:])
    value = float(fields['avg10'])
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError('Invalid pressure measurement')
    return value


def snapshot():
    result = {}
    for container in CONTAINERS:
        # Require private cgroup namespace so /sys/fs/cgroup means this container.
        info = json.loads(docker('inspect', '--format', '{{json .HostConfig}}', container))
        if info.get('CgroupnsMode') != 'private':
            raise RuntimeError('Private cgroup namespace required for pressure measurement')
        result[container] = {
            'cpu_some10': avg10(docker('exec', container, 'cat', '/sys/fs/cgroup/cpu.pressure'), 'some'),
            'io_full10': avg10(docker('exec', container, 'cat', '/sys/fs/cgroup/io.pressure'), 'full'),
            'memory_full10': avg10(docker('exec', container, 'cat', '/sys/fs/cgroup/memory.pressure'), 'full'),
        }
    return result


def refusal(values):
    if set(values) != set(CONTAINERS):
        raise ValueError('Incomplete pressure snapshot')
    for container in CONTAINERS:
        if set(values[container]) != set(THRESHOLDS):
            raise ValueError('Incomplete pressure metrics')
        for name, limit in THRESHOLDS.items():
            value = values[container][name]
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError('Invalid pressure metric')
            if value >= limit:
                return name
    return None


if __name__ == '__main__':
    try:
        measured = snapshot()
        print(json.dumps({'snapshot': measured, 'thresholds': THRESHOLDS, 'refusal': refusal(measured)}))
    except Exception:
        raise SystemExit('Pressure measurement unavailable; no admission decision can be made.')
