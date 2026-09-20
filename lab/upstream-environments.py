"""Two scoped Auth/REST environments on the pinned Supabase PostgreSQL image.
Creates only owned ephemeral resources. No replacement auth.uid fixture.
"""
import json
import secrets
import time
import urllib.request
import urllib.error
from pathlib import Path
import run as lab

def request(url, method='GET', body=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method, headers=headers)
    try:
        response = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read()
        return response.status, json.loads(raw) if raw else {}


LABEL = 'io.sbarbase.owner=upstream-environment-probe'
ENVIRONMENTS = ('env_alpha', 'env_beta')


def main():
    available = int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))
    if available < 6 * 1024 * 1024:
        raise RuntimeError('Insufficient memory headroom')
    prefix = 'sbarbase-upstream-' + secrets.token_hex(5)
    network = prefix + '-net'
    db = prefix + '-db'
    containers, env_files, checks = [], [], []
    pins = json.loads((lab.ROOT/'lab/images.lock.json').read_text())
    pins['db'] = json.loads((lab.ROOT/'lab/distro-image.lock.json').read_text())
    evidence = {'images': pins, 'scope': 'Two Auth/REST environment databases on original Supabase PostgreSQL; real Auth migrations and helpers, scoped service logins; not all Supabase components or upgrades', 'checks': checks}
    credentials = {e: {k: secrets.token_hex(32) for k in ('auth', 'rest', 'jwt')} for e in ENVIRONMENTS}

    def check(name, passed):
        checks.append({'check': name, 'passed': bool(passed)})
        if not passed:
            raise RuntimeError(name)

    def sql(query, database='postgres', check=True):
        return lab.docker('exec', '-i', db, 'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-U', 'supabase_admin', '-d', database, '-At', data=query, check=check)

    def launch(name, image, env, memory, cpus, command=()):
        path = lab.PRIVATE/(name+'.env')
        lab.secure_file(path, ''.join(f'{k}={v}\n' for k,v in env.items()))
        env_files.append(path)
        lab.docker('run', '-d', '--name', name, '--label', LABEL, '--network', network,
                   '--memory', memory, '--memory-swap', memory, '--cpus', str(cpus), '--pids-limit', '128',
                   '--log-opt', 'max-size=5m', '--log-opt', 'max-file=2', '--env-file', str(path), image, *command)
        containers.append(name)

    def endpoint(name, port):
        info = json.loads(lab.docker('inspect', name).stdout)[0]
        return f"http://{info['NetworkSettings']['Networks'][network]['IPAddress']}:{port}"

    def ready(name, url):
        for _ in range(60):
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    if response.status == 200:
                        return
            except Exception:
                pass
            if lab.docker('inspect', '--format', '{{.State.Running}}', name).stdout.strip() != 'true':
                raise RuntimeError(f'{name} exited during initialization')
            time.sleep(.5)
        raise RuntimeError('Upstream service readiness timed out')

    def connect(e, role, database, query='SELECT 1;'):
        # Credentials go to stdin, not process arguments or evidence.
        return lab.docker('exec', '-i', db, 'sh', '-c',
            'read -r PGPASSWORD; export PGPASSWORD; exec psql -X -v ON_ERROR_STOP=1 -h "$1" -U "$2" -d "$3" -At',
            'probe', db, f'{e}_{role}', database, data=credentials[e][role]+'\n'+query, check=False)

    lab.docker('network', 'create', '--internal', '--label', LABEL, network)
    try:
        launch(db, pins['db']['id'], {'POSTGRES_PASSWORD': secrets.token_hex(32), 'POSTGRES_HOST': '/var/run/postgresql', 'POSTGRES_DB': 'postgres'},
               '1024m', 1, ('postgres', '-c', 'config_file=/etc/postgresql/postgresql.conf', '-c', 'log_statement=none'))
        for _ in range(120):
            probe = sql("SELECT to_regrole('supabase_privileged_role') IS NOT NULL;", check=False)
            if probe.returncode == 0 and probe.stdout.strip() == 't' and lab.docker('exec', db, 'pg_isready', '-h', '127.0.0.1', check=False).returncode == 0:
                break
            time.sleep(.5)
        else:
            raise RuntimeError('Upstream database readiness timed out')
        check('upstream database initialized', True)
        hba = ['local all supabase_admin trust']
        for e in ENVIRONMENTS:
            lab.provision_environment(e, credentials[e], executor=sql)
            sql('CREATE SCHEMA IF NOT EXISTS extensions; CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions; CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA extensions; GRANT USAGE ON SCHEMA extensions TO anon, authenticated, service_role;', e)
            for role in ('auth', 'rest'):
                hba.append(f'host {e} {e}_{role} 0.0.0.0/0 scram-sha-256')
        hba += ['host all all 0.0.0.0/0 reject', 'host all all ::/0 reject']
        lab.docker('exec', '-i', db, 'sh', '-c', 'cat > /etc/postgresql/pg_hba.conf', data='\n'.join(hba)+'\n')
        sql('SELECT pg_reload_conf();')
        endpoints = {}
        for e in ENVIRONMENTS:
            v = credentials[e]
            auth, rest = prefix+'-'+e+'-auth', prefix+'-'+e+'-rest'
            launch(auth, pins['auth']['id'], lab.auth_configuration(e, v, db), '256m', .25)
            launch(rest, pins['rest']['id'], lab.rest_configuration(e, v, db), '256m', .25)
            endpoints[e] = {'auth': endpoint(auth, 9999), 'rest': endpoint(rest, 3000)}
            ready(auth, endpoints[e]['auth']+'/health');ready(rest, endpoints[e]['rest']+'/')
            check(e+' original Auth migrations create uid and jwt helpers', sql("SELECT to_regprocedure('auth.uid()') IS NOT NULL AND to_regprocedure('auth.jwt()') IS NOT NULL;", e).stdout.strip() == 't')
            check(e+' Auth tables owned by scoped login', sql(f"SELECT bool_and(pg_get_userbyid(c.relowner)='{e}_auth') FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='auth' AND c.relkind IN ('r','S');", e).stdout.strip() == 't')
            check(e+' REST role cannot assume global admin', connect(e, 'rest', e, 'SET ROLE supabase_admin;').returncode != 0)
            for role in ('auth', 'rest'):
                check(e+' '+role+' own database accepted', connect(e, role, e).returncode == 0)
                for other in (*ENVIRONMENTS, 'postgres'):
                    if other != e:
                        check(e+' '+role+' denied database '+other, connect(e, role, other).returncode != 0)
            sql('CREATE TABLE public.probe_items(id uuid DEFAULT gen_random_uuid() PRIMARY KEY, owner_id uuid NOT NULL, value text); ALTER TABLE public.probe_items ENABLE ROW LEVEL SECURITY; CREATE POLICY owner_only ON public.probe_items TO authenticated USING (owner_id=auth.uid()) WITH CHECK (owner_id=auth.uid()); GRANT SELECT,INSERT ON public.probe_items TO authenticated; NOTIFY pgrst, \'reload schema\';', e)
        accounts = {}
        email = 'upstream-'+secrets.token_hex(8)+'@example.com'
        password = secrets.token_urlsafe(24)
        for e in ENVIRONMENTS:
            auth,rest = endpoints[e]['auth'],endpoints[e]['rest']
            code,account = request(auth+'/signup','POST',{'email':email,'password':password})
            check(e+' signup', code == 200 and bool(account.get('access_token')))
            accounts[e] = account
            token,user = account['access_token'],account['user']['id']
            code,result = request(rest+'/probe_items','POST',{'owner_id':user,'value':'survives-restart'},token)
            if code != 201:
                evidence['rls_insert_diagnostic'] = {'status':code,'error_code':result.get('code'),'message':result.get('message'), 'helper_definition':sql("SELECT pg_get_functiondef('auth.uid()'::regprocedure);",e).stdout, 'privileges':sql("SELECT has_function_privilege('authenticated','auth.uid()','EXECUTE'),has_schema_privilege('authenticated','auth','USAGE');",e).stdout}
            check(e+' RLS insert uses original uid helper', code == 201)
            code,rows = request(rest+'/probe_items',token=token)
            check(e+' RLS owner reads own row', code == 200 and len(rows) == 1)
            code,_ = request(rest+'/probe_items','POST',{'owner_id':'00000000-0000-0000-0000-000000000000','value':'denied'},token)
            check(e+' forged owner denied', code == 403)
            code,other = request(auth+'/signup','POST',{'email':'other-'+email,'password':password})
            check(e+' second user signup', code == 200)
            code,rows = request(rest+'/probe_items',token=other['access_token'])
            check(e+' other user cannot read row', code == 200 and rows == [])
            for target in ENVIRONMENTS:
                if target != e:
                    check(e+' token denied by '+target+' Auth', request(endpoints[target]['auth']+'/user',token=token)[0] in (401,403))
                    check(e+' token denied by '+target+' REST', request(endpoints[target]['rest']+'/probe_items',token=token)[0] == 401)
        check('same email remains separate identity per environment', len({a['user']['id'] for a in accounts.values()}) == 2)
        # Retry the bootstrap after real Auth data exists; retain the database and identities.
        for e in ENVIRONMENTS:
            oid = sql(f"SELECT oid FROM pg_database WHERE datname='{e}';").stdout
            lab.provision_environment(e, credentials[e], executor=sql)
            check(e+' bootstrap retry keeps database identity', sql(f"SELECT oid FROM pg_database WHERE datname='{e}';").stdout == oid)
            auth = prefix+'-'+e+'-auth'
            lab.docker('restart', auth);ready(auth,endpoints[e]['auth']+'/health')
            code,login = request(endpoints[e]['auth']+'/token?grant_type=password','POST',{'email':email,'password':password})
            check(e+' migration restart preserves Auth account', code == 200 and login['user']['id'] == accounts[e]['user']['id'])
        print(f'{len(checks)} upstream environment checks passed without a replacement Auth helper.')
    finally:
        for name in reversed(containers):
            owner = lab.docker('inspect', '--format', '{{index .Config.Labels "io.sbarbase.owner"}}', name, check=False)
            if owner.returncode == 0 and owner.stdout.strip() == 'upstream-environment-probe':
                lab.docker('rm', '-f', name)
        owner = lab.docker('network', 'inspect', '--format', '{{index .Labels "io.sbarbase.owner"}}', network, check=False)
        if owner.returncode == 0 and owner.stdout.strip() == 'upstream-environment-probe':
            lab.docker('network', 'rm', network)
        for path in env_files:
            path.unlink(missing_ok=True)
        (lab.STATE/'upstream-environments.json').write_text(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
