"""Cgroup v2 pressure measurement for the owned containers, and its response.

Two things live here, and they are deliberately separate.

The admission-time gate (the first half) is unchanged: one snapshot of the owned
database and Storage container, and a refusal when any ten-second average is at
or over its threshold. Every caller that already depends on `snapshot()` and
`refusal()` keeps the same behaviour and the same vocabulary.

The second half is docs/engineering/RESOURCE-POLICY.md section 5.2: a bounded repeated
sampler, a summary of the series it took, and the one response this design can
support today. Read RESPONSE_SCOPE below before wiring it anywhere. The response
refuses new admissions while the latest reading is at or over a threshold and
writes the crossing to an append-only ledger, and it does nothing else. Killing,
pausing or throttling a running container is not implemented here, and section 2
item 6 with section 7 step 7 of that document is where the design says what is
still missing.

The measured facts this module reports are the kernel's PSI averages for cpu
(some), io (full) and memory (full). `docs/engineering/PRESSURE-ADMISSION.md` records what
those signals mean, and `docs/engineering/RESOURCE-POLICY.md` section 6.1 item 7 records the
limit of any statement made from them: this is stall time inside one container's
cgroup, not utilization, and it says nothing about two tenants inside the shared
PostgreSQL engine.
"""
import json
import math
import os
import time
from datetime import datetime, timezone
from resource_admission import docker

THRESHOLDS = {'cpu_some10': 50.0, 'io_full10': 20.0, 'memory_full10': 1.0}
CONTAINERS = ('sbarbase-durable-db', 'sbarbase-durable-storage')

# Sampling bounds for the repeated series (docs/engineering/RESOURCE-POLICY.md 5.2). The
# design asks for a sample every 5 seconds; the defaults are that, over a
# 30-second window. The bounds exist so this cannot turn a measurement into a
# stress test: at most 120 readings, at most ten minutes, and an interval no
# shorter than half a second.
DEFAULT_INTERVAL_SECONDS = 5.0
DEFAULT_WINDOW_SECONDS = 30.0
MIN_INTERVAL_SECONDS = 0.5
MAX_WINDOW_SECONDS = 600.0
MAX_SAMPLES = 120

# The durable crossing record. It lives under lab.STATE (.lab/, git-ignored),
# because it is operational state written by a running installation, not a
# document. The evidence a run commits is separate and lives in docs/evidence/.
LEDGER_NAME = 'pressure-crossings.jsonl'

# What the response in this module does, and what it deliberately does not.
# Section 2 item 6 and section 7 step 7 of docs/engineering/RESOURCE-POLICY.md name the
# graduated response the design proposes; only the first level is supported by
# the machinery that exists today, and the rest is stated as unsupported so no
# reader can mistake this module for the whole design.
RESPONSE_SCOPE = {
    'implemented': 'refuse new admissions while the latest reading is at or over a threshold, and record every crossing durably',
    'not_implemented': [
        'stop, kill or restart any running container',
        'pause or throttle a running environment (the gateway pause lease is in-process, disappears on restart, and does not prove its SQL stopped; docs/engineering/GATEWAY-DRAIN.md)',
        'change a container CPU quota, CPU weight, block IO limit or memory ceiling at run time',
        'act on a per-class threshold: no per-environment class exists in the runtime state yet, so only the shared thresholds below are evaluated',
    ],
    'limit': 'a refusal at admission is not sustained isolation, and a crossing recorded here is not proof that any tenant was slowed down',
}


def avg10(text, kind):
    matches = [line.split() for line in text.splitlines() if line.split() and line.split()[0] == kind]
    if len(matches) != 1:
        raise ValueError('Pressure measurement unavailable')
    fields = dict(part.split('=', 1) for part in matches[0][1:])
    value = float(fields['avg10'])
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError('Invalid pressure measurement')
    return value


def snapshot(containers=CONTAINERS):
    """One reading of every named container's cgroup v2 pressure.

    The default container tuple is the admission-time gate's, so existing
    callers are unaffected. A caller measuring its own disposable containers
    passes its own names, which is what `series()` does.
    """
    result = {}
    for container in containers:
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


def refusal(values, containers=CONTAINERS):
    """The first metric at or over its threshold, or None. Fails closed.

    `containers` defaults to the admission-time tuple, so this is the same
    function every existing caller already uses. A caller summarising its own
    series passes that series' container tuple.
    """
    if set(values) != set(containers):
        raise ValueError('Incomplete pressure snapshot')
    for container in containers:
        if set(values[container]) != set(THRESHOLDS):
            raise ValueError('Incomplete pressure metrics')
        for name, limit in THRESHOLDS.items():
            value = values[container][name]
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError('Invalid pressure metric')
            if value >= limit:
                return name
    return None


