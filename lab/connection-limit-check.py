"""Saturate one owned service login and verify a neighboring login still works."""
import json
import os
import subprocess
import time
import uuid
import durable_runtime as runtime
import connection_budget as budget

checks=[]
children=[]
app='sbarbase-connection-check-'+uuid.uuid4().hex

def check(name, condition):
    if not condition:
        raise RuntimeError(name)
    checks.append(name)


def connect(environment, role, query):
    # Password enters only through the private stdin pipe, never argv or logs.
    command=['docker','exec','-i',runtime.DB,'sh','-c',
             'IFS= read -r PGPASSWORD; export PGPASSWORD; exec psql -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -U "$1" -d "$2" -At',
             'probe',environment+'_'+role,environment]
    child=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=dict(os.environ))
    password=rt.values['environments'][environment][role]
    child.stdin.write(password+'\n'+f"SET application_name='{app}';\n"+query+'\n')
    child.stdin.close()
    children.append(child)
    return child

rt=runtime.Runtime()
try:
    environments=list(rt.values['environments'])
    check('at least two owned environments available',len(environments)>=2)
    target,neighbor=environments[:2]
    role=target+'_auth'
    result=rt.sql(f"SELECT rolconnlimit,rolsuper FROM pg_roles WHERE rolname='{role}';").stdout.strip()
    check('target login has finite non-superuser limit',result==f'{budget.SERVICE_LIMIT}|f')
    check('environment database has finite limit',rt.sql(f"SELECT datconnlimit FROM pg_database WHERE datname='{target}';").stdout.strip()==str(budget.ENVIRONMENT_LIMIT))
    held=0
    refused=False
    for _ in range(budget.SERVICE_LIMIT+1):
        child=connect(target,'auth','SELECT pg_sleep(60);')
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if child.poll() is not None:
                refused=child.returncode!=0 and 'too many connections for role' in child.stderr.read()
                break
            active=int(rt.sql(f"SELECT count(*) FROM pg_stat_activity WHERE application_name='{app}' AND usename='{role}';").stdout.strip())
            if active>held:
                held=active
                break
            time.sleep(.05)
        else:
            raise RuntimeError('Connection attempt did not settle')
        if child.poll() is not None:
            break
    check('probe held real concurrent service connections',held>0)
    check('extra login refused by PostgreSQL role limit',refused)
    other=connect(neighbor,'auth','SELECT 1;')
    check('neighbor login remains available while target saturated',other.wait(timeout=10)==0 and other.stdout.read().strip().endswith('1'))
    check('operator connection remains available',rt.sql('SELECT 1;').stdout.strip()=='1')
finally:
    rt.sql(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name='{app}';",check=False)
    for child in children:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.terminate()
            child.wait(timeout=5)
        child.stdout.close()
        child.stderr.close()
    runtime.stop()
result={'scope':'One real Auth login saturation, neighboring SQL login and operator availability; not a full noisy-neighbor load or query-resource isolation test','count':len(checks),'checks':checks}
(runtime.lab.ROOT/'docs/evidence/connection-limit-checks.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
