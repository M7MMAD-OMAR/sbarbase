"""Bounded pressure response check: real readings, a real crossing, disposable only.

docs/engineering/RESOURCE-POLICY.md section 5.2 asks the pressure module to sample
repeatedly, to define a response, and to measure it. This driver is that
measurement, and it is deliberately narrow:

- It creates one disposable container with a private cgroup namespace, a 0.25
  CPU quota and a 128 MiB ceiling, runs sixteen busy loops inside it, and takes a
  bounded series of real readings through pressure_admission.series().
- It then stops the load inside the same container and takes a second series, so
  the response is shown acting and then clearing on measured values rather than
  argued in prose.
- It never starts, stops or touches the retained durable runtime, and it removes
  every container it created in a finally block.

The 0.25 CPU quota is what this probe costs the host: the container's own quota
bounds it and spinner count does not raise it. The run takes about two minutes
on a loaded host, most of it Docker call latency inside the readings, so the
series reports the elapsed span next to the requested window instead of assuming
they are equal. The pinned database image is reused from lab/images.lock.json;
nothing is pulled, and no network is attached to the probe.

Exit codes: 0 when the run is valid (whether or not a threshold was crossed,
because a host under different load is a result, not a defect), 2 when a
recorded check failed, and any other non-zero exit for an unexpected error. Read
`crossing_produced` in the evidence before quoting this run as a demonstration.
"""
import json
import secrets
import socket
import time
from pathlib import Path
import run as lab
import pressure_admission as pressure

# The probe is bounded by its own cgroup: 0.25 CPU of this host, 128 MiB memory.
# Sixteen spinners are used because the stall fraction, not the thread count,
# decides how high cpu.pressure some rises, and measured on 2026-09-21 sixteen
# held 72 to 76 while four sat near the 50 threshold.
SPINNERS = 16
CPU_QUOTA = .25
MEMORY = '128m'
PIDS_LIMIT = 64
HOLD_SECONDS = 600
LOAD_WINDOW_SECONDS = 25.0
RECOVERY_WINDOW_SECONDS = 20.0
INTERVAL_SECONDS = 5.0
SETTLE_SECONDS = 10.0
RESERVE_BYTES = 2*1024**3
EVIDENCE = 'docs/evidence/pressure-response-checks.json'
LABEL = 'io.sbarbase.lab=pressure-response-check'
# The spinners are direct children of the container entrypoint, so they are the
# processes whose parent is PID 1. The killer excludes its own pid and PID 1, so
# the container stays up and its cgroup keeps its history for the second series.
STOP_LOAD = ("self=$$; ps -o pid=,ppid= | awk -v self=$self '$1 != self && $2 == 1 {print $1}'"
             " | xargs -r kill -9")

checks = []
created = []


def check(name, condition):
    checks.append({'check': name, 'ok': bool(condition)})
    return bool(condition)