def series(window_seconds=DEFAULT_WINDOW_SECONDS, interval_seconds=DEFAULT_INTERVAL_SECONDS, *,
           containers=CONTAINERS, read=None, sleep=time.sleep, clock=time.monotonic):
    """Take a bounded series of readings over a window and return them raw.

    The reading count is `window_seconds // interval_seconds`, so the series
    takes the number of readings the window asks for. The interval is the pause
    between readings; a reading itself costs time (four Docker calls per
    container), so on a loaded host the wall time of a series is longer than the
    window and the observed span is returned in `elapsed_seconds` next to the
    requested window rather than hidden.

    Every reading is validated by `refusal`, which fails closed on a missing
    container, a missing metric or a value outside 0 to 100, so a partial
    reading cannot be summarised as if it were complete. Each reading carries
    the instant it started and how long it took, both measured from the series
    start. `read`, `sleep` and `clock` are injectable, so the sampling
    arithmetic is tested without a host.
    """
    for name, value in (('interval_seconds', interval_seconds), ('window_seconds', window_seconds)):
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('Sampling bounds must be positive finite numbers: ' + name)
    if interval_seconds < MIN_INTERVAL_SECONDS:
        raise ValueError('Sampling interval below the configured minimum')
    if window_seconds > MAX_WINDOW_SECONDS:
        raise ValueError('Sampling window above the configured maximum')
    count = int(window_seconds // interval_seconds)
    if count < 2:
        raise ValueError('A series needs at least two readings')
    if count > MAX_SAMPLES:
        raise ValueError('Sampling window would exceed the configured maximum reading count')
    containers = tuple(containers)
    if not containers:
        raise ValueError('A series needs at least one container')
    measure = read or (lambda: snapshot(containers))
    started = clock()
    readings = []
    for index in range(count):
        at = clock() - started
        values = measure()
        # Raises on an incomplete or invalid reading instead of keeping it.
        refusal(values, containers)
        taken = clock() - started - at
        readings.append({'index': index, 'at_seconds': round(at, 3), 'read_seconds': round(taken, 3),
                         'values': values})
        if index != count-1:
            sleep(interval_seconds)
    return {'containers': list(containers), 'interval_seconds': float(interval_seconds),
            'window_seconds': float(window_seconds), 'readings': readings,
            'elapsed_seconds': round(readings[-1]['at_seconds']+readings[-1]['read_seconds'], 3)}


def summarise(record):
    """Summarise a series: count, the avg10 values, and every threshold crossing.

    `avg10` holds the mean of each metric over the series, in the same shape the
    single `snapshot()` returns, so the two can be compared key by key. `max10`
    and `last10` hold the same shape with the series maximum and the most recent
    reading, and `latest_refusal` is the metric that would refuse an admission
    right now, which is the reading the response acts on. A crossing is reported
    per container and metric with the count of readings at or over the
    threshold, the duration those readings cover, and the peak.

    The durations come from the measured instants each reading started, not from
    a count of intervals: `span_seconds` is the time between the first and the
    last over-threshold readings, and `duration_seconds` adds one sampling
    interval on the end, because a reading at or over the threshold covers up to
    the next reading. The resolution is therefore the sampling interval, and it
    is honest about it: a crossing shorter than one interval is seen once and
    reported as one interval, and a host slow enough to stretch the spacing
    between readings shows that in `elapsed_seconds` and in the raw
    `at_seconds`, never as a finer figure than was measured.
    """
    readings = record.get('readings')
    if not readings:
        raise ValueError('A summary needs at least one reading')
    containers = tuple(record.get('containers') or sorted(readings[0]['values']))
    interval = record.get('interval_seconds')
    if type(interval) not in (int, float) or not math.isfinite(interval) or interval <= 0:
        raise ValueError('A summary needs a positive sampling interval')
    count = len(readings)
    avg10, max10, last10 = {}, {}, {}
    for container in containers:
        avg10[container], max10[container], last10[container] = {}, {}, {}
        for metric in THRESHOLDS:
            values = []
            for reading in readings:
                refusal(reading.get('values'), containers)
                values.append(reading['values'][container][metric])
            avg10[container][metric] = sum(values)/count
            max10[container][metric] = max(values)
            last10[container][metric] = values[-1]
    crossings, peak = [], None
    for container in containers:
        for metric, limit in THRESHOLDS.items():
            values = [reading['values'][container][metric] for reading in readings]
            highest = max(values)
            if peak is None or highest > peak['value']:
                peak = {'container': container, 'metric': metric, 'value': highest}
            over = [index for index, value in enumerate(values) if value >= limit]
            if not over:
                continue
            first, last = readings[over[0]], readings[over[-1]]
            span = last.get('at_seconds', 0)-first.get('at_seconds', 0)
            crossings.append({
                'container': container, 'metric': metric, 'threshold': limit,
                'samples_over': len(over), 'first_index': over[0], 'last_index': over[-1],
                'span_seconds': round(span, 3),
                'duration_seconds': round(span+interval, 3),
                'first_at_seconds': first.get('at_seconds'), 'last_at_seconds': last.get('at_seconds'),
                'peak': max(values[index] for index in over)})
    return {
        'count': count,
        'containers': list(containers),
        'interval_seconds': float(interval),
        'window_seconds': float(record.get('window_seconds') or count*interval),
        'observed_seconds': round(readings[-1].get('at_seconds', 0)+readings[-1].get('read_seconds', 0), 3),
        'thresholds': dict(THRESHOLDS),
        'avg10': avg10, 'max10': max10, 'last10': last10,
        'latest_refusal': refusal(last10, containers),
        'crossed': bool(crossings),
        'crossings': crossings,
        'peak': peak,
    }


def ledger_path():
    """The append-only crossing ledger, under the lab state directory."""
    import run as lab
    return lab.STATE / LEDGER_NAME


def record_crossings(summary, path=None, now=time.time):
    """Append every crossing in a summary to the durable ledger, or report why not.

    One JSON object per line, each flushed and fsynced before the next, so a
    crossing that was seen is on disk even if the host goes away immediately
    afterwards. A write failure never changes the admission decision: the
    refusal stands on the measured values, and `ok` plus `error` carry the
    recording outcome so a caller cannot report a record it does not have.
    """
    target = path or ledger_path()
    records = []
    for crossing in summary.get('crossings', []):
        records.append({'at': round(now(), 3),
                        'utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                        'container': crossing['container'], 'metric': crossing['metric'],
                        'threshold': crossing['threshold'], 'peak': crossing['peak'],
                        'samples_over': crossing['samples_over'],
                        'duration_seconds': crossing['duration_seconds'],
                        'span_seconds': crossing['span_seconds'],
                        'first_at_seconds': crossing['first_at_seconds'],
                        'last_at_seconds': crossing['last_at_seconds'],
                        'samples': summary.get('count'),
                        'interval_seconds': summary.get('interval_seconds'),
                        'window_seconds': summary.get('window_seconds')})
    if not records:
        return {'path': str(target), 'records': [], 'ok': True, 'error': None}
    try:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with target.open('a') as handle:
            for record in records:
                handle.write(json.dumps(record) + '\n')
                handle.flush()
                os.fsync(handle.fileno())
    except OSError as error:
        # The error class only; a path in a message could name a secret.
        return {'path': str(target), 'records': records, 'ok': False, 'error': type(error).__name__}
    return {'path': str(target), 'records': records, 'ok': True, 'error': None}


def refusing_container(summary, metric):
    """Which container is at or over the threshold in the latest reading."""
    if not metric:
        return None
    for container, values in (summary.get('last10') or {}).items():
        if values.get(metric) is not None and values[metric] >= THRESHOLDS[metric]:
            return container
    return None


def response(summary, ledger=None, now=time.time):
    """The one response this design supports today, decided from a summary.

    Refuse new admissions while the most recent reading is at or over a
    threshold, and record every crossing the series saw to the ledger. The
    refusal reason is the measured metric name, which is the same reason the
    admission-time gate already produces. Running work is not touched, and the
    scope that is deliberately out of reach is returned in `not_implemented` so
    a caller cannot present this as a continuous remedy.

    A crossing that has already ended is recorded and does not refuse: the
    ledger is history, the decision is the present reading.
    """
    metric = summary.get('latest_refusal')
    container = refusing_container(summary, metric)
    current = [crossing for crossing in summary.get('crossings', [])
               if crossing['metric'] == metric and crossing['container'] == container]
    return {
        'action': 'refuse_new_admissions' if metric else 'admit',
        'refuse': bool(metric),
        'reason': 'pressure_' + metric if metric else None,
        'metric': metric,
        'container': container,
        'over_threshold_seconds': current[0]['duration_seconds'] if current else None,
        'crossed': bool(summary.get('crossings')),
        'record': record_crossings(summary, ledger, now),
        'scope': RESPONSE_SCOPE['implemented'],
        'not_implemented': list(RESPONSE_SCOPE['not_implemented']),
        'limit': RESPONSE_SCOPE['limit'],
    }


if __name__ == '__main__':
    try:
        measured = snapshot()
        print(json.dumps({'snapshot': measured, 'thresholds': THRESHOLDS, 'refusal': refusal(measured)}))
    except Exception:
        raise SystemExit('Pressure measurement unavailable; no admission decision can be made.')