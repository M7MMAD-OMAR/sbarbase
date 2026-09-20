"""Live API and database boundary checks, using only disposable lab data."""
import json
import secrets
import time
import urllib.error
import urllib.request
import run

results = []


def expect(name, condition):
    results.append({'check': name, 'passed': bool(condition)})
    if not condition:
        raise AssertionError(name)


def request(url, method='GET', body=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as err:
        return err.code, None


def db_request(env, role, password, database, query):
    # Password goes over stdin, never process arguments or test output.
    command = ['exec', '-i', run.DB, 'sh', '-c',
               'read -r PGPASSWORD; export PGPASSWORD; exec psql -X -v ON_ERROR_STOP=1 -h "$1" -U "$2" -d "$3" -At',
               'lab-probe', run.DB, f'{env}_{role}', database]
    return run.docker(*command, data=password+'\n'+query, check=False)


def verify():
    values = json.loads((run.PRIVATE/'lab.json').read_text())
    environments = tuple(values['environments'])
    tokens = {}
    stamp = secrets.token_hex(6)
    email = f'lab-{stamp}@example.com'
    password = secrets.token_urlsafe(24)
    for e in environments:
        auth = run.port(f'sbarbase-lab-{e}-auth', 9999)
        rest = run.port(f'sbarbase-lab-{e}-rest', 3000)
        run.sql("""
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$
 SELECT nullif(current_setting('request.jwt.claims',true)::jsonb->>'sub','')::uuid
$$;
CREATE TABLE IF NOT EXISTS public.lab_items(id uuid PRIMARY KEY DEFAULT gen_random_uuid(), owner_id uuid NOT NULL, value text NOT NULL);
ALTER TABLE public.lab_items ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS owner_only ON public.lab_items;
CREATE POLICY owner_only ON public.lab_items TO authenticated USING (owner_id=auth.uid()) WITH CHECK (owner_id=auth.uid());
GRANT SELECT, INSERT, UPDATE, DELETE ON public.lab_items TO authenticated;
GRANT ALL ON public.lab_items TO service_role;
NOTIFY pgrst, 'reload schema';
""", e)
        code, account = request(auth+'/signup', 'POST', {'email': email, 'password': password})
        expect(e+' signup', code == 200 and bool(account.get('access_token')))
        token = account['access_token']
        user = account['user']['id']
        code, login = request(auth+'/token?grant_type=password', 'POST', {'email': email, 'password': password})
        expect(e+' password login', code == 200 and login['user']['id'] == user)
        code, refreshed = request(auth+'/token?grant_type=refresh_token','POST',{'refresh_token':login['refresh_token']})
        expect(e+' refresh',code == 200 and refreshed['user']['id'] == user)
        token = refreshed['access_token']
        time.sleep(.3)
        code, _ = request(rest+'/lab_items','POST',{'owner_id':user,'value':stamp},token)
        expect(e+' owner insert',code == 201)
        code, rows = request(rest+'/lab_items?value=eq.'+stamp, token=token)
        expect(e+' owner read',code == 200 and len(rows)==1)
        code, _ = request(rest+'/lab_items','POST',{'owner_id':'00000000-0000-0000-0000-000000000000','value':stamp},token)
        expect(e+' forged row owner denied',code == 403)
        code, other = request(auth+'/signup','POST',{'email':f'other-{stamp}@example.com','password':password})
        expect(e+' second signup',code == 200)
        code, rows = request(rest+'/lab_items?value=eq.'+stamp,token=other['access_token'])
        expect(e+' second user cannot read first',code == 200 and rows==[])
        tokens[e] = (token,user)
        for role in ('auth','rest'):
            pw=values['environments'][e][role]
            expect(e+' '+role+' own database accepted',db_request(e,role,pw,e,'SELECT 1;').returncode==0)
            for target in environments:
                if target != e:
                    expect(e+' '+role+' database '+target+' denied',db_request(e,role,pw,target,'SELECT 1;').returncode!=0)
            expect(e+' '+role+' admin database denied',db_request(e,role,pw,'postgres','SELECT 1;').returncode!=0)
    expect('same email has different identity in each environment',len({u for _,u in tokens.values()})==len(environments))
    for source,(token,_) in tokens.items():
        for target in environments:
            if source != target:
                rest=run.port(f'sbarbase-lab-{target}-rest',3000)
                auth=run.port(f'sbarbase-lab-{target}-auth',9999)
                expect(source+' token rejected by '+target+' REST',request(rest+'/lab_items',token=token)[0]==401)
                expect(source+' token rejected by '+target+' Auth',request(auth+'/user',token=token)[0] in (401,403))
    print(f'{len(results)} live checks passed. No credentials recorded.')


if __name__=='__main__':
    try:
        verify()
    finally:
        (run.STATE/'verification.json').write_text(json.dumps({'checks':results},indent=2))