def headroom():
    line = next(line for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
    return int(line.split()[1])*1024


def host_pressure():
    """Host-wide PSI averages, read from procfs. Read-only, no container involved."""
    return {name: pressure.avg10(Path('/proc/pressure/'+name).read_text(), kind)
            for name, kind in (('cpu', 'some'), ('io', 'full'), ('memory', 'full'))}


def daemon_is_native_local():
    info = json.loads(lab.docker('info', '--format', '{{json .}}').stdout)
    return info.get('Name') == socket.gethostname() and info.get('OSType') == 'linux'


def pinned_db_image():
    pin = json.loads((lab.ROOT/'lab/images.lock.json').read_text())['db']['id']
    if lab.docker('image', 'inspect', pin, check=False).returncode:
        raise RuntimeError('The pinned probe image is not present locally; nothing was pulled')
    return pin


def probe(name, image, command):
    """Start one disposable container and return its name once it answers."""
    if lab.docker('inspect', name, check=False).returncode == 0:
        raise RuntimeError('Probe container name already exists')
    lab.docker('run', '-d', '--name', name, '--label', LABEL, '--network', 'none',
               '--memory', MEMORY, '--memory-swap', MEMORY, '--cpus', str(CPU_QUOTA),
               '--pids-limit', str(PIDS_LIMIT), '--log-opt', 'max-size=1m', '--log-opt', 'max-file=1',
               image, 'sh', '-c', command)
    created.append(name)
    deadline = time.monotonic()+10
    while time.monotonic() < deadline:
        if lab.docker('exec', name, 'cat', '/sys/fs/cgroup/cpu.pressure', check=False).returncode == 0:
            return name
        time.sleep(.25)
    raise RuntimeError('Probe container did not become measurable')


def reading(name):
    return pressure.snapshot((name,))


def lines(path):
    if not Path(path).exists():
        return 0
    return len(Path(path).read_text().splitlines())


try:
    if not check('native local Linux Docker daemon', daemon_is_native_local()):
        raise RuntimeError('This probe requires the native local Linux daemon')
    available = headroom()
    if not check('host headroom above the probe reserve', available >= RESERVE_BYTES):
        raise RuntimeError('Insufficient host headroom for the bounded probe')
    image = pinned_db_image()
    before = host_pressure()
    token = secrets.token_hex(6)
    load_name = probe('sbarbase-pressure-probe-'+token, image,
                      "i=0; while [ $i -lt %d ]; do sh -c 'while :; do :; done' & i=$((i+1)); done"
                      "; wait; exec sleep %d" % (SPINNERS, HOLD_SECONDS))
    check('probe container carries a private cgroup namespace',
          json.loads(lab.docker('inspect', '--format', '{{json .HostConfig}}', load_name).stdout).get('CgroupnsMode') == 'private')

    load_series = pressure.series(LOAD_WINDOW_SECONDS, INTERVAL_SECONDS, containers=(load_name,))
    load_summary = pressure.summarise(load_series)
    check('every load reading was complete and within 0 to 100',
          load_summary['count'] == len(load_series['readings']) and load_summary['count'] >= 2)
    during = host_pressure()
    ledger_before = lines(pressure.ledger_path())
    decision = pressure.response(load_summary)
    if load_summary['latest_refusal']:
        check('the response refused new admissions while the latest reading was over the threshold',
              decision['action'] == 'refuse_new_admissions'
              and decision['reason'] == 'pressure_'+load_summary['latest_refusal'])
    if load_summary['crossed']:
        check('every crossing was written to the durable ledger',
              decision['record']['ok'] and len(decision['record']['records']) == len(load_summary['crossings']))

    # End the load inside the same container, so the second series measures the
    # decay of the cgroup the first series measured, not a fresh quiet one. The
    # settle wait is chosen so the second series starts below the threshold: the
    # crossing it would otherwise re-observe is already recorded, and the point
    # of the second series is the admit path on real readings.
    lab.docker('exec', load_name, 'sh', '-c', STOP_LOAD)
    time.sleep(SETTLE_SECONDS)
    recovery_series = pressure.series(RECOVERY_WINDOW_SECONDS, INTERVAL_SECONDS, containers=(load_name,))
    recovery_summary = pressure.summarise(recovery_series)
    recovery = pressure.response(recovery_summary)
    after = host_pressure()
    check('the response admits once the measured readings are below every threshold',
          recovery['action'] == 'admit' and recovery['metric'] is None)

    result = {
        'scope': ('One disposable container, 0.25 CPU quota, 128 MiB ceiling, private cgroup namespace, '
                  'sixteen internal busy loops, measured through pressure_admission.series(). The retained '
                  'durable runtime is not started or touched. No per-tenant work, no SQL, no gateway '
                  'traffic, and no claim about isolation between tenants inside the shared PostgreSQL engine.'),
        'probe': {'image': image, 'cpus': CPU_QUOTA, 'memory': MEMORY, 'pids_limit': PIDS_LIMIT,
                  'spinners': SPINNERS, 'network': 'none', 'label': LABEL},
        'windows': {'load': {'seconds': LOAD_WINDOW_SECONDS, 'interval': INTERVAL_SECONDS,
                             'samples': load_summary['count'], 'elapsed_seconds': load_series['elapsed_seconds']},
                    'recovery': {'seconds': RECOVERY_WINDOW_SECONDS, 'interval': INTERVAL_SECONDS,
                                 'settle_seconds': SETTLE_SECONDS, 'samples': recovery_summary['count'],
                                 'elapsed_seconds': recovery_series['elapsed_seconds']}},
        'host_headroom_bytes': available,
        'host_pressure': {'before': before, 'during_load': during, 'after': after,
                          'caveat': ('host-wide PSI is aggregated from descendant cgroups, so the reading taken '
                                     'while the probe runs includes the probe\'s own stall. It is not an '
                                     'independent measure of load from outside the probe; the inside-the-cgroup '
                                     'series above is the probe\'s own reading.')},
        'load_series': {**load_summary, 'readings': load_series['readings']},
        'response': decision,
        'recovery_series': {**recovery_summary, 'readings': recovery_series['readings']},
        'recovery_response': recovery,
        'ledger': {'path': decision['record']['path'], 'lines_before': ledger_before,
                   'appended': len(decision['record']['records']),
                   'recording_ok': decision['record']['ok']},
        'crossing_produced': load_summary['crossed'],
        'checks': checks,
        'count': len(checks),
    }

    for phase, series in (('load', load_series), ('recovery', recovery_series)):
        print(phase + ' series, requested window %ss, interval %ss, elapsed %ss:'
              % (series['window_seconds'], series['interval_seconds'], series['elapsed_seconds']))
        for item in series['readings']:
            values = item['values'][created[0]]
            print('  t=%ss (+%ss) cpu_some10=%.2f io_full10=%.2f memory_full10=%.2f'
                  % (item['at_seconds'], item['read_seconds'],
                     values['cpu_some10'], values['io_full10'], values['memory_full10']))
    print('load crossings: ' + json.dumps(load_summary['crossings']))
    print('response while loaded: %s reason=%s over_threshold_seconds=%s'
          % (decision['action'], decision['reason'], decision['over_threshold_seconds']))
    print('response after recovery: %s reason=%s' % (recovery['action'], recovery['reason']))
    print('durable ledger: %s, %s line(s) before, %s appended by the load response, %s appended by the recovery response, recording ok=%s/%s'
          % (decision['record']['path'], ledger_before, len(decision['record']['records']),
             len(recovery['record']['records']), decision['record']['ok'], recovery['record']['ok']))
    print('host pressure before=%s during=%s after=%s' % (json.dumps(before), json.dumps(during), json.dumps(after)))
    if not load_summary['crossed']:
        print('No threshold was crossed in this run, so no crossing was recorded and the response admitted '
              'every reading. That is a measured result on this host, not a demonstration of the refusal path.')
finally:
    for name in created:
        lab.docker('rm', '-f', name, check=False)
    remaining = [name for name in created if lab.docker('inspect', name, check=False).returncode == 0]
    check('every created container was removed', not remaining)
    stray = [name for name in lab.docker('ps', '-a', '--filter', 'label='+LABEL, '--format', '{{.Names}}').stdout.split()
             if name not in created]
    check('no probe container was left behind', not stray)

failed = [row['check'] for row in checks if not row['ok']]
result['count'] = len(checks)
result['cleanup'] = {'created': created, 'failed': failed}
(lab.ROOT/EVIDENCE).write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps({key: value for key, value in result.items() if key not in ('load_series', 'recovery_series', 'response', 'recovery_response')}))
if failed:
    raise SystemExit('Pressure response check recorded failed checks: ' + '; '.join(failed))