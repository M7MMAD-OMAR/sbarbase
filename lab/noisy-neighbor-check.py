"""Bounded SQL microbenchmark, not a visitor or production-capacity benchmark."""
import json
import math
import re
import subprocess
import time
import uuid
import durable_runtime as runtime
import pressure_admission

rt=runtime.Runtime()
children=[]
app='sbarbase-neighbor-'+uuid.uuid4().hex

def client(environment, query):
    command=['docker','exec','-i','-e','LC_ALL=C',runtime.DB,'sh','-c',
        'IFS= read -r PGPASSWORD; export PGPASSWORD; exec psql -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -U "$1" -d "$2" -At',
        'probe',environment+'_auth',environment]
    child=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    payload=rt.values['environments'][environment]['auth']+'\n'+f"SET application_name='{app}'; SET statement_timeout='12s'; SET work_mem='8MB';\n"+query
    child.stdin.write(payload)
    child.stdin.close()
    children.append(child)
    return child


def answers(environment):
    """True when this environment's own login can reach its database.

    A state entry is not proof of a live environment: a retired one keeps its row
    and its role with NOLOGIN, so selecting the first two entries can pick a dead
    environment and fail the probe for a reason that has nothing to do with
    neighbours. The selection below probes instead of assuming.
    """
    child=client(environment,'SELECT 1;\n')
    child.wait(timeout=20)
    # psql prints a command tag for each SET before the answer, so the answer is
    # the last non empty line, not the whole output.
    lines=[line for line in child.stdout.read().splitlines() if line]
    return child.returncode==0 and lines[-1:]==['1']


def samples(environment):
    query=''.join('\\timing on\nSELECT sum(i) FROM generate_series(1,10000) i;\n\\timing off\nSELECT pg_sleep(0.1);\n' for _ in range(50))
    child=client(environment, query)
    child.wait(timeout=20)
    output=child.stdout.read()
    if child.returncode:
        raise RuntimeError('Neighbor SQL probe failed')
    values=[float(value) for value in re.findall(r'^Time: ([0-9.]+) ms',output,re.M)]
    if len(values)!=50:
        raise RuntimeError('Incomplete SQL timing sample')
    # Every probe sum must be correct, not merely successful SQL execution.
    if output.splitlines().count('50005000')!=50:
        raise RuntimeError('Unexpected SQL probe result')
    ordered=sorted(values)
    return {'samples':len(values),'median_ms':ordered[len(values)//2],
            'p95_ms':ordered[math.ceil(len(values)*.95)-1],'max_ms':max(values),'raw_ms':values}

try:
    available=int(next(line.split()[1] for line in runtime.lab.Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))
    if available<3*1024*1024:
        raise RuntimeError('Insufficient host headroom for bounded probe')
    environments=[e for e in list(rt.values['environments']) if answers(e)]
    if len(environments)<2:
        raise RuntimeError('Two answering retained environments required')
    target,neighbor=environments[:2]
    actual=runtime.inspect('container', runtime.DB)['HostConfig']
    if actual.get('NanoCpus')!=1000000000 or actual.get('Memory')!=1024**3:
        raise RuntimeError('Unexpected database resource limits; inspect before benchmarking')
    before=pressure_admission.snapshot()
    baseline=samples(neighbor)
    heavy=client(target,"DO $$ DECLARE deadline timestamptz := clock_timestamp()+interval '8 seconds'; BEGIN WHILE clock_timestamp()<deadline LOOP PERFORM sum(i::bigint*i) FROM generate_series(1,20000) i; END LOOP; END $$;\n")
    # Start neighboring samples only after the heavy query is confirmed active.
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        active=rt.sql(f"SELECT count(*) FROM pg_stat_activity WHERE application_name='{app}' AND datname='{target}' AND state='active' AND query LIKE 'DO %';").stdout.strip()
        if active=='1': break
        if heavy.poll() is not None: raise RuntimeError('Heavy query ended before observation')
        time.sleep(.05)
    else:
        raise RuntimeError('Heavy query was not observed active')
    loaded=samples(neighbor)
    overlapping=heavy.poll() is None
    during=pressure_admission.snapshot()
    heavy.wait(timeout=15)
    if heavy.returncode:
        raise RuntimeError('Bounded workload did not finish normally')
    recovered=samples(neighbor)
    result={'scope':'Single local SQL microbenchmark: 50 read-only aggregate queries per phase, 100 ms pacing, one other environment runs an eight-second CPU query. Existing DB container is capped at one CPU. No HTTP/SDK, uploads, visitor conversion, sustained disk-write load or 10/100 environment capacity conclusion.',
            'environments':{'target':target,'neighbor':neighbor},
            'heavy_overlapped_entire_loaded_sample':overlapping,
            'baseline':baseline,'with_neighbor_load':loaded,'after_load':recovered,
            'pressure_before':before,'pressure_during':during}
    (runtime.lab.ROOT/'docs/evidence/noisy-neighbor-sql.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({key:({k:v for k,v in value.items() if k!='raw_ms'} if isinstance(value,dict) and 'raw_ms' in value else value) for key,value in result.items()}))
finally:
    rt.sql(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name='{app}';",check=False)
    for child in children:
        if child.poll() is None:
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=5)
        child.stdout.close()
        child.stderr.close()
    runtime.stop()
